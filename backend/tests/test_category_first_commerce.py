from __future__ import annotations

import os

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_category_first_commerce")
os.environ.setdefault("JWT_SECRET", "test-only-commerce-secret")
os.environ.setdefault("APP_ENV", "test")

import pytest
from fastapi import HTTPException

from app.models import CommerceAttributeIn, CommerceBundleIn, CommerceEntityIn, CommerceHomepageIn, ProductCategoryIn, ShopQuoteIn
from app.routers import commerce, products, shop
from app.migrations.registry import get_migrations
from app.migrations.versions import v0015_category_first_commerce as migration15
from test_tenant_business_access import AsyncDatabase, TENANT_A, TENANT_B, access, principal, run


def product(product_id="p1", **overrides):
    row = {
        "id": product_id, "slug": f"product-{product_id}", "name": "Aiello Classica",
        "brand": "Aiello", "brandId": "brand-a", "sku": "SKU-1", "ean": "800000000001",
        "categoryId": "cat-beans", "collectionIds": ["collection-new"],
        "active": True, "b2cAvailable": True, "b2cPrice": 5.0, "b2cPriceMinor": 500,
        "currency": "EUR", "taxRate": 7, "contentAmount": 250, "contentUnit": "g",
        "attributeValues": {"roast_level": "dark", "internal_grade": "secret"},
        "variants": [{"id": "v-ground", "name": "250 g gemahlen", "sku": "V-1", "active": True}],
        "b2cTiers": [], "availability": "available",
    }
    row.update(overrides)
    return row


def test_migration_15_is_additive_and_registered():
    assert get_migrations()[-1].version == 15
    plan = migration15.inspect(None)
    assert plan.expected_changes["documentsChanged"] == 0
    assert plan.expected_changes["indexesToEnsure"] == len(migration15.INDEXES)


def test_category_tree_rejects_cycles_and_cross_tenant_parent():
    database = AsyncDatabase("commerce_category_tree")
    scoped = access(database, TENANT_A)
    other = access(database, TENANT_B)
    run(scoped.product_categories.insert_one({"id": "root", "name": "Food", "slug": "food", "active": True}))
    run(scoped.product_categories.insert_one({"id": "child", "name": "Pasta", "slug": "pasta", "parentId": "root", "active": True}))
    run(other.product_categories.insert_one({"id": "foreign", "name": "Foreign", "slug": "foreign", "active": True}))
    with pytest.raises(HTTPException) as cycle:
        run(commerce.update_category_v2("root", ProductCategoryIn(name="Food", parentId="child"), principal(scoped), scoped))
    assert cycle.value.status_code == 400
    with pytest.raises(HTTPException) as foreign:
        run(commerce.create_category_v2(ProductCategoryIn(name="Hidden", parentId="foreign"), principal(scoped), scoped))
    assert foreign.value.status_code == 400
    with pytest.raises(HTTPException) as has_children:
        run(products.archive_product_category("root", principal(scoped), scoped))
    assert has_children.value.status_code == 409


def test_public_catalog_is_tenant_scoped_paginated_and_redacts_internal_attributes():
    database = AsyncDatabase("commerce_public_catalog")
    scoped = access(database, TENANT_A)
    other = access(database, TENANT_B)
    run(scoped.commerce_attributes.insert_one({"id": "a1", "key": "roast_level", "name": "Roast", "valueType": "select", "options": ["dark"], "public": True, "filterable": True, "active": True}))
    run(scoped.commerce_attributes.insert_one({"id": "a2", "key": "internal_grade", "name": "Internal", "valueType": "text", "public": False, "filterable": True, "active": True}))
    run(scoped.products.insert_one(product()))
    run(other.products.insert_one(product("foreign", sku="FOREIGN")))
    result = run(commerce.shop_catalog(scoped, q="Aiello", attribute=["roast_level:dark"], sort="relevance", page=1, pageSize=24))
    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == ["p1"]
    assert result["items"][0]["attributeValues"] == {"roast_level": "dark"}
    assert result["items"][0]["basePrice"] == {"priceMinor": 2000, "price": 20.0, "unit": "kg"}
    assert [item["key"] for item in result["filters"]] == ["roast_level"]
    assert "cost" not in result["items"][0]


