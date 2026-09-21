from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import threading
import time

import mongomock
import pytest
from pymongo.errors import AutoReconnect, DuplicateKeyError

from app.migrations.lock import LOCK_COLLECTION, LOCK_ID, MigrationLease
from app.migrations.models import (
    ChecksumMismatchError,
    DryRunWriteError,
    Migration,
    MigrationDefinitionError,
    MigrationLockLost,
    MigrationLockUnavailable,
    MigrationPlan,
    MigrationStateError,
    ProductionGuardError,
    checksum_file,
    safe_error_message,
)
from app.migrations.registry import get_migrations
from app.migrations.runner import (
    MIGRATION_COLLECTION,
    MigrationRunner,
    ReadOnlyDatabase,
    VERSION_INDEX_NAME,
)
from scripts.migrate import production_approval_for_target
import scripts.migrate as migrate_script


BASELINE_ONLY = (get_migrations()[0],)


def isolated_database(name: str = "default"):
    database_name = f"ordo_test_migrations_{name}"
    assert database_name != "ordo_staging"
    return mongomock.MongoClient(tz_aware=True)[database_name]


def runner(
    database,
    *,
    migrations=None,
    app_env="test",
    production_approval=None,
    lease_seconds=3,
):
    return MigrationRunner(
        database,
        app_env=app_env,
        application_version="test-commit",
        migrations=migrations,
        production_approval=production_approval,
        lease_seconds=lease_seconds,
    )


def test_empty_isolated_database_preview_is_read_only():
    database = isolated_database("empty")

    report = runner(database).run(dry_run=True).as_dict()

    assert report["dryRun"] is True
    assert report["appEnv"] == "test"
    assert report["databaseName"] == database.name
    assert report["plannedMigrations"][0]["version"] == 1
    assert report["plannedMigrations"][0]["expectedChanges"]["totalBusinessDocuments"] == 0
    assert database.list_collection_names() == []


def test_baseline_application_records_bson_dates_and_only_allowed_index():
    database = isolated_database("baseline")

    report = runner(database, migrations=BASELINE_ONLY).run().as_dict()

    record = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert report["executedMigrations"][0]["version"] == 1
    assert record["status"] == "completed"
    assert isinstance(record["startedAt"], datetime)
    assert isinstance(record["completedAt"], datetime)
    assert record["startedAt"].utcoffset() == timedelta(0)
    assert record["completedAt"].utcoffset() == timedelta(0)
    assert record["applicationVersion"] == "test-commit"
    assert record["resultSummary"]["businessDocumentsModified"] == 0
    assert record["resultSummary"]["baselineInventory"]["totalBusinessDocuments"] == 0
    assert record["resultSummary"]["baselineInventory"]["missingExpectedCollections"]
    indexes = database[MIGRATION_COLLECTION].index_information()
    assert set(indexes) == {"_id_", VERSION_INDEX_NAME}
    assert indexes[VERSION_INDEX_NAME]["unique"] is True
    assert indexes[VERSION_INDEX_NAME]["key"] == [("version", 1)]
    with pytest.raises(DuplicateKeyError):
        database[MIGRATION_COLLECTION].insert_one({"version": 1})


def test_lock_and_migration_metadata_use_majority_concerns():
    database = isolated_database("majority_concerns")
    migration_runner = runner(database)
    lease = MigrationLease(database, application_version="test")

    assert migration_runner._metadata.write_concern.document == {"w": "majority"}
    assert migration_runner._metadata.read_concern.document == {"level": "majority"}
    assert lease.collection.write_concern.document == {"w": "majority"}
    assert lease.collection.read_concern.document == {"level": "majority"}


def test_second_run_skips_completed_migration():
    database = isolated_database("repeat")
    first = runner(database, migrations=BASELINE_ONLY).run().as_dict()
    second = runner(database, migrations=BASELINE_ONLY).run().as_dict()

    assert len(first["executedMigrations"]) == 1
    assert second["executedMigrations"] == []
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["attempts"] == 1


