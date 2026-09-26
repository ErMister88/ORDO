from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import mongomock
import pytest
from pymongo.errors import AutoReconnect, DuplicateKeyError, OperationFailure

from app.migrations.models import MigrationStateError
from app.migrations.runner import MIGRATION_COLLECTION, MigrationRunner
from app.migrations.versions import v0002_tenant_schema_expansion as expansion
from app.tenancy import SS_TENANT, SS_TENANT_ID, tenant_to_document


FIXED_TIME = datetime(2026, 9, 21, 10, 30, tzinfo=timezone.utc)
EXPECTED_TENANT_COLLECTIONS = {
    "audit_log",
    "companies",
    "contracts",
    "customer_prices",
    "invoices",
    "machine_requests",
    "machines",
    "newsletter",
    "offers",
    "orders",
    "price_history",
    "products",
    "push_registrations",
    "settings",
    "shop_orders",
    "subscriptions",
    "uploads",
}


def isolated_database(name: str):
    database_name = f"ordo_test_tenant_expansion_{name}"
    assert database_name != "ordo_staging"
    return mongomock.MongoClient(tz_aware=True)[database_name]


def runner(database, *, migrations=(expansion.MIGRATION,)):
    return MigrationRunner(
        database,
        app_env="test",
        application_version="tenant-expansion-test",
        migrations=migrations,
        lease_seconds=3,
    )


def canonical_tenant(*, created_at=FIXED_TIME, updated_at=FIXED_TIME):
    return tenant_to_document(
        SS_TENANT,
        created_at=created_at,
        updated_at=updated_at,
    )


def business_documents(database):
    technical = {"tenants", MIGRATION_COLLECTION, "schema_migration_lock"}
    return {
        name: list(database[name].find({}))
        for name in database.list_collection_names()
        if name not in technical
    }


def test_migration_snapshot_matches_canonical_domain_persistence_contract():
    persisted = canonical_tenant()
    without_timestamps = {
        key: value
        for key, value in persisted.items()
        if key not in {"createdAt", "updatedAt"}
    }

    assert expansion.SS_TENANT_ID == SS_TENANT_ID
    assert without_timestamps == expansion.SS_TENANT_FIELDS
    assert persisted["legalName"] is None
    assert persisted["schemaVersion"] == 1


def test_tenant_persistence_requires_ordered_utc_timestamps():
    non_utc = timezone(timedelta(hours=1))
    with pytest.raises(ValueError, match="must use UTC"):
        tenant_to_document(
            SS_TENANT,
            created_at=FIXED_TIME.astimezone(non_utc),
            updated_at=FIXED_TIME.astimezone(non_utc),
        )
    with pytest.raises(ValueError, match="must not precede"):
        tenant_to_document(
            SS_TENANT,
            created_at=FIXED_TIME,
            updated_at=FIXED_TIME - timedelta(seconds=1),
        )


def test_index_matrix_covers_only_approved_tenant_collections():
    scoped = {
        spec.collection
        for spec in expansion.INDEX_SPECS
        if spec.collection != expansion.TENANT_COLLECTION
    }

    assert scoped == EXPECTED_TENANT_COLLECTIONS
    assert len(expansion.INDEX_SPECS) == 19
    assert all(spec.collection != "counters" for spec in expansion.INDEX_SPECS)
    assert all(
        spec.partial_filter is not None
        for spec in expansion.INDEX_SPECS
        if spec.collection != expansion.TENANT_COLLECTION
    )


def test_empty_database_dry_run_reports_plan_without_any_write():
    database = isolated_database("empty_preview")

    report = runner(database).run(dry_run=True).as_dict()

    plan = report["plannedMigrations"][0]
    assert plan["version"] == 2
    assert plan["expectedChanges"]["tenantDocumentsToInsert"] == 1
    assert plan["expectedChanges"]["indexesToCreate"] == 19
    assert plan["expectedChanges"]["businessDocumentsModified"] == 0
    assert database.list_collection_names() == []


def test_dry_run_preserves_legacy_documents_indexes_and_migration_status():
    database = isolated_database("legacy_preview")
    database.companies.insert_one({"_id": "legacy", "id": "c1", "name": "Legacy"})
    database.products.insert_one({"_id": "legacy-product", "id": "p1"})
    database.products.create_index("id", unique=True, name="uniq_product_id")
    documents_before = {
        name: list(database[name].find({}))
        for name in database.list_collection_names()
    }
    indexes_before = {
        name: deepcopy(database[name].index_information())
        for name in database.list_collection_names()
    }

    runner(database).run(dry_run=True)

    assert {
        name: list(database[name].find({}))
        for name in database.list_collection_names()
    } == documents_before
    assert {
        name: database[name].index_information()
        for name in database.list_collection_names()
    } == indexes_before
    assert MIGRATION_COLLECTION not in database.list_collection_names()
    assert "tenants" not in database.list_collection_names()


