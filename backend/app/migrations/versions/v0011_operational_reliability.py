"""Add indexes for tenant-scoped operations, recovery and diagnostics."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    (
        "background_jobs",
        (("tenantId", ASCENDING), ("jobType", ASCENDING), ("idempotencyKey", ASCENDING)),
        "uniq_tenant_job_intent",
        {"unique": True},
    ),
    (
        "background_jobs",
        (("tenantId", ASCENDING), ("status", ASCENDING), ("nextRetryAt", ASCENDING)),
        "idx_tenant_job_due",
        {},
    ),
    (
        "background_jobs",
        (("tenantId", ASCENDING), ("status", ASCENDING), ("leaseUntil", ASCENDING)),
        "idx_tenant_job_lease",
        {},
    ),
    (
        "reconciliation_runs",
        (("tenantId", ASCENDING), ("id", ASCENDING)),
        "uniq_tenant_reconciliation_run",
        {"unique": True},
    ),
    (
        "reconciliation_runs",
        (("tenantId", ASCENDING), ("finishedAt", DESCENDING)),
        "idx_tenant_reconciliation_recent",
        {},
    ),
    (
        "reconciliation_runs",
        (("tenantId", ASCENDING), ("status", ASCENDING), ("finishedAt", DESCENDING)),
        "idx_tenant_reconciliation_status",
        {},
    ),
    (
        "technical_errors",
        (("tenantId", ASCENDING), ("id", ASCENDING)),
        "uniq_tenant_technical_error",
        {"unique": True},
    ),
    (
        "technical_errors",
        (("tenantId", ASCENDING), ("occurredAt", DESCENDING)),
        "idx_tenant_technical_error_recent",
        {},
    ),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive operational indexes and empty-on-demand collections are introduced",
            "No business documents or retention deadlines are changed",
            "No job, reconciliation or recovery action is executed by the migration",
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
    version=11,
    name="operational_reliability",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
