"""Add tenant-scoped indexes for CRM and universal commerce foundations."""
from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    ("customer_activities", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_customer_activity_id", True),
    ("customer_activities", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("occurredAt", DESCENDING)), "idx_tenant_customer_activity_timeline", False),
    ("customer_tasks", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_customer_task_id", True),
    ("customer_tasks", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("status", ASCENDING), ("dueAt", ASCENDING)), "idx_tenant_customer_task_due", False),
    ("equipment_requests", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_equipment_request_id", True),
    ("equipment_requests", (("tenantId", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_equipment_request_queue", False),
    ("product_categories", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_product_category_id", True),
    ("product_categories", (("tenantId", ASCENDING), ("active", ASCENDING), ("name", ASCENDING)), "idx_tenant_product_category_list", False),
    ("price_approvals", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_price_approval_id", True),
    ("price_approvals", (("tenantId", ASCENDING), ("status", ASCENDING), ("createdAt", DESCENDING)), "idx_tenant_price_approval_queue", False),
    ("companies", (("tenantId", ASCENDING), ("assignedSalesRepId", ASCENDING), ("status", ASCENDING)), "idx_tenant_company_crm", False),
    ("products", (("tenantId", ASCENDING), ("categoryId", ASCENDING), ("active", ASCENDING)), "idx_tenant_product_category", False),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes are created",
            "No business document is inserted, changed, deleted or backfilled",
            "Unknown legacy CRM, category and payment values remain absent",
        ),
        expected_changes={"indexesToEnsure": len(INDEXES), "businessDocumentsChanged": 0},
    )


def apply(database, context) -> dict:
    for collection, keys, name, unique in INDEXES:
        context.checkpoint()
        options = {"name": name}
        if unique:
            options.update({
                "unique": True,
                "partialFilterExpression": {
                    "tenantId": {"$type": "string"},
                    "id": {"$type": "string"},
                },
            })
        database[collection].create_index(list(keys), **options)
    context.checkpoint()
    return {"indexesEnsured": len(INDEXES), "businessDocumentsChanged": 0}


MIGRATION = Migration(
    version=7,
    name="customer_commerce_foundation",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