def test_empty_database_application_creates_only_schema_and_canonical_tenant():
    database = isolated_database("empty_apply")

    report = runner(database).run().as_dict()

    tenant_document = database.tenants.find_one({"id": SS_TENANT_ID})
    expansion._validate_canonical_tenant(tenant_document)
    assert report["executedMigrations"][0]["tenantDocumentsInserted"] == 1
    assert report["executedMigrations"][0]["indexesCreated"] == 19
    assert all(rows == [] for rows in business_documents(database).values())
    assert not any(
        database[name].find_one({"_demoSeed": {"$exists": True}})
        for name in database.list_collection_names()
    )


def test_all_created_indexes_have_the_exact_approved_definition():
    database = isolated_database("index_matrix")
    runner(database).run()

    for spec in expansion.INDEX_SPECS:
        index = database[spec.collection].index_information()[spec.name]
        assert tuple(index["key"]) == spec.keys
        assert bool(index.get("unique", False)) is spec.unique
        assert index.get("partialFilterExpression") == spec.partial_filter


def test_legacy_documents_without_tenant_id_are_not_modified_or_blocked():
    database = isolated_database("legacy_apply")
    legacy = {
        "companies": {"_id": "company", "id": "same"},
        "products": {"_id": "product", "id": "same"},
        "orders": {"_id": "order", "id": "same", "companyId": "same"},
        "settings": {"_id": "shop", "freeShippingThreshold": 50.0},
    }
    for name, document in legacy.items():
        database[name].insert_one(deepcopy(document))

    runner(database).run()

    for name, document in legacy.items():
        assert database[name].find_one({"_id": document["_id"]}) == document


def test_dry_run_accepts_legacy_duplicates_and_plans_partial_unique_index():
    database = isolated_database("partial_unique_semantics")
    database.companies.insert_many([
        {"_id": "legacy-one", "id": "same"},
        {"_id": "legacy-two", "id": "same"},
    ])

    report = runner(database).run(dry_run=True).as_dict()

    assert database.companies.count_documents({"id": "same"}) == 2
    action = next(
        item
        for item in report["plannedMigrations"][0]["expectedChanges"]["indexActions"]
        if item["name"] == "uniq_tenant_company_id"
    )
    assert action["partialFilterExpression"] == {
        "tenantId": {"$type": "string"},
        "id": {"$type": "string"},
    }
    assert "uniq_tenant_company_id" not in database.companies.index_information()


def test_tenant_index_accepts_same_business_id_in_two_tenants_without_legacy_index():
    database = isolated_database("two_tenant_keys")
    database.products.insert_many([
        {"tenantId": SS_TENANT_ID, "id": "p1"},
        {"tenantId": "tnt_other_0001", "id": "p1"},
    ])

    runner(database).run()

    assert database.products.count_documents({"id": "p1"}) == 2


def test_existing_global_unique_index_is_preserved_during_expand_phase():
    database = isolated_database("preserve_global_index")
    database.products.create_index("id", unique=True, name="uniq_product_id")

    runner(database).run()

    indexes = database.products.index_information()
    assert indexes["uniq_product_id"]["key"] == [("id", 1)]
    assert indexes["uniq_product_id"]["unique"] is True
    assert "uniq_tenant_product_id" in indexes
    database.products.insert_one({"tenantId": SS_TENANT_ID, "id": "p1"})
    with pytest.raises(DuplicateKeyError):
        database.products.insert_one({"tenantId": "tnt_other_0001", "id": "p1"})


def test_correct_existing_tenant_is_accepted_without_overwrite():
    database = isolated_database("existing_tenant")
    original = canonical_tenant(
        created_at=FIXED_TIME - timedelta(days=2),
        updated_at=FIXED_TIME - timedelta(days=1),
    )
    database.tenants.insert_one(deepcopy(original))

    report = runner(database).run().as_dict()

    stored = database.tenants.find_one({"id": SS_TENANT_ID})
    stored.pop("_id")
    assert stored == original
    assert report["executedMigrations"][0]["tenantDocumentsInserted"] == 0


