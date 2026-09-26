from __future__ import annotations

import io
import os
from pathlib import Path
import asyncio
import time

import mongomock
import pytest
from fastapi import HTTPException, UploadFile

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_package12")
os.environ.setdefault("JWT_SECRET", "test-only-package12-secret-at-least-32-bytes")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TENANCY_MODE", "single")
os.environ.setdefault("DEFAULT_TENANT_ID", "tnt_ss_0001")

from app.background_jobs import BackgroundJobQueue
from app.capabilities import capability_snapshot
from app.email_provider import ProviderReceipt, email_configuration_status
from app.emailer import deliver_outbox_email, email_shell, send_email
from app.migrations.registry import get_migrations
from app.migrations.versions import v0012_storage_communication_worker as migration12
from app.notifications import create_notification
from app.routers import files, notifications, operations
from app.storage import (
    LocalStorageProvider, StorageError, StoredObject, build_storage_key,
    storage_configuration_status, validate_upload,
)
from app.worker import record_worker_heartbeat, run_worker_once
from test_customer_commerce_platform import AsyncDatabase, run, scoped


PNG = b"\x89PNG\r\n\x1a\n" + b"safe-image" * 10


class MemoryStorage:
    name = "memory"

    def __init__(self):
        self.objects = {}

    def put(self, key, data, content_type):
        self.objects[key] = (bytes(data), content_type)
        from hashlib import sha256
        return StoredObject(key, len(data), content_type, sha256(data).hexdigest())

    def read(self, key):
        if key not in self.objects:
            raise StorageError("missing")
        return self.objects[key]

    def delete(self, key):
        self.objects.pop(key, None)

    def exists(self, key):
        return key in self.objects

    def metadata(self, key):
        return {"size": len(self.objects[key][0])}

    def signed_url(self, key, *, expires_seconds=300):
        return None


def prepare_delivery_indexes(database: AsyncDatabase) -> None:
    database.raw.email_outbox.create_index([("tenantId", 1), ("deduplicationKey", 1)], unique=True)
    database.raw.background_jobs.create_index(
        [("tenantId", 1), ("jobType", 1), ("idempotencyKey", 1)], unique=True,
    )


def test_storage_key_is_server_structured_and_upload_magic_is_verified():
    key = build_storage_key(
        tenant_id="tenant-a", visibility="public", resource_type="product",
        resource_id="../../foreign", file_id="file_" + "a" * 24, content_type="image/png",
    )
    assert key.startswith("tenants/tenant-a/public/product/")
    assert "foreign" not in key and ".." not in key
    assert validate_upload(PNG, "image/png", visibility="public") == "image/png"
    with pytest.raises(StorageError, match="differ"):
        validate_upload(PNG, "image/jpeg", visibility="public")
    with pytest.raises(StorageError, match="Unsupported"):
        validate_upload(b"<script>alert(1)</script>", "image/png", visibility="public")