@pytest.mark.parametrize(("amount", "unit", "price_minor", "expected"), [
    (1, "kg", 1890, {"priceMinor": 1890, "price": 18.9, "unit": "kg"}),
    (250, "g", 500, {"priceMinor": 2000, "price": 20.0, "unit": "kg"}),
    (750, "ml", 600, {"priceMinor": 800, "price": 8.0, "unit": "l"}),
    (1, "l", 725, {"priceMinor": 725, "price": 7.25, "unit": "l"}),
])
def test_base_price_uses_decimal_minor_units(amount, unit, price_minor, expected):
    assert commerce._base_price({"contentAmount": amount, "contentUnit": unit}, price_minor) == expected


def test_base_price_is_omitted_without_supported_positive_content():
    assert commerce._base_price({"contentAmount": 0, "contentUnit": "g"}, 500) is None
    assert commerce._base_price({"contentAmount": 1, "contentUnit": "piece"}, 500) is None


def test_legacy_product_without_slug_keeps_a_resolvable_public_url():
    database = AsyncDatabase("commerce_legacy_product_url")
    scoped = access(database, TENANT_A)
    legacy = product()
    legacy.pop("slug")
    legacy["variants"] = []
    run(scoped.products.insert_one(legacy))
    catalog = run(commerce.shop_catalog(scoped, sort="relevance", page=1, pageSize=24))
    assert catalog["items"][0]["slug"] == "p1"
    detail = run(commerce.shop_product_detail("p1", scoped))
    assert detail["id"] == "p1"
    assert detail["slug"] == "p1"


def test_category_catalog_exposes_only_attributes_assigned_to_its_tree():
    database = AsyncDatabase("commerce_category_filters")
    scoped = access(database, TENANT_A)
    run(scoped.product_categories.insert_one({"id": "coffee", "name": "Coffee", "slug": "coffee", "active": True}))
    run(scoped.product_categories.insert_one({"id": "beans", "name": "Beans", "slug": "beans", "parentId": "coffee", "active": True}))
    run(scoped.commerce_attributes.insert_one({"id": "roast", "key": "roast_level", "name": "Roast", "valueType": "select", "options": ["dark"], "categoryIds": ["coffee"], "public": True, "filterable": True, "active": True}))
    run(scoped.commerce_attributes.insert_one({"id": "power", "key": "power_kw", "name": "Power", "valueType": "number", "categoryIds": ["machines"], "public": True, "filterable": True, "active": True}))
    run(scoped.products.insert_one(product(categoryId="beans")))
    result = run(commerce.shop_catalog(scoped, category="coffee", sort="relevance", page=1, pageSize=24))
    assert [item["key"] for item in result["filters"]] == ["roast_level"]
    with pytest.raises(HTTPException) as denied:
        run(commerce.shop_catalog(scoped, category="coffee", attribute=["power_kw:4"], sort="relevance", page=1, pageSize=24))
    assert denied.value.status_code == 400


def test_search_autocomplete_is_bounded_and_grouped():
    database = AsyncDatabase("commerce_autocomplete")
    scoped = access(database, TENANT_A)
    run(scoped.business_brands.insert_one({"id": "brand-a", "name": "Aiello", "slug": "aiello", "active": True}))
    run(scoped.product_categories.insert_one({"id": "cat-a", "name": "Aiello Sets", "slug": "aiello-sets", "active": True}))
    for index in range(10):
        run(scoped.products.insert_one(product(f"p{index}", name=f"Aiello {index}", sku=f"AI-{index}")))
    result = run(commerce.search_suggest(scoped, "ai"))
    assert len(result["products"]) == 6
    assert result["brands"][0]["slug"] == "aiello"
    assert result["categories"][0]["slug"] == "aiello-sets"