@pytest.mark.parametrize(
    "document",
    [
        {**canonical_tenant(), "slug": "wrong-slug"},
        {**canonical_tenant(), "id": "tnt_wrong_0001"},
        {**canonical_tenant(), "displayName": "Manipulated"},
        {key: value for key, value in canonical_tenant().items() if key != "timezone"},
        {**canonical_tenant(), "schemaVersion": 2},
        {**canonical_tenant(), "unexpected": "field"},
        {**canonical_tenant(), "createdAt": "not-a-datetime"},
        {
            **canonical_tenant(),
            "updatedAt": FIXED_TIME - timedelta(seconds=1),
        },
    ],
)
def test_conflicting_or_incomplete_tenant_document_fails_before_writes(document):
    database = isolated_database("tenant_conflict")
    database.tenants.insert_one(document)
    before = list(database.tenants.find({}))

    with pytest.raises(MigrationStateError):
        runner(database).run(dry_run=True)

    assert list(database.tenants.find({})) == before
    assert MIGRATION_COLLECTION not in database.list_collection_names()


def test_multiple_documents_conflicting_with_ss_identity_fail_closed():
    database = isolated_database("multiple_tenants")
    first = canonical_tenant()
    second = {**canonical_tenant(), "id": "tnt_wrong_0001"}
    database.tenants.insert_many([first, second])

    with pytest.raises(MigrationStateError, match="Multiple tenant documents"):
        runner(database).run(dry_run=True)


def test_correct_existing_index_is_reported_unchanged():
    database = isolated_database("correct_index")
    spec = next(spec for spec in expansion.INDEX_SPECS if spec.collection == "companies")
    database.companies.create_index(
        list(spec.keys),
        name=spec.name,
        unique=spec.unique,
        partialFilterExpression=dict(spec.partial_filter),
    )

    preview = runner(database).run(dry_run=True).as_dict()
    action = next(
        item
        for item in preview["plannedMigrations"][0]["expectedChanges"]["indexActions"]
        if item["name"] == spec.name
    )

    assert action["action"] == "unchanged"
    report = runner(database).run().as_dict()
    assert report["executedMigrations"][0]["indexesCreated"] == 18
    assert report["executedMigrations"][0]["indexesAlreadyCorrect"] == 1


@pytest.mark.parametrize(
    "wrong_options",
    [
        {"keys": [("tenantId", 1)], "unique": True, "partial": {"tenantId": {"$type": "string"}}},
        {"keys": [("tenantId", 1), ("id", 1)], "unique": False, "partial": {"tenantId": {"$type": "string"}, "id": {"$type": "string"}}},
        {"keys": [("tenantId", 1), ("id", 1)], "unique": True, "partial": None},
    ],
)
def test_same_index_name_with_wrong_definition_fails_before_writes(wrong_options):
    database = isolated_database("wrong_named_index")
    options = {"name": "uniq_tenant_company_id", "unique": wrong_options["unique"]}
    if wrong_options["partial"] is not None:
        options["partialFilterExpression"] = wrong_options["partial"]
    database.companies.create_index(wrong_options["keys"], **options)
    before = deepcopy(database.companies.index_information())

    with pytest.raises(MigrationStateError, match="another definition"):
        runner(database).run(dry_run=True)

    assert database.companies.index_information() == before
    assert "tenants" not in database.list_collection_names()


def test_same_index_keys_under_another_name_fail_before_writes():
    database = isolated_database("wrong_index_name")
    spec = next(spec for spec in expansion.INDEX_SPECS if spec.collection == "companies")
    database.companies.create_index(
        list(spec.keys),
        name="unexpected_name",
        unique=True,
        partialFilterExpression=dict(spec.partial_filter),
    )

    with pytest.raises(MigrationStateError, match="already exist as unexpected_name"):
        runner(database).run(dry_run=True)


def test_existing_tenant_documents_with_unique_conflicts_fail_before_writes():
    database = isolated_database("duplicate_tenant_keys")
    database.companies.insert_many([
        {"_id": "one", "tenantId": SS_TENANT_ID, "id": "c1"},
        {"_id": "two", "tenantId": SS_TENANT_ID, "id": "c1"},
    ])
    before = list(database.companies.find({}))

    with pytest.raises(MigrationStateError, match="duplicate values"):
        runner(database).run(dry_run=True)

    assert list(database.companies.find({})) == before
    assert "tenants" not in database.list_collection_names()


