"""Add tenant-first indexes for financial operations without rewriting business data."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    ("invoices", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("currency", ASCENDING), ("status", ASCENDING), ("dueDate", ASCENDING)), "idx_tenant_receivables", {}),
    ("orders", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("currency", ASCENDING), ("status", ASCENDING)), "idx_tenant_credit_exposure", {}),
    ("accounting_syncs", (("tenantId", ASCENDING), ("resourceType", ASCENDING), ("resourceId", ASCENDING)), "uniq_tenant_accounting_resource", {"unique": True}),
    ("accounting_syncs", (("tenantId", ASCENDING), ("status", ASCENDING), ("updatedAt", ASCENDING)), "idx_tenant_accounting_status", {}),
    ("commission_agreements", (("tenantId", ASCENDING), ("salesRepId", ASCENDING), ("active", ASCENDING), ("companyId", ASCENDING), ("productId", ASCENDING)), "idx_tenant_commission_resolution", {}),
    ("commission_entries", (("tenantId", ASCENDING), ("eventKey", ASCENDING)), "uniq_tenant_commission_event", {"unique": True}),
    ("commission_entries", (("tenantId", ASCENDING), ("sourceEntryId", ASCENDING), ("status", ASCENDING)), "idx_tenant_commission_source", {"sparse": True}),
    ("commission_entries", (("tenantId", ASCENDING), ("salesRepId", ASCENDING), ("currency", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_sales_commission", {}),
    ("commission_settlements", (("tenantId", ASCENDING), ("idempotencyKey", ASCENDING)), "uniq_tenant_commission_settlement_key", {"unique": True}),
    ("commission_settlements", (("tenantId", ASCENDING), ("settlementLockKey", ASCENDING)), "uniq_tenant_open_commission_settlement", {
        "unique": True,
        "partialFilterExpression": {"settlementLockKey": {"$type": "string"}},
    }),
    ("commission_settlements", (("tenantId", ASCENDING), ("salesRepId", ASCENDING), ("currency", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_sales_settlement", {}),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive tenant-first indexes and empty financial collections are introduced",
            "No company, product, order, invoice, payment or pricing document is changed",
            "Legacy records remain untouched and are interpreted fail-closed at runtime",
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
    version=16,
    name="financial_operations",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
