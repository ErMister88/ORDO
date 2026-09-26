"""Additive indexes for files, communication outbox, notifications and workers."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    ("uploads", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_file_id", {
        "unique": True, "partialFilterExpression": {"tenantId": {"$type": "string"}, "id": {"$type": "string"}},
    }),
    ("uploads", (("tenantId", ASCENDING), ("storageKey", ASCENDING)), "uniq_tenant_storage_key_v2", {
        "unique": True, "partialFilterExpression": {"tenantId": {"$type": "string"}, "storageKey": {"$type": "string"}},
    }),
    ("uploads", (("tenantId", ASCENDING), ("resourceType", ASCENDING), ("resourceId", ASCENDING), ("status", ASCENDING), ("sortOrder", ASCENDING)), "idx_tenant_resource_files", {}),
    ("email_outbox", (("tenantId", ASCENDING), ("deduplicationKey", ASCENDING)), "uniq_tenant_email_intent", {"unique": True}),
    ("email_outbox", (("tenantId", ASCENDING), ("status", ASCENDING), ("nextAttemptAt", ASCENDING)), "idx_tenant_email_due", {}),
    ("notifications", (("tenantId", ASCENDING), ("recipientUserId", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_user_notifications", {}),
    ("worker_heartbeats", (("tenantId", ASCENDING), ("workerId", ASCENDING)), "uniq_tenant_worker", {"unique": True}),
    ("worker_heartbeats", (("tenantId", ASCENDING), ("status", ASCENDING), ("lastSeenAt", DESCENDING)), "idx_tenant_worker_health", {}),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes and empty-on-demand technical collections are introduced",
            "Legacy upload documents remain unchanged and readable as migration metadata",
            "No email, notification, storage or worker action is executed by this migration",
        ),
        expected_changes={"indexesToEnsure": len(INDEXES), "documentsChanged": 0},
    )


def apply(database, context) -> dict:
    for collection, keys, name, options in INDEXES:
        context.checkpoint()
        database[collection].create_index(list(keys), name=name, **options)
    context.checkpoint()
    return {"indexesEnsured": len(INDEXES), "documentsChanged": 0}


MIGRATION = Migration(
    version=12,
    name="storage_communication_worker",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