def test_migration_result_cannot_override_framework_completion_fields():
    database = isolated_database("reserved_summary")
    migration = Migration(
        version=1,
        name="reserved_summary",
        checksum="8" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=lambda _database, _context: {
            "message": "false message",
            "attempt": 999,
            "durationMs": -1,
            "version": 999,
            "name": "false_name",
        },
    )

    report = runner(database, migrations=(migration,)).run().as_dict()
    summary = database[MIGRATION_COLLECTION].find_one({"version": 1})["resultSummary"]

    assert summary["message"] == "Migration completed"
    assert summary["attempt"] == 1
    assert summary["durationMs"] >= 0
    assert report["executedMigrations"][0]["version"] == 1
    assert report["executedMigrations"][0]["name"] == "reserved_summary"


def test_dry_run_after_application_does_not_write():
    database = isolated_database("dry_applied")
    runner(database, migrations=BASELINE_ONLY).run()
    before = deepcopy(database[MIGRATION_COLLECTION].find_one({"version": 1}))
    lock_before = deepcopy(database[LOCK_COLLECTION].find_one({"_id": LOCK_ID}))

    report = runner(database, migrations=BASELINE_ONLY).run(dry_run=True).as_dict()

    assert report["plannedMigrations"] == []
    assert report["appliedMigrations"][0]["version"] == 1
    assert database[MIGRATION_COLLECTION].find_one({"version": 1}) == before
    assert database[LOCK_COLLECTION].find_one({"_id": LOCK_ID}) == lock_before


def test_checksum_mismatch_is_rejected_without_reapplying():
    database = isolated_database("checksum")
    runner(database, migrations=BASELINE_ONLY).run()
    original = get_migrations()[0]
    changed = Migration(
        version=original.version,
        name=original.name,
        checksum="f" * 64,
        inspect=original.inspect,
        apply=original.apply,
    )

    with pytest.raises(ChecksumMismatchError):
        runner(database, migrations=(changed,)).run(dry_run=True)
    lock_before = deepcopy(database[LOCK_COLLECTION].find_one({"_id": LOCK_ID}))
    with pytest.raises(ChecksumMismatchError):
        runner(database, migrations=(changed,)).run()

    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["attempts"] == 1
    assert database[LOCK_COLLECTION].find_one({"_id": LOCK_ID}) == lock_before


def test_failed_migration_is_recorded_and_can_resume():
    database = isolated_database("recovery")
    calls = {"count": 0}

    def apply(_database, context):
        context.checkpoint()
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return {"businessDocumentsModified": 0}

    migration = Migration(
        version=1,
        name="retryable_test",
        checksum="a" * 64,
        inspect=lambda _db: MigrationPlan(preconditions=("test",)),
        apply=apply,
    )

    with pytest.raises(RuntimeError, match="temporary failure"):
        runner(database, migrations=(migration,)).run()

    failed = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert failed["status"] == "failed"
    assert failed["resultSummary"]["errorType"] == "RuntimeError"

    result = runner(database, migrations=(migration,)).run().as_dict()
    completed = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert result["executedMigrations"][0]["attempt"] == 2
    assert completed["status"] == "completed"
    assert completed["attempts"] == 2


def test_two_parallel_runners_cannot_execute_same_migration():
    database = isolated_database("parallel")
    entered = threading.Event()
    release = threading.Event()
    first_error = []

    def apply(_database, context):
        context.checkpoint()
        entered.set()
        assert release.wait(timeout=5)
        return {"businessDocumentsModified": 0}

    migration = Migration(
        version=1,
        name="parallel_test",
        checksum="b" * 64,
        inspect=lambda _db: MigrationPlan(),
        apply=apply,
    )

    def first_runner():
        try:
            runner(database, migrations=(migration,), lease_seconds=2).run()
        except Exception as exc:  # pragma: no cover - asserted through the shared list
            first_error.append(exc)

    thread = threading.Thread(target=first_runner)
    thread.start()
    assert entered.wait(timeout=5)
    try:
        with pytest.raises(MigrationLockUnavailable):
            runner(database, migrations=(migration,), lease_seconds=2).run()
    finally:
        release.set()
        thread.join(timeout=5)

    assert not first_error
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["attempts"] == 1


