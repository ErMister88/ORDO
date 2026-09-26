from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import io
import logging
import os
from pathlib import Path

import mongomock
import pytest

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_package11")
os.environ.setdefault("JWT_SECRET", "test-only-package11-secret-at-least-32-bytes")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TENANCY_MODE", "single")
os.environ.setdefault("DEFAULT_TENANT_ID", "tnt_ss_0001")

from app import backup_restore
from app.background_jobs import BackgroundJobQueue, JobClaim
from app.capabilities import capability_snapshot
from app.migrations.registry import get_migrations
from app.migrations.versions import v0011_operational_reliability as migration11
from app.observability import (
    JsonFormatter,
    OperationalContextMiddleware,
    configure_structured_logging,
    report_operational_failure,
)
from app.reconciliation import run_reconciliation
from app.routers import operations
from test_customer_commerce_platform import AsyncDatabase, run, scoped


class CapabilityDatabase(AsyncDatabase):
    async def command(self, name: str):
        assert name == "ping"
        return {"ok": 1}

    def __getattr__(self, name: str):
        return self[name]


def prepare_jobs(database: AsyncDatabase) -> None:
    database.raw.background_jobs.create_index(
        [("tenantId", 1), ("jobType", 1), ("idempotencyKey", 1)], unique=True,
    )


def test_capabilities_separate_critical_readiness_from_optional_providers(monkeypatch):
    database = CapabilityDatabase("package11_capabilities")
    for migration in get_migrations():
        database.raw.schema_migrations.insert_one({
            "version": migration.version, "status": "completed", "checksum": migration.checksum,
        })
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("TENANCY_MODE", "single")
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tnt_ss_0001")
    for name in ("STRIPE_API_KEY", "STRIPE_WEBHOOK_SECRET", "SMTP_HOST", "SMTP_FROM_EMAIL", "EMERGENT_EMAIL_KEY", "EMERGENT_LLM_KEY", "STORAGE_BACKEND"):
        monkeypatch.delenv(name, raising=False)

    result = run(capability_snapshot(database))

    assert result["ready"] is True
    assert result["capabilities"]["database"]["status"] == "available"
    assert result["capabilities"]["payments"]["status"] == "not_configured"
    assert result["capabilities"]["email"]["status"] == "not_configured"
    assert result["capabilities"]["storage"]["status"] == "not_configured"


def test_readiness_fails_closed_for_outdated_schema(monkeypatch):
    database = CapabilityDatabase("package11_schema_old")
    database.raw.schema_migrations.insert_one({"version": 10, "status": "completed"})
    monkeypatch.setenv("TENANCY_MODE", "single")
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tnt_ss_0001")

    result = run(capability_snapshot(database))

    assert result["ready"] is False
    assert result["capabilities"]["schema"]["expectedVersion"] == 11
    assert result["capabilities"]["schema"]["appliedVersion"] == 10


def test_readiness_fails_closed_for_duplicate_migration_ledger_entry(monkeypatch):
    database = CapabilityDatabase("package11_schema_duplicate")
    for migration in get_migrations():
        database.raw.schema_migrations.insert_one({
            "version": migration.version, "status": "completed", "checksum": migration.checksum,
        })
    latest = get_migrations()[-1]
    database.raw.schema_migrations.insert_one({
        "version": latest.version, "status": "completed", "checksum": latest.checksum,
    })
    monkeypatch.setenv("TENANCY_MODE", "single")
    monkeypatch.setenv("DEFAULT_TENANT_ID", "tnt_ss_0001")

    result = run(capability_snapshot(database))

    assert result["ready"] is False
    assert result["capabilities"]["schema"]["status"] == "unavailable"


def test_migration_11_is_additive_and_creates_only_operational_indexes():
    database = mongomock.MongoClient().ordo_test_package11_migration
    plan = migration11.inspect(database)

    result = migration11.apply(database, type("Context", (), {"checkpoint": lambda self: None})())

    assert plan.expected_changes["documentsChanged"] == 0
    assert result == {"indexesEnsured": 8, "documentsChanged": 0}
    assert database.list_collection_names()
    assert all(database[name].count_documents({}) == 0 for name in database.list_collection_names())