def test_local_storage_is_explicit_and_blocked_in_production(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_BACKEND", "local")
    monkeypatch.setenv("STORAGE_LOCAL_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("APP_ENV", "production")
    assert storage_configuration_status()[0] == "unavailable"
    monkeypatch.setenv("APP_ENV", "test")
    provider = LocalStorageProvider()
    stored = provider.put("tenants/t/public/product/x/file.png", PNG, "image/png")
    assert provider.read(stored.key) == (PNG, "image/png")
    provider.delete(stored.key)
    assert provider.exists(stored.key) is False


def test_upload_metadata_is_tenant_scoped_and_storage_key_is_not_returned(monkeypatch):
    database = AsyncDatabase("package12_upload")
    access = scoped(database, tenant="tenant-a")
    provider = MemoryStorage()
    monkeypatch.setattr(files, "get_storage_provider", lambda: provider)
    run(access.products.insert_one({"id": "product-a", "name": "Product"}))
    upload = UploadFile(filename="../../product.png", file=io.BytesIO(PNG), headers={"content-type": "image/png"})
    response = run(files.upload_file(
        {"id": "admin", "role": "admin"}, access, upload,
        resource_type="product", resource_id="product-a", visibility="public",
    ))
    row = database.raw.uploads.find_one({"id": response["id"]})
    assert row["tenantId"] == "tenant-a"
    assert row["originalFilename"] == "product.png"
    assert row["storageKey"].startswith("tenants/tenant-a/public/product/")
    assert "storageKey" not in response and response["path"] == response["id"]
    served = run(files.serve_public_file(response["id"], access))
    assert served.body == PNG and served.headers["x-content-type-options"] == "nosniff"
    foreign = scoped(database, tenant="tenant-b")
    with pytest.raises(HTTPException) as missing:
        run(files.serve_public_file(response["id"], foreign))
    assert missing.value.status_code == 404


def test_upload_distinguishes_invalid_content_from_provider_outage(monkeypatch):
    database = AsyncDatabase("package12_upload_failure")
    access = scoped(database, tenant="tenant-a")
    run(access.products.insert_one({"id": "product-a", "name": "Product"}))
    invalid = UploadFile(
        filename="fake.png", file=io.BytesIO(b"not-an-image"),
        headers={"content-type": "image/png"},
    )
    with pytest.raises(HTTPException) as invalid_error:
        run(files.upload_file(
            {"id": "admin", "role": "admin"}, access, invalid,
            resource_type="product", resource_id="product-a", visibility="public",
        ))
    assert invalid_error.value.status_code == 400

    class UnavailableStorage(MemoryStorage):
        def put(self, key, data, content_type):
            raise StorageError("provider unavailable")

    monkeypatch.setattr(files, "get_storage_provider", lambda: UnavailableStorage())
    valid = UploadFile(
        filename="product.png", file=io.BytesIO(PNG), headers={"content-type": "image/png"},
    )
    with pytest.raises(HTTPException) as provider_error:
        run(files.upload_file(
            {"id": "admin", "role": "admin"}, access, valid,
            resource_type="product", resource_id="product-a", visibility="public",
        ))
    assert provider_error.value.status_code == 503
    assert database.raw.uploads.count_documents({}) == 0


def test_private_document_is_not_public_and_requires_visible_resource(monkeypatch):
    database = AsyncDatabase("package12_private")
    access = scoped(database, tenant="tenant-a")
    provider = MemoryStorage()
    monkeypatch.setattr(files, "get_storage_provider", lambda: provider)
    run(access.invoices.insert_one({"id": "inv-1", "companyId": "company-a"}))
    upload = UploadFile(filename="invoice.pdf", file=io.BytesIO(b"%PDF-1.7\ncontent"), headers={"content-type": "application/pdf"})
    response = run(files.upload_file(
        {"id": "admin", "role": "admin"}, access, upload,
        resource_type="invoice", resource_id="inv-1", visibility="private",
    ))
    with pytest.raises(HTTPException) as public_denied:
        run(files.serve_public_file(response["id"], access))
    assert public_denied.value.status_code == 404
    private = run(files.serve_private_document(response["id"], {"id": "admin", "role": "admin"}, access))
    assert private.body.startswith(b"%PDF-") and private.headers["cache-control"] == "private, no-store"
    assert private.headers["content-disposition"] == "attachment"


def test_private_machine_request_document_uses_nested_company_scope(monkeypatch):
    database = AsyncDatabase("package12_private_machine_request")
    access = scoped(database, tenant="tenant-a")
    provider = MemoryStorage()
    monkeypatch.setattr(files, "get_storage_provider", lambda: provider)

    async def visible(*_args):
        return ["company-a"]

    monkeypatch.setattr(files, "visible_company_ids", visible)
    run(access.machine_requests.insert_one({
        "id": "request-a", "customer": {"companyId": "company-a"},
    }))
    upload = UploadFile(
        filename="request.pdf", file=io.BytesIO(b"%PDF-1.7\ncontent"),
        headers={"content-type": "application/pdf"},
    )
    response = run(files.upload_file(
        {"id": "admin", "role": "admin"}, access, upload,
        resource_type="machine_request", resource_id="request-a", visibility="private",
    ))
    document = run(files.serve_private_document(
        response["id"], {"id": "sales-a", "role": "sales"}, access,
    ))
    assert document.body.startswith(b"%PDF-")


def test_admin_can_bind_primary_product_image_and_archive_it(monkeypatch):
    database = AsyncDatabase("package12_product_image")
    access = scoped(database, tenant="tenant-a")
    provider = MemoryStorage()
    monkeypatch.setattr(files, "get_storage_provider", lambda: provider)
    run(access.products.insert_one({"id": "product-a", "name": "Product", "active": True}))
    upload = UploadFile(
        filename="product.png", file=io.BytesIO(PNG), headers={"content-type": "image/png"},
    )
    uploaded = run(files.upload_file(
        {"id": "admin", "role": "admin"}, access, upload,
        resource_type="product", resource_id="unassigned", visibility="public",
    ))
    updated = run(files.update_product_image(
        "product-a", uploaded["id"], files.ProductImageOrderIn(sortOrder=4, isPrimary=True),
        {"id": "admin", "role": "admin"}, access,
    ))
    assert updated["isPrimary"] is True and updated["sortOrder"] == 4
    assert updated["resourceId"] == "product-a"
    assert run(access.products.find_one({"id": "product-a"}))["imageUrl"] == uploaded["url"]
    assert [row["id"] for row in run(files.list_product_images("product-a", access))] == [uploaded["id"]]
    run(files.archive_file(uploaded["id"], {"id": "admin", "role": "admin"}, access))
    assert run(access.uploads.find_one({"id": uploaded["id"]}))["status"] == "archived"
    assert run(access.products.find_one({"id": "product-a"}))["imageUrl"] == ""
    assert provider.objects == {}


def test_public_file_association_requires_existing_same_type_resource(monkeypatch):
    database = AsyncDatabase("package12_file_association")
    access = scoped(database, tenant="tenant-a")
    provider = MemoryStorage()
    monkeypatch.setattr(files, "get_storage_provider", lambda: provider)
    run(access.machines.insert_one({"id": "machine-a", "name": "Machine"}))
    upload = UploadFile(
        filename="machine.png", file=io.BytesIO(PNG), headers={"content-type": "image/png"},
    )
    uploaded = run(files.upload_file(
        {"id": "admin", "role": "admin"}, access, upload,
        resource_type="machine", resource_id="unassigned", visibility="public",
    ))
    assert run(access.uploads.find_one({"id": uploaded["id"]}))["status"] == "staged"
    with pytest.raises(HTTPException) as not_public:
        run(files.serve_public_file(uploaded["id"], access))
    assert not_public.value.status_code == 404
    associated = run(files.associate_public_file(
        uploaded["id"], files.FileAssociationIn(resourceType="machine", resourceId="machine-a"),
        {"id": "admin", "role": "admin"}, access,
    ))
    assert associated["resourceId"] == "machine-a"
    with pytest.raises(HTTPException) as reassign:
        run(files.associate_public_file(
            uploaded["id"], files.FileAssociationIn(resourceType="product", resourceId="product-a"),
            {"id": "admin", "role": "admin"}, access,
        ))
    assert reassign.value.status_code == 409


def test_email_outbox_is_idempotent_and_provider_is_called_once(monkeypatch):
    database = AsyncDatabase("package12_email")
    prepare_delivery_indexes(database)
    access = scoped(database, tenant="tenant-a")
    sent = []

    class Provider:
        name = "test"
        def send(self, message):
            sent.append(message)
            return ProviderReceipt(message.message_id)

    monkeypatch.setattr("app.emailer.get_email_provider", lambda: Provider())
    message = dict(
        access=access, to="buyer@example.test", subject="Existing subject",
        html="<p>Existing body</p>", idempotency_key="order:1:created",
        template_key="order.created", resource_type="order", resource_id="1",
    )
    first = run(send_email(**message))
    replay = run(send_email(**message))
    assert first["id"] == replay["id"]
    assert database.raw.email_outbox.count_documents({}) == 1
    assert database.raw.background_jobs.count_documents({}) == 1
    run(deliver_outbox_email(access, first["id"], attempt=1))
    run(deliver_outbox_email(access, first["id"], attempt=2))
    assert len(sent) == 1
    delivered = database.raw.email_outbox.find_one({"id": first["id"]})
    assert delivered["status"] == "sent"
    assert "html" not in delivered and "attachmentFileIds" not in delivered


def test_email_configuration_and_template_locales_fail_safely(monkeypatch):
    for name in (
        "EMAIL_BACKEND", "SMTP_HOST", "SMTP_PORT", "SMTP_SECURITY", "SMTP_USERNAME",
        "SMTP_PASSWORD", "SMTP_FROM_EMAIL",
    ):
        monkeypatch.delenv(name, raising=False)
    assert email_configuration_status()[0] == "not_configured"
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "sender@example.test")
    monkeypatch.setenv("SMTP_USERNAME", "user")
    assert email_configuration_status()[0] == "degraded"
    monkeypatch.setenv("SMTP_PASSWORD", "test-password")
    monkeypatch.setenv("SMTP_PORT", "invalid")
    assert email_configuration_status()[0] == "degraded"
    assert "Inviato da" in email_shell("Titolo", "Intro", "<p>Corpo</p>", locale="it")
    assert "Sent by" in email_shell("Title", "Intro", "<p>Body</p>", locale="en")


def test_concurrent_email_delivery_claim_sends_only_once(monkeypatch):
    database = AsyncDatabase("package12_email_concurrency")
    prepare_delivery_indexes(database)
    access = scoped(database, tenant="tenant-a")
    sent = []

    class Provider:
        name = "test"
        def send(self, message):
            time.sleep(0.05)
            sent.append(message)
            return ProviderReceipt(message.message_id)

    monkeypatch.setattr("app.emailer.get_email_provider", lambda: Provider())
    row = run(send_email(
        access=access, to="buyer@example.test", subject="Subject", html="<p>Body</p>",
        idempotency_key="order:parallel:created", template_key="order.created",
    ))

    async def deliver_twice():
        return await asyncio.gather(
            deliver_outbox_email(access, row["id"], attempt=1),
            deliver_outbox_email(access, row["id"], attempt=1),
            return_exceptions=True,
        )

    results = run(deliver_twice())
    assert len(sent) == 1
    assert sum(isinstance(result, RuntimeError) for result in results) == 1
    assert database.raw.email_outbox.find_one({"id": row["id"]})["status"] == "sent"


def test_email_deduplication_and_jobs_are_tenant_isolated():
    database = AsyncDatabase("package12_email_tenants")
    prepare_delivery_indexes(database)
    tenant_a = scoped(database, tenant="tenant-a")
    tenant_b = scoped(database, tenant="tenant-b")
    args = dict(
        to="same@example.test", subject="Subject", html="<p>Body</p>",
        idempotency_key="offer:1:sent", template_key="offer.sent",
    )
    run(send_email(access=tenant_a, **args))
    run(send_email(access=tenant_b, **args))
    assert database.raw.email_outbox.count_documents({}) == 2
    assert run(tenant_a.email_outbox.count_documents({})) == 1
    assert run(tenant_b.email_outbox.count_documents({})) == 1


def test_two_workers_cannot_deliver_same_email_job():
    database = AsyncDatabase("package12_worker_claim")
    prepare_delivery_indexes(database)
    access = scoped(database)
    run(BackgroundJobQueue(access).enqueue(
        "email.deliver", actor_id="admin", idempotency_key="email:mail-1", payload={"outboxId": "mail-1"},
    ))
    first = run(BackgroundJobQueue(access).claim_next(worker_id="one"))
    second = run(BackgroundJobQueue(access).claim_next(worker_id="two"))
    assert first is not None and first.job_type == "email.deliver"
    assert second is None


def test_worker_once_recovers_pending_outbox_gap(monkeypatch):
    database = AsyncDatabase("package12_worker_gap")
    prepare_delivery_indexes(database)
    access = scoped(database)
    run(access.email_outbox.insert_one({
        "id": "mail-gap", "deduplicationKey": "gap", "recipient": "a@example.test",
        "subject": "Subject", "html": "<p>Body</p>", "status": "pending", "attempts": 0,
    }))
    sent = []
    class Provider:
        name = "test"
        def send(self, message):
            sent.append(message)
            return ProviderReceipt(message.message_id)
    monkeypatch.setattr("app.emailer.get_email_provider", lambda: Provider())
    assert run(run_worker_once(access, worker_id="worker")) is True
    assert len(sent) == 1
    assert database.raw.background_jobs.find_one({})["status"] == "completed"


def test_outbox_persistence_survives_initial_job_enqueue_failure(monkeypatch):
    database = AsyncDatabase("package12_enqueue_failure")
    prepare_delivery_indexes(database)
    access = scoped(database)

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("queue temporarily unavailable")

    monkeypatch.setattr(BackgroundJobQueue, "enqueue", unavailable)
    row = run(send_email(
        access=access, to="buyer@example.test", subject="Subject", html="<p>Body</p>",
        idempotency_key="order:enqueue-gap", template_key="order.created",
    ))
    assert row["status"] == "pending"
    assert database.raw.email_outbox.count_documents({"id": row["id"]}) == 1
    assert database.raw.background_jobs.count_documents({}) == 0


def test_notifications_are_recipient_and_tenant_scoped():
    database = AsyncDatabase("package12_notifications")
    tenant_a = scoped(database, tenant="tenant-a", actor="user-a")
    tenant_b = scoped(database, tenant="tenant-b", actor="user-a")
    row = run(create_notification(
        tenant_a, recipient_user_id="user-a", notification_type="system",
        title_key="Title", message_key="Message",
    ))
    own = run(notifications.list_notifications({"id": "user-a"}, tenant_a))
    assert [item["id"] for item in own] == [row["id"]]
    assert run(notifications.list_notifications({"id": "user-a"}, tenant_b)) == []
    with pytest.raises(HTTPException) as foreign:
        run(notifications.mark_notification_read(row["id"], {"id": "user-a"}, tenant_b))
    assert foreign.value.status_code == 404


def test_worker_heartbeat_and_operations_counts_do_not_expose_payloads():
    database = AsyncDatabase("package12_operations")
    access = scoped(database)
    run(record_worker_heartbeat(access, worker_id="worker-secret", status="active", processed=2))
    run(access.email_outbox.insert_one({"id": "mail-1", "status": "failed", "recipient": "private@example.test"}))
    result = run(operations.communication_status({"role": "admin"}, access))
    assert result["mail"]["failed"] == 1 and result["worker"]["status"] == "active"
    assert "private@example.test" not in repr(result)
    assert "worker-secret" not in repr(result)


def test_migration_12_is_additive_and_registered_after_11():
    database = mongomock.MongoClient().ordo_test_package12_migration
    plan = migration12.inspect(database)
    result = migration12.apply(database, type("Context", (), {"checkpoint": lambda self: None})())
    assert plan.expected_changes["documentsChanged"] == 0
    assert result == {"indexesEnsured": 8, "documentsChanged": 0}
    migration = next(row for row in get_migrations() if row.version == 12)
    assert migration.depends_on == (11,)
    assert all(database[name].count_documents({}) == 0 for name in database.list_collection_names())


def test_runtime_source_has_no_emergent_integration_dependency():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in app_dir.rglob("*.py")
        if "migrations" not in path.parts and "__pycache__" not in path.parts
    ).lower()
    assert "integrations.emergentagent.com" not in source
    assert "emergent_email_key" not in source
    assert "emergent_push_key" not in source
    assert "emergent_llm_key" not in source
