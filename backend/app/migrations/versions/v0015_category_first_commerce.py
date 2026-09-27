"""Add bounded indexes for the category-first commerce engine."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    ("product_categories", (("slugKey", ASCENDING),), "uniq_tenant_category_slug_key", {"unique": True, "sparse": True}),
    ("product_categories", (("tenantId", ASCENDING), ("parentId", ASCENDING), ("active", ASCENDING), ("sortOrder", ASCENDING)), "idx_tenant_category_tree", {}),
    ("business_brands", (("slugKey", ASCENDING),), "uniq_tenant_brand_slug_key", {"unique": True, "sparse": True}),
    ("shop_collections", (("slugKey", ASCENDING),), "uniq_tenant_collection_slug_key", {"unique": True, "sparse": True}),
    ("commerce_attributes", (("tenantId", ASCENDING), ("key", ASCENDING)), "uniq_tenant_attribute_key", {"unique": True}),
    ("commerce_attributes", (("tenantId", ASCENDING), ("categoryIds", ASCENDING), ("filterable", ASCENDING), ("active", ASCENDING)), "idx_tenant_attribute_filters", {}),
    ("commerce_regions", (("tenantId", ASCENDING), ("slug", ASCENDING)), "uniq_tenant_region_slug", {"unique": True}),
    ("commerce_shipping_classes", (("tenantId", ASCENDING), ("slug", ASCENDING)), "uniq_tenant_shipping_slug", {"unique": True}),
    ("commerce_bundles", (("tenantId", ASCENDING), ("slug", ASCENDING)), "uniq_tenant_bundle_slug", {"unique": True}),
    ("commerce_homepages", (("tenantId", ASCENDING), ("key", ASCENDING)), "uniq_tenant_homepage_key", {"unique": True}),
    ("products", (("tenantId", ASCENDING), ("active", ASCENDING), ("b2cAvailable", ASCENDING), ("categoryId", ASCENDING), ("name", ASCENDING), ("id", ASCENDING)), "idx_tenant_shop_category", {}),
    ("products", (("tenantId", ASCENDING), ("brandId", ASCENDING), ("active", ASCENDING), ("name", ASCENDING)), "idx_tenant_shop_brand", {}),
    ("products", (("tenantId", ASCENDING), ("collectionIds", ASCENDING), ("active", ASCENDING), ("name", ASCENDING)), "idx_tenant_shop_collection", {}),
    ("products", (("tenantId", ASCENDING), ("regionIds", ASCENDING), ("active", ASCENDING), ("name", ASCENDING)), "idx_tenant_shop_region", {}),
    ("products", (("slugKey", ASCENDING),), "uniq_tenant_product_slug_key", {"unique": True, "sparse": True}),
    ("products", (("tenantId", ASCENDING), ("sku", ASCENDING)), "idx_tenant_product_sku", {"sparse": True}),
    ("products", (("tenantId", ASCENDING), ("ean", ASCENDING)), "idx_tenant_product_ean", {"sparse": True}),
    ("products", (("tenantId", ASCENDING), ("variants.sku", ASCENDING)), "idx_tenant_variant_sku", {"sparse": True}),
    ("products", (("tenantId", ASCENDING), ("variants.ean", ASCENDING)), "idx_tenant_variant_ean", {"sparse": True}),
    ("products", (("tenantId", ASCENDING), ("active", ASCENDING), ("b2cAvailable", ASCENDING), ("b2cPriceMinor", ASCENDING), ("id", ASCENDING)), "idx_tenant_shop_price", {}),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes and empty commerce collections are introduced",
            "No existing product, collection, order or price document is changed",
            "Legacy products without a category remain available through curated/search discovery",
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
    version=15,
    name="category_first_commerce",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