def test_heartbeat_keeps_a_long_running_migration_lease_alive():
    database = isolated_database("heartbeat")

    def apply(_database, context):
        time.sleep(0.35)
        context.checkpoint()
        return {"businessDocumentsModified": 0}

    migration = Migration(
        version=1,
        name="heartbeat_test",
        checksum="9" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=apply,
    )

    runner(database, migrations=(migration,), lease_seconds=0.15).run()

    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["status"] == "completed"


def test_expired_lease_can_be_taken_over_without_old_owner_releasing_new_owner():
    database = isolated_database("lease_takeover")
    database[LOCK_COLLECTION].insert_one(
        {
            "_id": LOCK_ID,
            "ownerId": "crashed-runner",
            "expiresAt": datetime.now(timezone.utc) - timedelta(seconds=5),
        }
    )
    lease = MigrationLease(
        database,
        application_version="test",
        lease_seconds=5,
        owner_id="new-runner",
    )

    lease.acquire(start_heartbeat=False)
    current = database[LOCK_COLLECTION].find_one({"_id": LOCK_ID})
    assert current["ownerId"] == "new-runner"
    assert current["generation"] == 1

    old = MigrationLease(
        database,
        application_version="test",
        lease_seconds=5,
        owner_id="crashed-runner",
    )
    old.release()
    assert database[LOCK_COLLECTION].find_one({"_id": LOCK_ID})["ownerId"] == "new-runner"
    lease.release()


def test_expired_lease_cannot_be_revived_by_a_late_heartbeat():
    database = isolated_database("late_heartbeat")
    lease = MigrationLease(
        database,
        application_version="test",
        lease_seconds=0.05,
        owner_id="late-runner",
    )
    lease.acquire(start_heartbeat=False)
    time.sleep(0.08)

    with pytest.raises(MigrationLockLost, match="Lease ownership changed"):
        lease.renew()

    expired = database[LOCK_COLLECTION].find_one({"_id": LOCK_ID})
    assert expired["ownerId"] == "late-runner"
    assert expired["expiresAt"] <= datetime.now(timezone.utc)
    lease.release()


def test_production_guard_blocks_writes_but_allows_read_only_preview():
    database = isolated_database("production_guard")
    production_runner = runner(database, app_env="production", migrations=BASELINE_ONLY)

    preview = production_runner.run(dry_run=True).as_dict()
    assert preview["dryRun"] is True
    assert database.list_collection_names() == []

    with pytest.raises(ProductionGuardError):
        production_runner.run()
    assert database.list_collection_names() == []

    wrong_target = runner(
        database,
        app_env="production",
        production_approval="production:another_database",
        migrations=BASELINE_ONLY,
    )
    with pytest.raises(ProductionGuardError):
        wrong_target.run()

    approved = runner(
        database,
        app_env=" Production ",
        production_approval=f"production:{database.name}",
        migrations=BASELINE_ONLY,
    )
    approved.run()
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["status"] == "completed"


def test_baseline_does_not_change_business_documents_or_business_indexes():
    database = isolated_database("business_unchanged")
    database.companies.insert_one({"_id": "mongo-1", "id": "c1", "name": "Example"})
    database.orders.insert_one({"_id": "mongo-2", "id": "o1", "companyId": "c1"})
    database.orders.create_index("id", unique=True, name="existing_order_id")
    documents_before = {
        name: list(database[name].find({}).sort("_id", 1))
        for name in ("companies", "orders")
    }
    indexes_before = {
        name: deepcopy(database[name].index_information())
        for name in ("companies", "orders")
    }

    runner(database, migrations=BASELINE_ONLY).run()

    for name in ("companies", "orders"):
        assert list(database[name].find({}).sort("_id", 1)) == documents_before[name]
        assert database[name].index_information() == indexes_before[name]


def test_unknown_database_migration_version_blocks_downgraded_code():
    database = isolated_database("unknown_version")
    database[MIGRATION_COLLECTION].insert_one(
        {
            "version": 999,
            "name": "future_migration",
            "checksum": "c" * 64,
            "status": "completed",
        }
    )

    with pytest.raises(MigrationStateError, match="unknown to this application"):
        runner(database).run(dry_run=True)