@pytest.mark.parametrize("tenant_id", [None, "", " tnt_ss_0001 ", 123])
def test_malformed_existing_tenant_id_fails_before_writes(tenant_id):
    database = isolated_database("malformed_tenant_id")
    database.orders.insert_one({"id": "o1", "tenantId": tenant_id})

    with pytest.raises(MigrationStateError, match="invalid tenantId"):
        runner(database).run(dry_run=True)


def test_partial_index_failure_is_restartable_and_never_reports_completed(monkeypatch):
    database = isolated_database("partial_index_failure")
    collection = database.orders
    original_create_index = collection.create_index
    failed = {"value": False}

    def fail_once(*args, **kwargs):
        if kwargs.get("name") == "uniq_tenant_order_id" and not failed["value"]:
            failed["value"] = True
            raise OperationFailure("simulated index outage")
        return original_create_index(*args, **kwargs)

    monkeypatch.setattr(collection, "create_index", fail_once)
    with pytest.raises(OperationFailure, match="simulated index outage"):
        runner(database).run()

    record = database[MIGRATION_COLLECTION].find_one({"version": 2})
    assert record["status"] == "failed"
    assert database.tenants.count_documents({}) == 0

    monkeypatch.setattr(collection, "create_index", original_create_index)
    result = runner(database).run().as_dict()

    assert result["executedMigrations"][0]["attempt"] == 2
    assert database[MIGRATION_COLLECTION].find_one({"version": 2})["status"] == "completed"
    assert database.tenants.count_documents({"id": SS_TENANT_ID}) == 1


def test_completion_status_failure_reuses_existing_tenant_on_retry(monkeypatch):
    database = isolated_database("completion_status_failure")
    migration_runner = runner(database)
    metadata = migration_runner._metadata
    original_update_one = metadata.update_one
    fail_once = {"value": True}

    def injected_update(filter_document, update_document, *args, **kwargs):
        if (
            update_document.get("$set", {}).get("status") == "completed"
            and fail_once["value"]
        ):
            fail_once["value"] = False
            raise AutoReconnect("simulated completion outage")
        return original_update_one(filter_document, update_document, *args, **kwargs)

    monkeypatch.setattr(metadata, "update_one", injected_update)
    with pytest.raises(AutoReconnect, match="completion outage"):
        migration_runner.run()

    assert database.tenants.count_documents({"id": SS_TENANT_ID}) == 1
    assert metadata.find_one({"version": 2})["status"] == "failed"

    monkeypatch.setattr(metadata, "update_one", original_update_one)
    result = runner(database).run().as_dict()

    assert result["executedMigrations"][0]["attempt"] == 2
    assert result["executedMigrations"][0]["tenantDocumentsInserted"] == 0
    assert database.tenants.count_documents({"id": SS_TENANT_ID}) == 1


def test_successful_migration_is_idempotent_and_second_run_writes_nothing():
    database = isolated_database("repeat")
    first = runner(database).run().as_dict()
    tenant_before = deepcopy(database.tenants.find_one({"id": SS_TENANT_ID}))
    indexes_before = {
        spec.collection: deepcopy(database[spec.collection].index_information())
        for spec in expansion.INDEX_SPECS
    }

    second = runner(database).run().as_dict()

    assert len(first["executedMigrations"]) == 1
    assert second["executedMigrations"] == []
    assert database.tenants.find_one({"id": SS_TENANT_ID}) == tenant_before
    assert {
        spec.collection: database[spec.collection].index_information()
        for spec in expansion.INDEX_SPECS
    } == indexes_before
    assert database[MIGRATION_COLLECTION].find_one({"version": 2})["attempts"] == 1


def test_registry_executes_baseline_tenant_expansion_and_membership_schema():
    database = isolated_database("full_registry")

    report = runner(database, migrations=None).run().as_dict()

    assert [item["version"] for item in report["executedMigrations"]] == [1, 2, 6, 3, 4, 5, 7, 8, 9, 10, 11]
    assert database.tenants.count_documents({"id": SS_TENANT_ID}) == 1
    documents = business_documents(database)
    settings = documents.pop("settings")
    assert len(settings) == 1
    settings[0].pop("_id")
    assert settings == [{
        "tenantId": SS_TENANT_ID, "key": "shop", "currency": "EUR",
        "freeShippingThreshold": 59.0, "freeShippingThresholdMinor": 5900,
        "shippingFee": 0.0, "shippingFeeMinor": 0,
        "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True,
    }]
    assert all(rows == [] for rows in documents.values())
