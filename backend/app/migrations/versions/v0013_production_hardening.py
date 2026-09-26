"""Add query indexes for bounded tenant-scoped runtime paths."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    ("companies", (("tenantId", ASCENDING), ("name", ASCENDING), ("id", ASCENDING)), "idx_tenant_company_name", {}),
    ("orders", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("createdAt", DESCENDING), ("id", DESCENDING)), "idx_tenant_company_orders_recent", {}),
    ("offers", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("createdAt", DESCENDING), ("id", DESCENDING)), "idx_tenant_company_offers_recent", {}),
    ("invoices", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("date", DESCENDING), ("id", DESCENDING)), "idx_tenant_company_invoices_recent", {}),
    ("contracts", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("start", DESCENDING), ("id", DESCENDING)), "idx_tenant_company_contracts_recent", {}),
    ("subscriptions", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("createdAt", DESCENDING), ("id", DESCENDING)), "idx_tenant_company_subscriptions_recent", {}),
    ("products", (("tenantId", ASCENDING), ("active", ASCENDING), ("name", ASCENDING), ("id", ASCENDING)), "idx_tenant_product_active_name", {}),
    ("customer_prices", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("productId", ASCENDING), ("active", ASCENDING)), "idx_tenant_customer_price_lookup", {}),
    ("pricing_promotions", (("tenantId", ASCENDING), ("productId", ASCENDING), ("companyId", ASCENDING), ("active", ASCENDING), ("startsAt", DESCENDING)), "idx_tenant_promotion_lookup", {}),
    ("shop_orders", (("tenantId", ASCENDING), ("createdAt", DESCENDING), ("id", DESCENDING)), "idx_tenant_shop_orders_recent", {}),
    ("shop_orders", (("tenantId", ASCENDING), ("userId", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_shop_user_orders", {}),
    ("machine_requests", (("tenantId", ASCENDING), ("customer.companyId", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_machine_company_recent", {}),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes for observed runtime query patterns are introduced",
            "No business document is inserted, changed, deleted or backfilled",
            "TenantId remains the leading key for tenant-owned collections",
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
    version=13,
    name="production_hardening",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