def test_full_search_resolves_parent_category_brand_and_collection_names():
    database = AsyncDatabase("commerce_search_dimensions")
    scoped = access(database, TENANT_A)
    run(scoped.product_categories.insert_one({"id": "coffee", "name": "Coffee", "slug": "coffee", "active": True}))
    run(scoped.product_categories.insert_one({"id": "beans", "name": "Whole beans", "slug": "beans", "parentId": "coffee", "active": True}))
    run(scoped.business_brands.insert_one({"id": "brand-a", "name": "Aiello", "slug": "aiello", "active": True}))
    run(scoped.shop_collections.insert_one({"id": "gift", "name": "Gift ideas", "slug": "gift", "active": True}))
    run(scoped.products.insert_one(product(categoryId="beans", collectionIds=["gift"])))
    assert run(commerce.shop_catalog(scoped, q="Coffee", sort="relevance", page=1, pageSize=24))["total"] == 1
    assert run(commerce.shop_catalog(scoped, q="Aiello", sort="relevance", page=1, pageSize=24))["total"] == 1
    assert run(commerce.shop_catalog(scoped, q="Gift", sort="relevance", page=1, pageSize=24))["total"] == 1


def test_internal_attribute_cannot_be_used_as_public_filter():
    database = AsyncDatabase("commerce_filter_security")
    scoped = access(database, TENANT_A)
    run(scoped.commerce_attributes.insert_one({"id": "a1", "key": "internal_grade", "name": "Internal", "valueType": "text", "public": False, "filterable": True, "active": True}))
    with pytest.raises(HTTPException) as denied:
        run(commerce.shop_catalog(scoped, attribute=["internal_grade:secret"], sort="relevance", page=1, pageSize=24))
    assert denied.value.status_code == 400


def test_variant_is_validated_and_snapshotted_without_client_price():
    database = AsyncDatabase("commerce_variant_quote")
    scoped = access(database, TENANT_A)
    run(scoped.settings.insert_one({"key": "shop", "freeShippingThreshold": 50.0, "freeShippingThresholdMinor": 5000, "shippingFee": 5.0, "shippingFeeMinor": 500, "currency": "EUR", "subscriptionDiscountPercent": 5}))
    run(scoped.products.insert_one(product()))
    quote = run(shop._quote(ShopQuoteIn(items=[{"productId": "p1", "variantId": "v-ground", "qty": 2}]), scoped))
    assert quote.lines[0][1].final_unit_price_minor == 500
    with pytest.raises(HTTPException) as invalid:
        run(shop._quote(ShopQuoteIn(items=[{"productId": "p1", "variantId": "missing", "qty": 1}]), scoped))
    assert invalid.value.status_code == 409
    with pytest.raises(HTTPException) as missing:
        run(shop._quote(ShopQuoteIn(items=[{"productId": "p1", "qty": 1}]), scoped))
    assert missing.value.status_code == 409


def test_unavailable_product_and_variant_are_rejected_server_side():
    database = AsyncDatabase("commerce_unavailable_variant")
    scoped = access(database, TENANT_A)
    run(scoped.settings.insert_one({"key": "shop", "freeShippingThreshold": 50.0, "freeShippingThresholdMinor": 5000, "shippingFee": 5.0, "shippingFeeMinor": 500, "currency": "EUR"}))
    run(scoped.products.insert_one(product(variants=[{"id": "sold-out", "name": "Sold out", "availability": "unavailable", "active": True}])))
    with pytest.raises(HTTPException) as denied:
        run(shop._quote(ShopQuoteIn(items=[{"productId": "p1", "variantId": "sold-out", "qty": 1}]), scoped))
    assert denied.value.status_code == 409


def test_subscription_requires_explicitly_eligible_products_without_changing_price_order():
    database = AsyncDatabase("commerce_subscription_eligibility")
    scoped = access(database, TENANT_A)
    run(scoped.settings.insert_one({"key": "shop", "freeShippingThreshold": 50.0, "freeShippingThresholdMinor": 5000, "shippingFee": 5.0, "shippingFeeMinor": 500, "currency": "EUR", "subscriptionDiscountPercent": 10}))
    run(scoped.products.insert_one(product(subscriptionAllowed=False, variants=[])))
    with pytest.raises(HTTPException) as denied:
        run(shop._quote(ShopQuoteIn(items=[{"productId": "p1", "qty": 2}], subscription=True), scoped))
    assert denied.value.status_code == 409