def test_boolean_database_migration_version_is_rejected():
    database = isolated_database("boolean_database_version")
    database[MIGRATION_COLLECTION].insert_one(
        {
            "version": True,
            "name": "baseline_current_schema",
            "checksum": get_migrations()[0].checksum,
            "status": "completed",
        }
    )

    with pytest.raises(MigrationStateError, match="integer version"):
        runner(database).run(dry_run=True)


def test_dry_run_blocks_write_attempts_from_inspection_code():
    database = isolated_database("dry_run_write_guard")

    def unsafe_inspect(read_only_database):
        read_only_database["products"].insert_one({"id": "should-not-exist"})
        return MigrationPlan()

    migration = Migration(
        version=1,
        name="unsafe_preview",
        checksum="d" * 64,
        inspect=unsafe_inspect,
        apply=lambda _db, _context: {},
    )

    with pytest.raises(DryRunWriteError, match="insert_one"):
        runner(database, migrations=(migration,)).run(dry_run=True)
    assert database.list_collection_names() == []


@pytest.mark.parametrize(
    ("scope", "method", "arguments"),
    [
        ("collection", "insert_one", ({"id": "x"},)),
        ("collection", "insert_many", ([{"id": "x"}],)),
        ("collection", "update_one", ({}, {"$set": {"x": 1}})),
        ("collection", "update_many", ({}, {"$set": {"x": 1}})),
        ("collection", "replace_one", ({}, {"id": "x"})),
        ("collection", "delete_one", ({},)),
        ("collection", "delete_many", ({},)),
        ("collection", "bulk_write", ([],)),
        ("collection", "find_one_and_update", ({}, {"$set": {"x": 1}})),
        ("collection", "find_one_and_replace", ({}, {"id": "x"})),
        ("collection", "find_one_and_delete", ({},)),
        ("collection", "create_index", ("id",)),
        ("collection", "create_indexes", ([],)),
        ("collection", "drop_index", ("id_1",)),
        ("collection", "drop_indexes", ()),
        ("collection", "rename", ("renamed",)),
        ("collection", "drop", ()),
        ("database", "create_collection", ("created",)),
        ("database", "drop_collection", ("products",)),
        ("database", "command", ({"create": "created"},)),
    ],
)
def test_dry_run_proxy_blocks_known_mongodb_write_surfaces(scope, method, arguments):
    database = isolated_database(f"blocked_{scope}_{method}")
    read_only = ReadOnlyDatabase(database)
    target = read_only["products"] if scope == "collection" else read_only

    with pytest.raises(DryRunWriteError, match=method):
        getattr(target, method)(*arguments)
    assert database.list_collection_names() == []


@pytest.mark.parametrize(
    "pipeline",
    [
        [{"$out": "copied"}],
        [{"$merge": {"into": "copied"}}],
        [{"$lookup": {"from": "other", "pipeline": [{"$out": "copied"}], "as": "x"}}],
    ],
)
def test_dry_run_proxy_blocks_write_aggregation_stages_at_any_depth(pipeline):
    database = isolated_database("blocked_aggregation")

    with pytest.raises(DryRunWriteError, match=r"\$out or \$merge"):
        list(ReadOnlyDatabase(database)["products"].aggregate(pipeline))
    assert database.list_collection_names() == []


def test_dry_run_proxy_allows_reads_without_creating_database_structures():
    database = isolated_database("allowed_reads")
    read_only = ReadOnlyDatabase(database)

    assert read_only.list_collection_names() == []
    assert read_only["products"].find_one({}) is None
    assert list(read_only["products"].find({})) == []
    assert list(read_only["products"].aggregate([{"$match": {}}])) == []
    assert database.list_collection_names() == []


@pytest.mark.parametrize(
    ("target", "attribute"),
    [
        ("database", "_database"),
        ("database", "_ReadOnlyDatabase__database"),
        ("collection", "_collection"),
        ("collection", "_ReadOnlyCollection__collection"),
    ],
)
def test_dry_run_proxy_does_not_expose_normal_raw_handle_attributes(target, attribute):
    database = isolated_database(f"raw_handle_{target}_{attribute}")
    read_only = ReadOnlyDatabase(database)
    subject = read_only if target == "database" else read_only["products"]

    with pytest.raises(DryRunWriteError, match="writable"):
        getattr(subject, attribute)


