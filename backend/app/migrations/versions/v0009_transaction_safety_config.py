"""Add idempotency and tenant business-configuration indexes."""

from __future__ import annotations

from pymongo import ASCENDING

from ..models import Migration, MigrationPlan, checksum_file


def _partial(field: str) -> dict:
    # These operation/reference fields are only written as non-empty strings.
    # $exists keeps legacy rows without the field outside the unique index and
    # is supported consistently by MongoDB and the migration dry-run emulator.
    return {field: {"$exists": True}}


INDEXES = (
    ("idempotency_records", (("tenantId", ASCENDING), ("operation", ASCENDING), ("actorId", ASCENDING), ("key", ASCENDING)), "uniq_tenant_idempotency_scope", {"unique": True}),
    ("idempotency_records", (("expiresAt", ASCENDING),), "ttl_idempotency_records", {"expireAfterSeconds": 0}),
    ("idempotency_records", (("tenantId", ASCENDING), ("status", ASCENDING), ("updatedAt", ASCENDING)), "idx_tenant_operation_status", {}),
    # Source and operation IDs are globally generated today. A sparse single-field
    # unique index excludes every legacy row without the optional reference and
    # remains enforceable in both MongoDB and the isolated dry-run emulator.
    ("orders", (("operationId", ASCENDING),), "uniq_order_operation", {"unique": True, "sparse": True}),
    ("orders", (("fromOffer", ASCENDING),), "uniq_order_offer", {"unique": True, "sparse": True}),
    ("offers", (("operationId", ASCENDING),), "uniq_offer_operation", {"unique": True, "sparse": True}),
    ("orders", (("subscriptionRunKey", ASCENDING),), "uniq_subscription_run", {"unique": True, "sparse": True}),
    ("invoices", (("orderId", ASCENDING),), "uniq_invoice_order", {"unique": True, "sparse": True}),
    ("subscriptions", (("operationId", ASCENDING),), "uniq_subscription_operation", {"unique": True, "sparse": True}),
    ("shop_orders", (("operationId", ASCENDING),), "uniq_shop_order_operation", {"unique": True, "sparse": True}),
    ("machine_requests", (("operationId", ASCENDING),), "uniq_machine_request_operation", {"unique": True, "sparse": True}),
    ("equipment_requests", (("operationId", ASCENDING),), "uniq_equipment_request_operation", {"unique": True, "sparse": True}),
    ("price_approvals", (("operationId", ASCENDING),), "uniq_price_approval_operation", {"unique": True, "sparse": True}),
    ("price_history", (("operationId", ASCENDING),), "uniq_price_history_operation", {"unique": True, "sparse": True}),
    ("contracts", (("machineRequestId", ASCENDING),), "uniq_machine_request_contract", {"unique": True, "sparse": True}),
    ("products", (("tenantId", ASCENDING), ("brandId", ASCENDING), ("active", ASCENDING)), "idx_tenant_product_brand", {}),
    ("companies", (("tenantId", ASCENDING), ("customerTypeId", ASCENDING), ("active", ASCENDING)), "idx_tenant_company_type", {}),
    ("companies", (("tenantId", ASCENDING), ("customerTagIds", ASCENDING), ("active", ASCENDING)), "idx_tenant_company_tags", {}),
)

for _collection in ("business_brands", "customer_types", "customer_tags"):
    INDEXES += (
        (_collection, (("tenantId", ASCENDING), ("id", ASCENDING)), f"uniq_tenant_{_collection}_id", {"unique": True, "partialFilterExpression": _partial("id")}),
        (_collection, (("tenantId", ASCENDING), ("normalizedName", ASCENDING)), f"uniq_tenant_{_collection}_name", {"unique": True, "partialFilterExpression": _partial("normalizedName")}),
        (_collection, (("tenantId", ASCENDING), ("active", ASCENDING), ("sortOrder", ASCENDING)), f"idx_tenant_{_collection}_list", {}),
    )


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes and empty-on-demand collections are introduced",
            "No customer or product classifications are invented",
            "Existing business documents are not rewritten",
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
    version=9,
    name="transaction_safety_config",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