def test_job_intent_is_idempotent_and_only_one_worker_claims_it():
    database = AsyncDatabase("package11_job_claim")
    prepare_jobs(database)
    access = scoped(database)
    queue = BackgroundJobQueue(access)

    first = run(queue.enqueue("reconcile.tenant", actor_id="admin", idempotency_key="reconcile-2026-09-26"))
    replay = run(queue.enqueue("reconcile.tenant", actor_id="admin", idempotency_key="reconcile-2026-09-26"))
    one = run(queue.claim_next(worker_id="worker-a"))
    two = run(queue.claim_next(worker_id="worker-b"))

    assert replay["id"] == first["id"]
    assert one is not None and two is None
    assert database.raw.background_jobs.count_documents({}) == 1


def test_job_lease_prevents_stale_worker_completion_and_recovers_after_expiry():
    database = AsyncDatabase("package11_job_lease")
    prepare_jobs(database)
    access = scoped(database)
    queue = BackgroundJobQueue(access)
    run(queue.enqueue("reconcile.tenant", actor_id="admin", idempotency_key="reconcile-lease-test"))
    stale = run(queue.claim_next(worker_id="worker-old"))
    assert stale is not None
    database.raw.background_jobs.update_one(
        {"id": stale.job_id}, {"$set": {"leaseUntil": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    current = run(queue.claim_next(worker_id="worker-new"))
    assert current is not None and current.lease_token != stale.lease_token
    with pytest.raises(RuntimeError, match="lease was lost"):
        run(queue.complete(stale, {"resourceId": "old"}))
    run(queue.complete(current, {"resourceId": "new"}))
    assert database.raw.background_jobs.find_one({"id": current.job_id})["status"] == "completed"


def test_expired_job_lease_cannot_heartbeat_complete_or_record_failure():
    database = AsyncDatabase("package11_expired_job_lease")
    prepare_jobs(database)
    access = scoped(database)
    queue = BackgroundJobQueue(access)
    run(queue.enqueue("reconcile.tenant", actor_id="admin", idempotency_key="expired-lease"))
    claim = run(queue.claim_next(worker_id="worker-old"))
    assert claim is not None
    database.raw.background_jobs.update_one(
        {"id": claim.job_id},
        {"$set": {"leaseUntil": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )

    with pytest.raises(RuntimeError, match="lease was lost"):
        run(queue.heartbeat(claim))
    with pytest.raises(RuntimeError, match="lease was lost"):
        run(queue.complete(claim, {"resourceId": "stale"}))
    with pytest.raises(RuntimeError, match="lease was lost"):
        run(queue.fail(claim, error_reference="joberr_stale"))
    assert database.raw.background_jobs.find_one({"id": claim.job_id})["status"] == "processing"


def test_failed_job_backoff_and_safe_retry_do_not_change_business_data():
    database = AsyncDatabase("package11_job_retry")
    prepare_jobs(database)
    access = scoped(database)
    queue = BackgroundJobQueue(access)
    run(access.orders.insert_one({"id": "order-1", "status": "Neu"}))
    run(queue.enqueue("reconcile.tenant", actor_id="admin", idempotency_key="reconcile-retry-test"))
    claim = run(queue.claim_next(worker_id="worker"))
    assert claim is not None
    run(queue.fail(claim, error_reference="joberr_reference"))
    row = database.raw.background_jobs.find_one({"id": claim.job_id})
    assert row["status"] == "failed" and row["nextRetryAt"] > row["updatedAt"]
    run(queue.retry(claim.job_id))
    assert database.raw.background_jobs.find_one({"id": claim.job_id})["status"] == "pending"
    assert database.raw.orders.find_one({"id": "order-1"})["status"] == "Neu"


def test_reconciliation_detects_mismatches_without_mutating_commercial_documents():
    database = AsyncDatabase("package11_reconciliation")
    access = scoped(database)
    run(access.invoices.insert_one({
        "id": "inv-1", "orderId": "missing-order", "status": "Bezahlt",
        "amountMinor": 2000, "paidAmountMinor": 1000,
        "paymentRecords": [{"amountMinor": 500}],
    }))
    before = database.raw.invoices.find_one({"id": "inv-1"})

    result = run(run_reconciliation(access, actor_id="admin"))

    codes = {issue["code"] for issue in result["issues"]}
    assert {"invoice_order_missing", "invoice_payment_sum_mismatch", "invoice_paid_amount_mismatch"} <= codes
    assert result["status"] == "REQUIRES_REVIEW"
    assert database.raw.invoices.find_one({"id": "inv-1"}) == before
    assert database.raw.reconciliation_runs.count_documents({"tenantId": "tenant-a"}) == 1


def test_reconciliation_is_tenant_isolated():
    database = AsyncDatabase("package11_reconciliation_tenants")
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    run(tenant_b.invoices.insert_one({"id": "foreign", "orderId": "missing"}))

    result = run(run_reconciliation(tenant_a, actor_id="admin"))

    assert result["status"] == "OK"
    assert result["issueCount"] == 0


def test_reconciliation_run_id_is_idempotent():
    database = AsyncDatabase("package11_reconciliation_idempotent")
    access = scoped(database)

    first = run(run_reconciliation(access, actor_id="admin", run_id="rec_fixed"))
    replay = run(run_reconciliation(access, actor_id="admin", run_id="rec_fixed"))

    assert replay["id"] == first["id"]
    assert replay["status"] == first["status"]
    assert database.raw.reconciliation_runs.count_documents({"tenantId": "tenant-a"}) == 1


def test_operations_problem_view_is_tenant_scoped_and_redacted():
    database = AsyncDatabase("package11_operations_tenants")
    own = scoped(database)
    foreign = scoped(database, tenant="tenant-b")
    run(own.background_jobs.insert_one({
        "id": "job-own", "jobType": "reconcile.tenant", "status": "failed",
        "payload": {"secret": "must-not-leak"}, "updatedAt": "2026-09-26", "retryAllowed": True,
    }))
    run(foreign.background_jobs.insert_one({
        "id": "job-foreign", "jobType": "reconcile.tenant", "status": "failed",
        "updatedAt": "2026-09-26", "retryAllowed": True,
    }))
    run(own.technical_errors.insert_one({
        "id": "err-own", "operation": "email.delivery", "occurredAt": "2026-09-26",
        "rawPayload": "must-not-leak",
    }))
    run(foreign.technical_errors.insert_one({
        "id": "err-foreign", "operation": "email.delivery", "occurredAt": "2026-09-26",
    }))

    result = run(operations.list_operational_problems({"id": "admin", "role": "admin"}, own))
    encoded = json.dumps(result)

    assert {row["id"] for row in result} == {"job-own", "err-own"}
    assert "foreign" not in encoded
    assert "must-not-leak" not in encoded
    assert "tenantId" not in encoded


def test_structured_formatter_ignores_arbitrary_sensitive_record_fields(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "request_failed", (), None)
    record.password = "do-not-log"
    record.token = "do-not-log-either"
    record.request_id = "req_safe"

    payload = JsonFormatter().format(record)

    parsed = json.loads(payload)
    assert parsed["request_id"] == "req_safe"
    assert "do-not-log" not in payload and "password" not in payload and "token" not in payload


def test_structured_logging_disables_query_bearing_uvicorn_access_log(monkeypatch):
    access_logger = logging.getLogger("uvicorn.access")
    monkeypatch.setattr(access_logger, "disabled", False)

    configure_structured_logging()

    assert access_logger.disabled is True


def test_unhandled_error_response_contains_reference_but_not_exception_detail():
    async def broken_app(_scope, _receive, _send):
        raise RuntimeError("password=must-never-leak")

    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    middleware = OperationalContextMiddleware(broken_app, database=object())
    run(middleware({"type": "http", "method": "GET", "path": "/broken", "headers": []}, receive, send))
    body = b"".join(message.get("body", b"") for message in messages).decode()
    assert "Technischer Fehler" in body and "err_" in body
    assert "must-never-leak" not in body and "password" not in body


def test_provider_failure_is_tenant_scoped_and_logs_only_safe_reference():
    database = AsyncDatabase("package11_provider_failure")
    access = scoped(database)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("package11.provider")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    error_id = run(report_operational_failure(
        access, logger, operation="email.delivery", category="email_delivery",
    ))

    row = database.raw.technical_errors.find_one({"id": error_id})
    assert row["tenantId"] == "tenant-a"
    assert row["operation"] == "email.delivery"
    assert "password" not in stream.getvalue().lower()
    assert error_id in stream.getvalue()


def _seed_backup_source(database):
    database.tenants.insert_one({"id": "tnt_ss_0001", "status": "active"})
    database.tenant_memberships.insert_one({"id": "m1", "tenantId": "tnt_ss_0001"})
    for migration in get_migrations():
        database.schema_migrations.insert_one({
            "version": migration.version, "status": "completed", "checksum": migration.checksum,
        })


def test_backup_manifest_is_written_only_after_nonempty_archive(monkeypatch, tmp_path):
    client = mongomock.MongoClient()
    database = client.ordo_test_backup
    _seed_backup_source(database)

    def fake_tool(command):
        archive = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--archive=")))
        archive.write_bytes(b"verified-test-archive")

    monkeypatch.setattr(backup_restore, "_run_tool", fake_tool)
    archive, manifest_path = backup_restore.create_backup(
        client, mongo_url="mongodb://example.test", database_name="ordo_test_backup",
        environment="test", output_directory=tmp_path,
    )
    manifest = backup_restore.load_manifest(manifest_path)
    assert archive.is_file() and manifest["archiveSha256"] == backup_restore._sha256(archive)
    assert manifest["schemaVersion"] == 11
    assert "mongodb://" not in manifest_path.read_text()


def test_restore_refuses_production_source_target_and_nonempty_database(tmp_path):
    client = mongomock.MongoClient()
    manifest = tmp_path / "backup.archive.gz.json"
    archive = tmp_path / "backup.archive.gz"
    archive.write_bytes(b"archive")
    manifest.write_text(json.dumps({
        "manifestVersion": 1, "databaseIdentifier": "ordo_restore_source", "schemaVersion": 11,
        "archiveFile": archive.name, "archiveSize": archive.stat().st_size,
        "archiveSha256": backup_restore._sha256(archive), "collectionCounts": {},
        "indexNames": {}, "integrity": "complete",
    }))
    with pytest.raises(ValueError, match="production"):
        backup_restore.restore_backup(client, mongo_url="mongodb://example.test", manifest_path=manifest, target_database="ordo_restore_x", environment="production")
    with pytest.raises(ValueError, match="must differ"):
        backup_restore.restore_backup(client, mongo_url="mongodb://example.test", manifest_path=manifest, target_database="ordo_restore_source", environment="test")
    client.ordo_restore_used.marker.insert_one({"x": 1})
    with pytest.raises(ValueError, match="must be empty"):
        backup_restore.restore_backup(client, mongo_url="mongodb://example.test", manifest_path=manifest, target_database="ordo_restore_used", environment="test")


def test_restore_rejects_archive_path_outside_manifest_directory(tmp_path):
    manifest = tmp_path / "backup.archive.gz.json"
    manifest.write_text(json.dumps({
        "manifestVersion": 1, "databaseIdentifier": "ordo_test_backup", "schemaVersion": 11,
        "archiveFile": "../outside.archive.gz", "archiveSize": 1,
        "archiveSha256": "0" * 64, "collectionCounts": {}, "indexNames": {},
        "integrity": "complete",
    }))

    with pytest.raises(ValueError, match="archive path is invalid"):
        backup_restore.load_manifest(manifest)


def test_restore_verification_detects_missing_collections_and_tenant_references():
    client = mongomock.MongoClient()
    database = client.ordo_restore_verify
    database.tenants.insert_one({"id": "tenant-a", "status": "active"})
    database.tenant_memberships.insert_one({"id": "m1", "tenantId": "tenant-missing"})
    database.invoices.insert_one({"id": "inv-1", "tenantId": "tenant-a", "orderId": "missing"})
    manifest = {"collectionCounts": {"tenants": 1, "orders": 2}, "indexNames": {"tenants": ["_id_"]}, "schemaVersion": 0}

    result = backup_restore.verify_restored_database(database, manifest)

    assert result["verified"] is False
    assert result["missingCollections"] == ["orders"]
    assert result["tenantReferenceErrors"] == 1
    assert result["membershipUserReferenceErrors"] == 1
    assert result["commercialReferenceErrors"] == 1
