"""Add customer master-data and shop-collection indexes and update S&S shipping."""

from __future__ import annotations

from pymongo import ASCENDING

from ..models import Migration, MigrationPlan, checksum_file


SS_TENANT_ID = "tnt_ss_0001"
INDEXES = (
    ("customer_addresses", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_customer_address_id", True),
    ("customer_addresses", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("type", ASCENDING), ("active", ASCENDING)), "idx_tenant_customer_address_list", False),
    ("customer_contacts", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_customer_contact_id", True),
    ("customer_contacts", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("active", ASCENDING)), "idx_tenant_customer_contact_list", False),
    ("shop_collections", (("tenantId", ASCENDING), ("id", ASCENDING)), "uniq_tenant_shop_collection_id", True),
    ("shop_collections", (("tenantId", ASCENDING), ("name", ASCENDING)), "uniq_tenant_shop_collection_name", True),
    ("shop_collections", (("tenantId", ASCENDING), ("active", ASCENDING), ("sortOrder", ASCENDING)), "idx_tenant_shop_collection_list", False),
    ("products", (("tenantId", ASCENDING), ("collectionIds", ASCENDING), ("active", ASCENDING)), "idx_tenant_product_collections", False),
)


def _shipping_needs_update(database) -> bool:
    current = database["settings"].find_one({"tenantId": SS_TENANT_ID, "key": "shop"})
    if not current:
        return False
    return current.get("freeShippingThresholdMinor") != 5900 or current.get("shippingFeeMinor") != 0


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes are created",
            "No customer address, contact or collection data is invented",
            "Only the explicitly approved S&S shop shipping configuration may change",
        ),
        expected_changes={
            "indexesToEnsure": len(INDEXES),
            "shopSettingsToUpdate": 1 if _shipping_needs_update(database) else 0,
            "customerDocumentsChanged": 0,
        },
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
    result = database["settings"].update_one(
        {"tenantId": SS_TENANT_ID, "key": "shop"},
        {"$set": {
            "freeShippingThreshold": 59.0,
            "freeShippingThresholdMinor": 5900,
            "shippingFee": 0.0,
            "shippingFeeMinor": 0,
        }},
    )
    context.checkpoint()
    return {
        "indexesEnsured": len(INDEXES),
        "shopSettingsUpdated": result.modified_count,
        "customerDocumentsChanged": 0,
    }


MIGRATION = Migration(
    version=8,
    name="product_review_round2",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