def test_unknown_environment_is_not_treated_as_non_production():
    database = isolated_database("unknown_environment")

    with pytest.raises(MigrationDefinitionError, match="Unknown APP_ENV"):
        runner(database, app_env="prduction")


@pytest.mark.parametrize("app_env", ["", "   ", "prduction", "PRODUCTIONAL"])
def test_cli_rejects_missing_or_unknown_environment_before_mongo_connection(
    monkeypatch,
    app_env,
):
    connected = {"value": False}

    def forbidden_client(*_args, **_kwargs):
        connected["value"] = True
        raise AssertionError("MongoClient must not be constructed")

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DB_NAME", "do_not_connect")
    monkeypatch.setenv("MONGO_URL", "mongodb://credentials-must-not-be-used")
    monkeypatch.setattr(migrate_script, "MongoClient", forbidden_client)
    monkeypatch.setattr("sys.argv", ["migrate.py"])

    assert migrate_script.main() == 2
    assert connected["value"] is False


def test_error_messages_redact_common_secret_forms():
    error = RuntimeError(
        "mongodb+srv://user:password@example.invalid/db "
        "token=top-secret password:also-secret"
    )

    message = safe_error_message(error)

    assert "user:password" not in message
    assert "top-secret" not in message
    assert "also-secret" not in message
    assert "mongodb+srv://<redacted>@example.invalid/db" in message
    assert "password=<redacted>" in message


def test_apply_rechecks_preconditions_under_lease_before_migration_write():
    database = isolated_database("precondition")
    applied = {"value": False}

    def inspect(_database):
        raise MigrationStateError("precondition failed")

    def apply(_database, _context):
        applied["value"] = True
        return {}

    migration = Migration(
        version=1,
        name="precondition_test",
        checksum="e" * 64,
        inspect=inspect,
        apply=apply,
    )

    with pytest.raises(MigrationStateError, match="precondition failed"):
        runner(database, migrations=(migration,)).run()
    assert applied["value"] is False
    assert database[MIGRATION_COLLECTION].count_documents({}) == 0


def test_cli_production_approval_is_flagged_and_bound_to_exact_target(monkeypatch):
    monkeypatch.setenv("MIGRATION_PRODUCTION_APPROVAL", "production:ordo_prod")

    assert production_approval_for_target("production", "ordo_prod", flag=False) is None
    assert production_approval_for_target("production", "another_database", flag=True) is None
    assert (
        production_approval_for_target(" Production ", "ordo_prod", flag=True)
        == "production:ordo_prod"
    )
    assert production_approval_for_target("staging", "ordo_staging", flag=False) is None


def test_checksum_is_independent_of_path_and_line_endings(tmp_path):
    unix = tmp_path / "unix.py"
    windows = tmp_path / "windows.py"
    classic_mac = tmp_path / "classic.py"
    unix.write_bytes(b"first\nsecond\n")
    windows.write_bytes(b"first\r\nsecond\r\n")
    classic_mac.write_bytes(b"first\rsecond\r")

    assert checksum_file(unix) == checksum_file(windows) == checksum_file(classic_mac)


def make_migration(version=1, name="valid_name", checksum="a" * 64):
    return Migration(
        version=version,
        name=name,
        checksum=checksum,
        inspect=lambda _database: MigrationPlan(),
        apply=lambda _database, _context: {},
    )


@pytest.mark.parametrize("version", [0, -1, True, 1.0, "1"])
def test_registry_rejects_invalid_version_types_and_values(version):
    database = isolated_database(f"invalid_version_{version!s}")

    with pytest.raises(MigrationDefinitionError, match="positive integers"):
        runner(database, migrations=(make_migration(version=version),))


def test_registry_rejects_duplicate_versions_names_and_wrong_order():
    database = isolated_database("invalid_registry")

    with pytest.raises(MigrationDefinitionError, match="Duplicate migration version"):
        runner(
            database,
            migrations=(make_migration(1, "first"), make_migration(1, "second")),
        )
    with pytest.raises(MigrationDefinitionError, match="Duplicate migration name"):
        runner(
            database,
            migrations=(make_migration(1, "same"), make_migration(2, "same")),
        )
    with pytest.raises(MigrationDefinitionError, match="ascending order"):
        runner(
            database,
            migrations=(make_migration(2, "second"), make_migration(1, "first")),
        )


