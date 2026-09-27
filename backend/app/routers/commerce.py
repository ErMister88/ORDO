"""Tenant-safe category-first commerce discovery and administration."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import secrets
import unicodedata
from typing import Annotated, Any, Literal

from fastapi import Depends, HTTPException, Query
from pymongo.errors import DuplicateKeyError

from ..audit_service import tenant_audit
from ..core import api_router, strip_id
from ..deps import public_tenant_business_access, require_roles, tenant_business_access
from ..models import (
    CommerceAttributeIn, CommerceBundleIn, CommerceEntityIn,
    CommerceHomepageIn, ProductCategoryIn,
)
from ..money import from_minor
from ..pricing_engine import PricingEngine, PricingError
from ..tenant_access import TenantBusinessAccess, TenantScopedCollection


RESOURCE_MAP = {
    "brands": ("business_brands", "brand"),
    "collections": ("shop_collections", "collection"),
    "regions": ("commerce_regions", "region"),
    "shipping-classes": ("commerce_shipping_classes", "shipping"),
}
ATTRIBUTE_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PUBLIC_PRODUCT_FIELDS = {
    "id", "slug", "sku", "ean", "brand", "brandId", "name", "categoryId",
    "collectionIds", "unit", "packagingUnit", "packageQuantity", "contentAmount",
    "contentUnit", "minimumOrderQuantity", "directPurchaseAllowed",
    "financingRequestAllowed", "imageUrl", "images", "description", "taxRate",
    "availability", "quickAdd", "subscriptionAllowed",
    "subscriptionIntervals", "variants", "attributeValues", "regionIds",
    "foodInfo", "seoTitle", "seoDescription",
}


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    result = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    if not result or len(result) > 200 or not SLUG.fullmatch(result):
        raise HTTPException(status_code=400, detail="Ungültiger Slug")
    return result


def _entity_payload(body: CommerceEntityIn, tenant_id: str) -> dict[str, Any]:
    name = body.name.strip()
    return {
        "name": name, "normalizedName": name.casefold(),
        "slug": _slug(body.slug or name),
        "slugKey": f"{tenant_id}:{_slug(body.slug or name)}",
        "description": body.description.strip(), "imageUrl": body.imageUrl.strip(),
        "seoTitle": body.seoTitle.strip(), "seoDescription": body.seoDescription.strip(),
        "sortOrder": body.sortOrder, "active": body.active,
    }


def _collection(access: TenantBusinessAccess, resource: str) -> tuple[TenantScopedCollection, str]:
    mapped = RESOURCE_MAP.get(resource)
    if not mapped:
        raise HTTPException(status_code=404, detail="Commerce-Bereich nicht gefunden")
    name, prefix = mapped
    return getattr(access, name), prefix


async def _unique_slug(collection: TenantScopedCollection, slug: str, entity_id: str | None = None) -> None:
    existing = await collection.find_one({"slug": slug})
    if existing and existing.get("id") != entity_id:
        raise HTTPException(status_code=409, detail="Slug ist bereits vergeben")


def _public_entity(row: dict) -> dict:
    return {key: value for key, value in strip_id(row).items() if key not in {
        "createdBy", "updatedBy", "normalizedName", "tenantId", "slugKey",
        "internalNotes", "createdAt", "updatedAt", "archivedAt",
    }}


def _base_price(product: dict, unit_price_minor: int) -> dict | None:
    units = {
        "g": (Decimal("1000"), "kg"), "kg": (Decimal("1"), "kg"),
        "ml": (Decimal("1000"), "l"), "l": (Decimal("1"), "l"),
    }
    unit = str(product.get("contentUnit") or "").strip().casefold()
    if unit not in units or product.get("contentAmount") is None:
        return None
    try:
        amount = Decimal(str(product["contentAmount"]))
    except (InvalidOperation, ValueError):
        return None
    if amount <= 0:
        return None
    factor, display_unit = units[unit]
    minor = int((Decimal(unit_price_minor) * factor / amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return {"priceMinor": minor, "price": from_minor(minor), "unit": display_unit}


def _public_variant(variant: Any) -> dict | None:
    if not isinstance(variant, dict) or not isinstance(variant.get("id"), str):
        return None
    if variant.get("active") is False:
        return None
    allowed = {"id", "name", "sku", "ean", "weight", "unit", "availability", "imageUrls", "attributeValues"}
    return {key: value for key, value in variant.items() if key in allowed}


def _public_product(product: dict, quote, *, detail: bool = False) -> dict:
    public = {key: product[key] for key in PUBLIC_PRODUCT_FIELDS if key in product}
    public.update({
        "slug": product.get("slug") or product["id"],
        "b2cPrice": quote.public()["baseUnitPrice"],
        "b2cPriceMinor": quote.base_unit_price_minor,
        "currency": quote.currency,
        "taxRate": quote.tax_rate,
        "b2cTiers": [
            {"minQty": float(tier["minQty"]), "priceMinor": int(tier["priceMinor"]), "price": from_minor(int(tier["priceMinor"]))}
            for tier in product.get("b2cTiers", [])
            if isinstance(tier, dict) and "minQty" in tier and isinstance(tier.get("priceMinor"), int) and tier["priceMinor"] > 0
        ],
        "basePrice": _base_price(product, quote.base_unit_price_minor),
        "variants": [value for variant in product.get("variants", []) if (value := _public_variant(variant))],
    })
    if isinstance(product.get("stock"), (int, float)) and product["stock"] <= 0:
        public["availability"] = "unavailable"
    if public.get("variants") or public.get("directPurchaseAllowed") is False:
        public["quickAdd"] = False
    if not detail:
        public.pop("foodInfo", None)
        public.pop("relatedProductIds", None)
        public.pop("recommendedProductIds", None)
        public.pop("compatibleProductIds", None)
    return public


async def _quoted_products(access: TenantBusinessAccess, query: dict, *, sort: str, page: int, page_size: int):
    sorts = {
        "relevance": [("sortOrder", 1), ("name", 1), ("id", 1)],
        "newest": [("createdAt", -1), ("id", 1)],
        "price_asc": [("b2cPriceMinor", 1), ("id", 1)],
        "price_desc": [("b2cPriceMinor", -1), ("id", 1)],
        "name": [("name", 1), ("id", 1)],
    }
    if sort not in sorts:
        raise HTTPException(status_code=400, detail="Ungültige Sortierung")
    total = await access.products.count_documents(query)
    rows = await access.products.find(query).sort(sorts[sort]).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    engine = PricingEngine(access)
    products = []
    for row in rows:
        try:
            products.append(_public_product(row, engine.quote_b2c_product(row, 1)))
        except PricingError as exc:
            raise HTTPException(status_code=409, detail="Produktpreis ist nicht verfügbar") from exc
    public_attributes = await access.commerce_attributes.find({"public": {"$ne": False}, "active": {"$ne": False}}).to_list(1000)
    public_keys = {row.get("key") for row in public_attributes}
    for product in products:
        product["attributeValues"] = {
            key: value for key, value in (product.get("attributeValues") or {}).items()
            if key in public_keys
        }
        for variant in product.get("variants", []):
            variant["attributeValues"] = {
                key: value for key, value in (variant.get("attributeValues") or {}).items()
                if key in public_keys
            }
    return {"items": products, "page": page, "pageSize": page_size, "total": total,
            "pages": (total + page_size - 1) // page_size}


async def _descendant_ids(access: TenantBusinessAccess, root_id: str) -> list[str]:
    rows = await access.product_categories.find({"active": {"$ne": False}}).to_list(2000)
    children: dict[str, list[str]] = {}
    for row in rows:
        parent = row.get("parentId")
        if isinstance(parent, str):
            children.setdefault(parent, []).append(row["id"])
    result, stack = [], [root_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.append(current)
        stack.extend(children.get(current, []))
    return result


@api_router.get("/shop/discovery")
async def shop_discovery(access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)]):
    categories = await access.product_categories.find({"active": {"$ne": False}}).sort([("sortOrder", 1), ("name", 1)]).to_list(1000)
    brands = await access.business_brands.find({"active": {"$ne": False}}).sort([("sortOrder", 1), ("name", 1)]).to_list(500)
    collections = await access.shop_collections.find({"active": {"$ne": False}}).sort([("sortOrder", 1), ("name", 1)]).to_list(500)
    regions = await access.commerce_regions.find({"active": {"$ne": False}}).sort([("sortOrder", 1), ("name", 1)]).to_list(500)
    homepage = await access.commerce_homepages.find_one({"key": "shop"})
    blocks = sorted((homepage or {}).get("blocks", []), key=lambda row: (row.get("sortOrder", 0), row.get("id", "")))
    visible_blocks = [dict(row) for row in blocks if row.get("active") is not False]
    curated_ids = list(dict.fromkeys(product_id for block in visible_blocks for product_id in block.get("productIds", []) if isinstance(product_id, str)))[:200]
    curated_rows = await access.products.find({"id": {"$in": curated_ids}, "active": True, "b2cAvailable": {"$ne": False}}).to_list(len(curated_ids)) if curated_ids else []
    engine = PricingEngine(access)
    curated = {}
    for row in curated_rows:
        try:
            public_product = _public_product(row, engine.quote_b2c_product(row, 1))
            public_product.pop("attributeValues", None)
            curated[row["id"]] = public_product
        except PricingError:
            continue
    for block in visible_blocks:
        block["products"] = [curated[product_id] for product_id in block.get("productIds", []) if product_id in curated]
    return {
        "categories": [_public_entity(row) for row in categories],
        "brands": [_public_entity(row) for row in brands],
        "collections": [_public_entity(row) for row in collections],
        "regions": [_public_entity(row) for row in regions],
        "blocks": visible_blocks,
    }


@api_router.get("/shop/catalog")
async def shop_catalog(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    q: Annotated[str, Query(max_length=200)] = "",
    category: str | None = None, brand: str | None = None,
    collection: str | None = None, region: str | None = None,
    availability: Literal["available", "unavailable", "preorder"] | None = None,
    minPrice: Annotated[int | None, Query(ge=0)] = None,
    maxPrice: Annotated[int | None, Query(ge=0)] = None,
    attribute: Annotated[list[str] | None, Query()] = None,
    sort: Literal["relevance", "newest", "price_asc", "price_desc", "name"] = "relevance",
    page: Annotated[int, Query(ge=1, le=100_000)] = 1,
    pageSize: Annotated[int, Query(ge=1, le=60)] = 24,
):
    query: dict[str, Any] = {"active": True, "b2cAvailable": {"$ne": False}, "b2cPriceMinor": {"$gt": 0}}
    relevant_filter_scope: list[dict[str, Any]] | None = None
    terms = [term for term in re.split(r"\s+", q.strip()) if term][:8]
    if terms:
        searchable_attributes = await access.commerce_attributes.find({"searchable": True, "public": {"$ne": False}, "active": {"$ne": False}}).to_list(100)
        search_fields = ["name", "brand", "sku", "ean", "description", "searchKeywords"] + [f"attributeValues.{row['key']}" for row in searchable_attributes if ATTRIBUTE_KEY.fullmatch(str(row.get("key", "")))]
        search_categories = await access.product_categories.find({"active": {"$ne": False}}).to_list(2000)
        search_brands = await access.business_brands.find({"active": {"$ne": False}}).to_list(500)
        search_collections = await access.shop_collections.find({"active": {"$ne": False}}).to_list(500)
        children: dict[str, list[str]] = {}
        for category_row in search_categories:
            if isinstance(category_row.get("parentId"), str):
                children.setdefault(category_row["parentId"], []).append(category_row["id"])

        def category_tree_ids(root_ids: list[str]) -> list[str]:
            found, stack = set(), list(root_ids)
            while stack:
                category_id = stack.pop()
                if category_id in found:
                    continue
                found.add(category_id)
                stack.extend(children.get(category_id, []))
            return list(found)

        term_queries = []
        for term in terms:
            folded = term.casefold()
            expression = {"$regex": re.escape(term), "$options": "i"}
            matching_categories = category_tree_ids([
                row["id"] for row in search_categories
                if folded in str(row.get("name", "")).casefold()
            ])
            matching_brands = [row["id"] for row in search_brands if folded in str(row.get("name", "")).casefold()]
            matching_collections = [row["id"] for row in search_collections if folded in str(row.get("name", "")).casefold()]
            choices = [{field: expression} for field in search_fields]
            if matching_categories:
                choices.append({"categoryId": {"$in": matching_categories}})
            if matching_brands:
                choices.append({"brandId": {"$in": matching_brands}})
            if matching_collections:
                choices.append({"collectionIds": {"$in": matching_collections}})
            term_queries.append({"$or": choices})
        query["$and"] = term_queries
    if category:
        row = await access.product_categories.find_one({"$or": [{"id": category}, {"slug": category}], "active": {"$ne": False}})
        if not row:
            raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
        category_ids = await _descendant_ids(access, row["id"])
        query["categoryId"] = {"$in": category_ids}
        relevant_filter_scope = [{"categoryIds": {"$in": category_ids}}, {"categoryIds": []}]
    if brand:
        row = await access.business_brands.find_one({"$or": [{"id": brand}, {"slug": brand}], "active": {"$ne": False}})
        if not row:
            raise HTTPException(status_code=404, detail="Marke nicht gefunden")
        query["brandId"] = row["id"]
    if collection:
        row = await access.shop_collections.find_one({"$or": [{"id": collection}, {"slug": collection}], "active": {"$ne": False}})
        if not row:
            raise HTTPException(status_code=404, detail="Collection nicht gefunden")
        query["collectionIds"] = row["id"]
    if region:
        row = await access.commerce_regions.find_one({"$or": [{"id": region}, {"slug": region}], "active": {"$ne": False}})
        if not row:
            raise HTTPException(status_code=404, detail="Region nicht gefunden")
        query["regionIds"] = row["id"]
    if availability:
        query["availability"] = availability
    if minPrice is not None or maxPrice is not None:
        query["b2cPriceMinor"] = {"$gt": 0, **({"$gte": minPrice} if minPrice is not None else {}), **({"$lte": maxPrice} if maxPrice is not None else {})}
    for raw in attribute or []:
        key, separator, value = raw.partition(":")
        if not separator or not ATTRIBUTE_KEY.fullmatch(key) or len(value) > 200:
            raise HTTPException(status_code=400, detail="Ungültiger Attributfilter")
        definition_query: dict[str, Any] = {"key": key, "filterable": True, "public": {"$ne": False}, "active": {"$ne": False}}
        if relevant_filter_scope is not None:
            definition_query["$or"] = relevant_filter_scope
        definition = await access.commerce_attributes.find_one(definition_query)
        if not definition:
            raise HTTPException(status_code=400, detail="Attribut ist nicht filterbar")
        if definition.get("valueType") == "number":
            try:
                typed_value: Any = float(value)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail="Ungültiger Zahlenfilter") from exc
        elif definition.get("valueType") == "boolean":
            if value.casefold() not in {"true", "false"}:
                raise HTTPException(status_code=400, detail="Ungültiger Ja/Nein-Filter")
            typed_value = value.casefold() == "true"
        else:
            typed_value = value
            if definition.get("valueType") in {"select", "multi_select"} and value not in definition.get("options", []):
                raise HTTPException(status_code=400, detail="Ungültige Filteroption")
        query[f"attributeValues.{key}"] = typed_value
    result = await _quoted_products(access, query, sort=sort, page=page, page_size=pageSize)
    filter_query: dict[str, Any] = {"filterable": True, "public": {"$ne": False}, "active": {"$ne": False}}
    if relevant_filter_scope is not None:
        filter_query["$or"] = relevant_filter_scope
    result["filters"] = [_public_entity(row) for row in await access.commerce_attributes.find(filter_query).sort("sortOrder", 1).to_list(500)]
    return result


@api_router.get("/shop/search/suggest")
async def search_suggest(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    q: Annotated[str, Query(min_length=1, max_length=100)],
):
    term = q.strip()
    if not term:
        return {"products": [], "brands": [], "categories": []}
    expression = {"$regex": f"^{re.escape(term)}", "$options": "i"}
    products = await access.products.find({"active": True, "b2cAvailable": {"$ne": False}, "$or": [
        {"name": expression}, {"brand": expression}, {"sku": expression}, {"ean": expression},
    ]}).sort("name", 1).limit(6).to_list(6)
    brands = await access.business_brands.find({"active": {"$ne": False}, "name": expression}).sort("name", 1).limit(4).to_list(4)
    categories = await access.product_categories.find({"active": {"$ne": False}, "name": expression}).sort("name", 1).limit(4).to_list(4)
    return {
        "products": [{"id": row["id"], "slug": row.get("slug") or row["id"], "name": row["name"], "brand": row.get("brand", ""), "imageUrl": row.get("imageUrl", "")} for row in products],
        "brands": [_public_entity(row) for row in brands],
        "categories": [_public_entity(row) for row in categories],
    }


@api_router.get("/shop/product/{reference}")
async def shop_product_detail(reference: str, access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)]):
    product = await access.products.find_one({"$or": [{"id": reference}, {"slug": reference}], "active": True, "b2cAvailable": {"$ne": False}})
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    try:
        public = _public_product(product, PricingEngine(access).quote_b2c_product(product, 1), detail=True)
    except PricingError as exc:
        raise HTTPException(status_code=409, detail="Produktpreis ist nicht verfügbar") from exc
    public_attributes = await access.commerce_attributes.find({"public": {"$ne": False}, "active": {"$ne": False}}).to_list(1000)
    public_definitions = {row.get("key"): row for row in public_attributes}
    public_keys = set(public_definitions)
    public["attributeValues"] = {key: value for key, value in (public.get("attributeValues") or {}).items() if key in public_keys}
    public["attributes"] = [
        {"key": key, "name": public_definitions[key].get("name", key), "value": value}
        for key, value in public["attributeValues"].items()
    ]
    for variant in public.get("variants", []):
        variant["attributeValues"] = {key: value for key, value in (variant.get("attributeValues") or {}).items() if key in public_keys}
    media = await access.uploads.find({
        "resourceType": "product", "resourceId": product["id"],
        "visibility": "public", "status": "active",
    }).sort([("isPrimary", -1), ("sortOrder", 1), ("createdAt", 1)]).to_list(100)
    if media:
        public["images"] = [
            {"id": row["id"], "url": f"/api/files/{row['id']}", "sortOrder": row.get("sortOrder", 0), "isPrimary": bool(row.get("isPrimary"))}
            for row in media
        ]
    relation_ids = list(dict.fromkeys(product.get("relatedProductIds", []) + product.get("recommendedProductIds", []) + product.get("compatibleProductIds", [])))[:30]
    related = await access.products.find({"id": {"$in": relation_ids}, "active": True, "b2cAvailable": {"$ne": False}}).to_list(30) if relation_ids else []
    engine = PricingEngine(access)
    try:
        public["relatedProducts"] = [_public_product(row, engine.quote_b2c_product(row, 1)) for row in related]
    except PricingError as exc:
        raise HTTPException(status_code=409, detail="Produktpreis ist nicht verfügbar") from exc
    category = await access.product_categories.find_one({"id": product.get("categoryId"), "active": {"$ne": False}}) if product.get("categoryId") else None
    trail = []
    seen = set()
    while category and category.get("id") not in seen:
        seen.add(category["id"])
        trail.append({"id": category["id"], "name": category["name"], "slug": category.get("slug") or category["id"]})
        category = await access.product_categories.find_one({"id": category.get("parentId"), "active": {"$ne": False}}) if category.get("parentId") else None
    public["breadcrumbs"] = list(reversed(trail))
    return public


@api_router.get("/commerce/entities/{resource}")
async def list_commerce_entities(
    resource: Literal["brands", "collections", "regions", "shipping-classes"],
    _user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    collection, _ = _collection(access, resource)
    return [strip_id(row) for row in await collection.find({}).sort([("sortOrder", 1), ("name", 1)]).to_list(2000)]


@api_router.post("/commerce/entities/{resource}", status_code=201)
async def create_commerce_entity(
    resource: Literal["brands", "collections", "regions", "shipping-classes"], body: CommerceEntityIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    collection, prefix = _collection(access, resource)
    payload = _entity_payload(body, access.context.tenant_id)
    await _unique_slug(collection, payload["slug"])
    row = {"id": f"{prefix}-{secrets.token_hex(6)}", **payload, "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    try:
        await collection.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Eintrag existiert bereits") from exc
    await tenant_audit(access, user, "commerce.entity.create", row["id"], {"resource": resource, "name": row["name"]})
    return strip_id(row)


@api_router.put("/commerce/entities/{resource}/{entity_id}")
async def update_commerce_entity(
    resource: Literal["brands", "collections", "regions", "shipping-classes"], entity_id: str, body: CommerceEntityIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    collection, _ = _collection(access, resource)
    payload = _entity_payload(body, access.context.tenant_id)
    await _unique_slug(collection, payload["slug"], entity_id)
    result = await collection.update_one({"id": entity_id}, {"$set": {**payload, "updatedAt": datetime.now(timezone.utc).isoformat(), "updatedBy": user["id"]}})
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    await tenant_audit(access, user, "commerce.entity.update", entity_id, {"resource": resource, "name": payload["name"]})
    return strip_id(await collection.find_one({"id": entity_id}))


@api_router.delete("/commerce/entities/{resource}/{entity_id}")
async def archive_commerce_entity(
    resource: Literal["brands", "collections", "regions", "shipping-classes"], entity_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    collection, _ = _collection(access, resource)
    result = await collection.update_one({"id": entity_id}, {"$set": {"active": False, "archivedAt": datetime.now(timezone.utc).isoformat()}})
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    await tenant_audit(access, user, "commerce.entity.archive", entity_id, {"resource": resource})
    return {"ok": True, "archived": True}


def _category_payload(body: ProductCategoryIn, tenant_id: str) -> dict:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Kategoriename ist erforderlich")
    slug = _slug(body.slug or name)
    return {**body.model_dump(), "name": name, "normalizedName": name.casefold(), "slug": slug, "slugKey": f"{tenant_id}:{slug}"}


async def _validate_category_parent(access: TenantBusinessAccess, parent_id: str | None, category_id: str | None) -> None:
    if not parent_id:
        return
    if parent_id == category_id:
        raise HTTPException(status_code=400, detail="Kategorie kann nicht ihr eigener Elternknoten sein")
    current, seen = parent_id, set()
    while current:
        if current in seen or current == category_id:
            raise HTTPException(status_code=400, detail="Kategoriehierarchie enthält einen Zyklus")
        seen.add(current)
        row = await access.product_categories.find_one({"id": current})
        if not row:
            raise HTTPException(status_code=400, detail="Übergeordnete Kategorie nicht gefunden")
        current = row.get("parentId")


@api_router.post("/commerce/categories", status_code=201)
async def create_category_v2(body: ProductCategoryIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    payload = _category_payload(body, access.context.tenant_id)
    await _unique_slug(access.product_categories, payload["slug"])
    await _validate_category_parent(access, body.parentId, None)
    row = {"id": "cat-" + secrets.token_hex(6), **payload, "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    await access.product_categories.insert_one(row)
    await tenant_audit(access, user, "commerce.category.create", row["id"], {"name": row["name"], "parentId": row.get("parentId")})
    return strip_id(row)


@api_router.put("/commerce/categories/{category_id}")
async def update_category_v2(category_id: str, body: ProductCategoryIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    payload = _category_payload(body, access.context.tenant_id)
    await _unique_slug(access.product_categories, payload["slug"], category_id)
    await _validate_category_parent(access, body.parentId, category_id)
    result = await access.product_categories.update_one({"id": category_id}, {"$set": payload})
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    await tenant_audit(access, user, "commerce.category.update", category_id, {"name": payload["name"], "parentId": payload.get("parentId")})
    return strip_id(await access.product_categories.find_one({"id": category_id}))


@api_router.get("/commerce/attributes")
async def list_attributes(_user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    return [strip_id(row) for row in await access.commerce_attributes.find({}).sort([("sortOrder", 1), ("name", 1)]).to_list(2000)]


@api_router.post("/commerce/attributes", status_code=201)
async def create_attribute(body: CommerceAttributeIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    key = body.key.strip().casefold()
    if not ATTRIBUTE_KEY.fullmatch(key):
        raise HTTPException(status_code=400, detail="Attributschlüssel muss mit einem Buchstaben beginnen und darf nur a-z, 0-9 und _ enthalten")
    if body.valueType in {"select", "multi_select"} and not body.options:
        raise HTTPException(status_code=400, detail="Auswahlattribute benötigen Optionen")
    for category_id in body.categoryIds:
        if not await access.product_categories.find_one({"id": category_id}):
            raise HTTPException(status_code=400, detail="Kategorie nicht gefunden")
    row = {"id": "attribute-" + secrets.token_hex(6), **body.model_dump(), **_entity_payload(body, access.context.tenant_id), "key": key,
           "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    try:
        await access.commerce_attributes.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Attributschlüssel existiert bereits") from exc
    await tenant_audit(access, user, "commerce.attribute.create", row["id"], {"key": key, "name": row["name"]})
    return strip_id(row)


@api_router.put("/commerce/attributes/{attribute_id}")
async def update_attribute(attribute_id: str, body: CommerceAttributeIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    key = body.key.strip().casefold()
    if not ATTRIBUTE_KEY.fullmatch(key):
        raise HTTPException(status_code=400, detail="Ungültiger Attributschlüssel")
    duplicate = await access.commerce_attributes.find_one({"key": key})
    if duplicate and duplicate.get("id") != attribute_id:
        raise HTTPException(status_code=409, detail="Attributschlüssel existiert bereits")
    if body.valueType in {"select", "multi_select"} and not body.options:
        raise HTTPException(status_code=400, detail="Auswahlattribute benötigen Optionen")
    for category_id in body.categoryIds:
        if not await access.product_categories.find_one({"id": category_id}):
            raise HTTPException(status_code=400, detail="Kategorie nicht gefunden")
    payload = {**body.model_dump(), **_entity_payload(body, access.context.tenant_id), "key": key}
    result = await access.commerce_attributes.update_one({"id": attribute_id}, {"$set": payload})
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Attribut nicht gefunden")
    await tenant_audit(access, user, "commerce.attribute.update", attribute_id, {"key": key, "name": payload["name"]})
    return strip_id(await access.commerce_attributes.find_one({"id": attribute_id}))


@api_router.get("/commerce/bundles")
async def list_bundles(_user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    return [strip_id(row) for row in await access.commerce_bundles.find({}).sort([("sortOrder", 1), ("name", 1)]).to_list(1000)]


@api_router.post("/commerce/bundles", status_code=201)
async def create_bundle(body: CommerceBundleIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    product_ids = list(dict.fromkeys(item.productId for item in body.items))
    if product_ids and await access.products.count_documents({"id": {"$in": product_ids}}) != len(product_ids):
        raise HTTPException(status_code=400, detail="Bundle enthält unbekannte Produkte")
    products = {row["id"]: row for row in await access.products.find({"id": {"$in": product_ids}}).to_list(len(product_ids))} if product_ids else {}
    for item in body.items:
        if item.variantId and not any(
            variant.get("id") == item.variantId and variant.get("active") is not False
            for variant in products[item.productId].get("variants", [])
        ):
            raise HTTPException(status_code=400, detail="Bundle enthält eine unbekannte Produktvariante")
    payload = _entity_payload(body, access.context.tenant_id)
    await _unique_slug(access.commerce_bundles, payload["slug"])
    row = {"id": "bundle-" + secrets.token_hex(6), **body.model_dump(mode="json"), **payload,
           "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    try:
        await access.commerce_bundles.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Bundle-Slug existiert bereits") from exc
    await tenant_audit(access, user, "commerce.bundle.create", row["id"], {"name": row["name"], "items": len(row["items"])})
    return strip_id(row)


@api_router.get("/commerce/homepage")
async def get_homepage(_user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    row = await access.commerce_homepages.find_one({"key": "shop"})
    return {"blocks": (row or {}).get("blocks", [])}


@api_router.put("/commerce/homepage")
async def put_homepage(body: CommerceHomepageIn, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    blocks = []
    block_ids: set[str] = set()
    for index, block in enumerate(body.blocks):
        data = block.model_dump()
        data["id"] = block.id or f"block-{secrets.token_hex(5)}"
        if data["id"] in block_ids:
            raise HTTPException(status_code=400, detail="Startseitenblöcke benötigen eindeutige IDs")
        block_ids.add(data["id"])
        if data["type"] in {"collection", "categories", "brands"} and data.get("targetId"):
            target_map = {"collection": access.shop_collections, "categories": access.product_categories, "brands": access.business_brands}
            if not await target_map[data["type"]].find_one({"id": data["targetId"]}):
                raise HTTPException(status_code=400, detail=f"Startseitenblock {index + 1} verweist auf unbekannten Inhalt")
        if data["productIds"] and await access.products.count_documents({"id": {"$in": list(set(data["productIds"]))}}) != len(set(data["productIds"])):
            raise HTTPException(status_code=400, detail=f"Startseitenblock {index + 1} verweist auf unbekannte Produkte")
        blocks.append(data)
    await access.commerce_homepages.update_one({"key": "shop"}, {"$set": {"blocks": blocks, "updatedAt": datetime.now(timezone.utc).isoformat(), "updatedBy": user["id"]}, "$setOnInsert": {"key": "shop"}}, upsert=True)
    await tenant_audit(access, user, "commerce.homepage.update", "shop", {"blocks": len(blocks)})
    return {"blocks": blocks}