def test_catalog_stays_bounded_with_five_thousand_products():
    database = AsyncDatabase("commerce_large_catalog")
    scoped = access(database, TENANT_A)
    rows = [{**product(f"p{index}"), "tenantId": TENANT_A, "name": f"Product {index:04d}", "sku": f"SKU-{index:04d}"} for index in range(5000)]
    database.raw.products.insert_many(rows)
    result = run(commerce.shop_catalog(scoped, q="Product", sort="name", page=100, pageSize=24))
    assert result["total"] == 5000
    assert result["pages"] == 209
    assert len(result["items"]) == 24


def test_admin_product_search_is_server_side_and_bounded():
    database = AsyncDatabase("commerce_admin_product_search")
    scoped = access(database, TENANT_A)
    database.raw.products.insert_many([
        {**product(f"p{index}"), "tenantId": TENANT_A, "name": f"Catalog product {index:04d}", "sku": f"SKU-{index:04d}"}
        for index in range(750)
    ])
    result = run(products.get_products(principal(scoped), scoped, limit=500, offset=0, q="SKU-0749"))
    assert [item["id"] for item in result] == ["p749"]


def test_admin_entities_are_tenant_scoped_and_slugged():
    database = AsyncDatabase("commerce_admin_entities")
    scoped = access(database, TENANT_A)
    other = access(database, TENANT_B)
    created = run(commerce.create_commerce_entity("regions", CommerceEntityIn(name="Sicília"), principal(scoped), scoped))
    assert created["slug"] == "sicilia"
    assert database.raw.commerce_regions.find_one({"id": created["id"]})["tenantId"] == TENANT_A
    assert run(other.commerce_regions.find_one({"id": created["id"]})) is None


def test_public_taxonomy_redacts_tenant_and_admin_metadata():
    row = {
        "id": "brand-a", "name": "Aiello", "slug": "aiello", "slugKey": f"{TENANT_A}:aiello",
        "tenantId": TENANT_A, "createdBy": "admin", "createdAt": "2026-01-01", "active": True,
    }
    public = commerce._public_entity(row)
    assert public == {"id": "brand-a", "name": "Aiello", "slug": "aiello", "active": True}


def test_attribute_keys_are_validated_and_unique_per_tenant():
    database = AsyncDatabase("commerce_attributes")
    scoped = access(database, TENANT_A)
    body = CommerceAttributeIn(name="Röstgrad", key="roast_level", valueType="select", options=["light", "dark"], filterable=True)
    created = run(commerce.create_attribute(body, principal(scoped), scoped))
    assert created["key"] == "roast_level"
    with pytest.raises(HTTPException):
        run(commerce.create_attribute(CommerceAttributeIn(name="Bad", key="bad.key", valueType="text"), principal(scoped), scoped))


def test_homepage_rejects_cross_tenant_targets():
    database = AsyncDatabase("commerce_homepage_scope")
    scoped = access(database, TENANT_A)
    other = access(database, TENANT_B)
    run(other.shop_collections.insert_one({"id": "foreign", "name": "Foreign", "slug": "foreign", "active": True}))
    with pytest.raises(HTTPException) as denied:
        run(commerce.put_homepage(CommerceHomepageIn(blocks=[{"type": "collection", "targetId": "foreign"}]), principal(scoped), scoped))
    assert denied.value.status_code == 400


def test_homepage_rejects_duplicate_block_ids():
    database = AsyncDatabase("commerce_homepage_block_ids")
    scoped = access(database, TENANT_A)
    body = CommerceHomepageIn(blocks=[
        {"id": "same", "type": "hero", "title": "One"},
        {"id": "same", "type": "text_image", "title": "Two"},
    ])
    with pytest.raises(HTTPException) as denied:
        run(commerce.put_homepage(body, principal(scoped), scoped))
    assert denied.value.status_code == 400


def test_bundle_variant_must_belong_to_its_tenant_product():
    database = AsyncDatabase("commerce_bundle_variant")
    scoped = access(database, TENANT_A)
    run(scoped.products.insert_one(product()))
    with pytest.raises(HTTPException) as denied:
        run(commerce.create_bundle(CommerceBundleIn(name="Set", items=[{"productId": "p1", "variantId": "foreign", "quantity": 1}]), principal(scoped), scoped))
    assert denied.value.status_code == 400