def test_running_record_left_by_crashed_process_is_retried():
    database = isolated_database("crashed_process")
    migration = make_migration()
    database[MIGRATION_COLLECTION].insert_one(
        {
            "version": migration.version,
            "name": migration.name,
            "checksum": migration.checksum,
            "status": "running",
            "attempts": 1,
            "lockOwnerId": "dead-runner",
            "leaseGeneration": 1,
        }
    )

    report = runner(database, migrations=(migration,)).run().as_dict()

    record = database[MIGRATION_COLLECTION].find_one({"version": migration.version})
    assert report["executedMigrations"][0]["attempt"] == 2
    assert record["status"] == "completed"
    assert record["attempts"] == 2


def test_business_write_before_failure_is_idempotently_recovered():
    database = isolated_database("partial_business_write")
    calls = {"count": 0}

    def apply(target_database, _context):
        target_database["business"].update_one(
            {"id": "once"},
            {"$setOnInsert": {"id": "once", "value": 1}},
            upsert=True,
        )
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("process stopped after business write")
        return {"businessDocumentsModified": 0}

    migration = Migration(
        version=1,
        name="partial_write",
        checksum="b" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=apply,
    )

    with pytest.raises(RuntimeError, match="after business write"):
        runner(database, migrations=(migration,)).run()
    assert database.business.count_documents({"id": "once"}) == 1
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["status"] == "failed"

    runner(database, migrations=(migration,)).run()
    assert database.business.count_documents({"id": "once"}) == 1
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["status"] == "completed"


def test_completion_status_write_failure_never_reports_completed_and_can_retry(monkeypatch):
    database = isolated_database("completion_write_failure")
    migration = make_migration()
    migration_runner = runner(database, migrations=(migration,))
    collection = migration_runner._metadata
    original_update_one = collection.update_one
    fail_once = {"value": True}

    def injected_update(filter_document, update_document, *args, **kwargs):
        new_status = update_document.get("$set", {}).get("status")
        if new_status == "completed" and fail_once["value"]:
            fail_once["value"] = False
            raise AutoReconnect("simulated status write outage")
        return original_update_one(filter_document, update_document, *args, **kwargs)

    monkeypatch.setattr(collection, "update_one", injected_update)
    with pytest.raises(AutoReconnect, match="status write outage"):
        migration_runner.run()

    assert collection.find_one({"version": 1})["status"] == "failed"
    runner(database, migrations=(migration,)).run()
    assert collection.find_one({"version": 1})["status"] == "completed"
    assert collection.find_one({"version": 1})["attempts"] == 2


def test_failure_status_write_failure_leaves_recoverable_running_record(monkeypatch):
    database = isolated_database("all_status_writes_fail")

    def apply(_database, _context):
        raise RuntimeError("migration failed")

    migration = Migration(
        version=1,
        name="status_failure",
        checksum="c" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=apply,
    )
    migration_runner = runner(database, migrations=(migration,))
    collection = migration_runner._metadata
    original_update_one = collection.update_one

    def injected_update(filter_document, update_document, *args, **kwargs):
        if update_document.get("$set", {}).get("status") == "failed":
            raise AutoReconnect("simulated failure status outage")
        return original_update_one(filter_document, update_document, *args, **kwargs)

    monkeypatch.setattr(collection, "update_one", injected_update)
    with pytest.raises(AutoReconnect, match="failure status outage"):
        migration_runner.run()

    record = collection.find_one({"version": 1})
    assert record["status"] == "running"
    assert record["attempts"] == 1

    monkeypatch.setattr(collection, "update_one", original_update_one)
    recovered = make_migration(version=1, name="status_failure", checksum="c" * 64)
    runner(database, migrations=(recovered,)).run()
    assert collection.find_one({"version": 1})["status"] == "completed"
    assert collection.find_one({"version": 1})["attempts"] == 2


def test_stale_generation_cannot_claim_or_overwrite_newer_migration_attempt():
    database = isolated_database("stale_generation")
    migration = make_migration()
    current_lease = MigrationLease(
        database,
        application_version="new",
        lease_seconds=30,
        owner_id="new-runner",
    )
    current_lease.acquire(start_heartbeat=False)
    database[MIGRATION_COLLECTION].create_index("version", unique=True)
    database[MIGRATION_COLLECTION].insert_one(
        {
            "version": 1,
            "name": migration.name,
            "checksum": migration.checksum,
            "status": "running",
            "attempts": 1,
            "lockOwnerId": "new-runner",
            "leaseGeneration": current_lease.generation,
        }
    )
    class StaleLease:
        owner_id = "old-runner"
        generation = current_lease.generation - 1

        @staticmethod
        def renew():
            # Simulate ownership changing immediately after a successful renewal.
            return None

        @staticmethod
        def assert_owned():
            return None

    stale_lease = StaleLease()

    with pytest.raises(MigrationStateError, match="could not be claimed"):
        runner(database, migrations=(migration,))._apply_one(migration, stale_lease)

    record = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert record["lockOwnerId"] == "new-runner"
    assert record["leaseGeneration"] == current_lease.generation
    assert record["attempts"] == 1
    current_lease.release()


def test_stale_completed_race_is_skipped_without_incrementing_attempts():
    database = isolated_database("completed_race")
    migration = make_migration()
    lease = MigrationLease(
        database,
        application_version="test",
        lease_seconds=30,
        owner_id="runner",
    )
    lease.acquire(start_heartbeat=False)
    database[MIGRATION_COLLECTION].create_index("version", unique=True)
    database[MIGRATION_COLLECTION].insert_one(
        {
            "version": 1,
            "name": migration.name,
            "checksum": migration.checksum,
            "status": "completed",
            "attempts": 1,
            "completedAt": datetime.now(timezone.utc),
        }
    )

    assert runner(database, migrations=(migration,))._apply_one(migration, lease) is None
    assert database[MIGRATION_COLLECTION].find_one({"version": 1})["attempts"] == 1
    lease.release()


def test_old_failure_write_cannot_overwrite_newer_generation_status():
    database = isolated_database("failure_fence")

    def apply(target_database, _context):
        target_database[MIGRATION_COLLECTION].update_one(
            {"version": 1},
            {
                "$set": {
                    "lockOwnerId": "new-runner",
                    "leaseGeneration": 999,
                    "resultSummary": {"message": "Newer attempt owns this status"},
                }
            },
        )
        raise RuntimeError("old attempt failed")

    migration = Migration(
        version=1,
        name="failure_fence",
        checksum="e" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=apply,
    )

    with pytest.raises(RuntimeError, match="old attempt failed"):
        runner(database, migrations=(migration,)).run()

    record = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert record["status"] == "running"
    assert record["lockOwnerId"] == "new-runner"
    assert record["leaseGeneration"] == 999
    assert record["resultSummary"]["message"] == "Newer attempt owns this status"


def test_lost_lease_after_business_work_cannot_write_completed_status():
    database = isolated_database("lost_lease")

    def apply(target_database, context):
        target_database.business.update_one(
            {"id": "marker"},
            {"$setOnInsert": {"id": "marker"}},
            upsert=True,
        )
        lock = target_database[LOCK_COLLECTION].find_one({"_id": LOCK_ID})
        target_database[LOCK_COLLECTION].update_one(
            {"_id": LOCK_ID},
            {
                "$set": {"ownerId": "takeover", "expiresAt": datetime.now(timezone.utc) + timedelta(seconds=30)},
                "$inc": {"generation": 1},
            },
        )
        assert lock["ownerId"] != "takeover"
        context.checkpoint()
        return {}

    migration = Migration(
        version=1,
        name="lease_loss",
        checksum="d" * 64,
        inspect=lambda _database: MigrationPlan(),
        apply=apply,
    )

    with pytest.raises(MigrationLockLost):
        runner(database, migrations=(migration,), lease_seconds=30).run()

    record = database[MIGRATION_COLLECTION].find_one({"version": 1})
    assert record["status"] == "failed"
    assert record["status"] != "completed"
    assert database.business.count_documents({"id": "marker"}) == 1
