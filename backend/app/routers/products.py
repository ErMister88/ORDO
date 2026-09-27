"""Products + image upload/serving."""
import hashlib
import secrets
import re
import unicodedata
from pymongo.errors import DuplicateKeyError
from fastapi import Depends, Header, HTTPException, Query
from typing import Annotated, Optional
from datetime import datetime, timezone

from ..core import api_router, strip_id, next_seq
from ..audit_service import tenant_audit
from ..deps import (
    current_user,
    public_tenant_business_access,
    require_roles,
    tenant_business_access,
)
from ..models import (
    EquipmentFinancingRequestIn, ProductCategoryIn, ProductIn, ShopCollectionIn,
    ActiveIn, StockIn,
)
from ..money import to_minor
from ..tenant_access import TenantBusinessAccess
from ..idempotency import IdempotencyService
from ..pagination import bounded_list

def _product_payload(body: ProductIn, currency: str, tenant_id: str) -> dict:
    payload = body.model_dump()
    payload["slug"] = _slug(body.slug or body.name)
    payload["slugKey"] = f"{tenant_id}:{payload['slug']}"
    payload["searchKeywords"] = list(dict.fromkeys(value.strip() for value in body.searchKeywords if value.strip()))
    payload.update({
        "currency": currency,
        "standardPriceMinor": to_minor(body.standardPrice),
        "salesFloorMinor": to_minor(body.salesFloor),
        "absoluteFloorMinor": to_minor(body.absoluteFloor),
        "costMinor": to_minor(body.cost),
        "b2cPriceMinor": to_minor(body.b2cPrice) if body.b2cPrice is not None else None,
        "discountTiers": [
            {**tier.model_dump(), "priceMinor": to_minor(tier.price), "currency": currency}
            for tier in body.discountTiers
        ],
        "b2cTiers": [
            {**tier.model_dump(), "priceMinor": to_minor(tier.price), "currency": currency}
            for tier in body.b2cTiers
        ],
    })
    return payload


def _slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.casefold()).strip("-")
    if not slug or len(slug) > 200:
        raise HTTPException(status_code=400, detail="Ungültiger Produkt-Slug")
    return slug


INTERNAL_PRODUCT_FIELDS = {
    "cost", "costMinor", "salesFloor", "salesFloorMinor",
    "absoluteFloor", "absoluteFloorMinor", "internalCosts", "margin", "profitability",
    "metadata",
    "attributeValues",
}


def _public_taxonomy_row(row: dict) -> dict:
    return {key: value for key, value in strip_id(row).items() if key not in {
        "tenantId", "slugKey", "normalizedName", "createdBy", "updatedBy",
        "createdAt", "updatedAt", "archivedAt", "internalNotes",
    }}


def _product_response(product: dict, role: str) -> dict:
    payload = strip_id(product)
    if role != "admin":
        for field in INTERNAL_PRODUCT_FIELDS:
            payload.pop(field, None)
    if role in ("sales", "customer"):
        # The wholesale catalog receives only B2B data. Its payable price
        # comes from the authoritative B2B quote endpoint; shop prices belong
        # exclusively to the public shop API.
        for field in ("b2cPrice", "b2cPriceMinor", "b2cTiers"):
            payload.pop(field, None)
    return payload


async def _validate_product_references(access: TenantBusinessAccess, body: ProductIn, product_id: str | None = None) -> None:
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Produktname ist erforderlich")
    if not body.unit.strip():
        raise HTTPException(status_code=400, detail="Einheit ist erforderlich")
    category = None
    category_lineage: set[str] = set()
    if body.categoryId:
        category = await access.product_categories.find_one({"id": body.categoryId, "active": {"$ne": False}})
        if not category:
            raise HTTPException(status_code=400, detail="Kategorie ist nicht verfügbar")
        current = category
        while current and current.get("id") not in category_lineage:
            category_lineage.add(current["id"])
            current = await access.product_categories.find_one({"id": current.get("parentId"), "active": {"$ne": False}}) if current.get("parentId") else None
    if body.brandId and not await access.business_brands.find_one(
        {"id": body.brandId, "active": {"$ne": False}}
    ):
        raise HTTPException(status_code=400, detail="Marke ist nicht verfügbar")
    slug = _slug(body.slug or body.name)
    slug_match = await access.products.find_one({"slug": slug})
    if slug_match and slug_match.get("id") != product_id:
        raise HTTPException(status_code=409, detail="Produkt-Slug ist bereits vergeben")
    collection_ids = list(dict.fromkeys(body.collectionIds))
    if len(collection_ids) != len(body.collectionIds):
        raise HTTPException(status_code=400, detail="Shop-Collections dürfen nicht doppelt zugeordnet werden")
    if collection_ids:
        available = await access.shop_collections.count_documents({
            "id": {"$in": collection_ids}, "active": {"$ne": False},
        })
        if available != len(collection_ids):
            raise HTTPException(status_code=400, detail="Shop-Collection ist nicht verfügbar")
    sku = body.sku.strip()
    if sku:
        existing = await access.products.find_one({"sku": sku})
        if existing and existing.get("id") != product_id:
            raise HTTPException(status_code=409, detail="Artikelnummer ist bereits vergeben")
    if body.ean.strip():
        existing = await access.products.find_one({"ean": body.ean.strip()})
        if existing and existing.get("id") != product_id:
            raise HTTPException(status_code=409, detail="EAN/GTIN ist bereits vergeben")
    if body.regionIds and await access.commerce_regions.count_documents({"id": {"$in": list(set(body.regionIds))}, "active": {"$ne": False}}) != len(set(body.regionIds)):
        raise HTTPException(status_code=400, detail="Region ist nicht verfügbar")
    if body.shippingClassId and not await access.commerce_shipping_classes.find_one({"id": body.shippingClassId, "active": {"$ne": False}}):
        raise HTTPException(status_code=400, detail="Versandklasse ist nicht verfügbar")
    relation_ids = list(dict.fromkeys(body.relatedProductIds + body.recommendedProductIds + body.compatibleProductIds))
    if product_id and product_id in relation_ids:
        raise HTTPException(status_code=400, detail="Produkt kann nicht mit sich selbst verknüpft werden")
    if relation_ids and await access.products.count_documents({"id": {"$in": relation_ids}}) != len(relation_ids):
        raise HTTPException(status_code=400, detail="Produktbeziehung verweist auf unbekanntes Produkt")
    attribute_keys = set(body.attributeValues)
    for variant in body.variants:
        attribute_keys.update(variant.attributeValues)
    definitions = await access.commerce_attributes.find({"key": {"$in": list(attribute_keys)}}).to_list(500) if attribute_keys else []
    if len(definitions) != len(attribute_keys):
        raise HTTPException(status_code=400, detail="Produkt enthält unbekannte Attribute")
    definitions_by_key = {definition["key"]: definition for definition in definitions}
    for definition in definitions:
        category_ids = definition.get("categoryIds", [])
        if category_ids and not category_lineage.intersection(category_ids):
            raise HTTPException(status_code=400, detail=f"Attribut {definition.get('name', definition['id'])} ist für diese Kategorie nicht freigegeben")
    for values in [body.attributeValues, *(variant.attributeValues for variant in body.variants)]:
        for key, value in values.items():
            definition = definitions_by_key[key]
            value_type = definition.get("valueType")
            valid = (
                (value_type == "text" and isinstance(value, str))
                or (value_type == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
                or (value_type == "boolean" and isinstance(value, bool))
                or (value_type == "select" and isinstance(value, str) and value in definition.get("options", []))
                or (value_type == "multi_select" and isinstance(value, list) and all(isinstance(item, str) and item in definition.get("options", []) for item in value))
            )
            if not valid:
                raise HTTPException(status_code=400, detail=f"Ungültiger Wert für Attribut {definition.get('name', definition['id'])}")
    variant_ids = set()
    variant_skus = {body.sku.strip()} if body.sku.strip() else set()
    variant_eans = {body.ean.strip()} if body.ean.strip() else set()
    for variant_model in body.variants:
        variant = variant_model.model_dump()
        if not isinstance(variant.get("id"), str) or not variant["id"].strip():
            raise HTTPException(status_code=400, detail="Jede Variante benötigt eine stabile ID")
        if variant["id"] in variant_ids:
            raise HTTPException(status_code=400, detail="Varianten-ID ist doppelt")
        variant_ids.add(variant["id"])
        for field, seen in (("sku", variant_skus), ("ean", variant_eans)):
            value = str(variant.get(field) or "").strip()
            if value and value in seen:
                raise HTTPException(status_code=400, detail=f"Varianten-{field.upper()} ist doppelt")
            if value:
                seen.add(value)
    identifier_queries = []
    if variant_skus:
        identifier_queries.extend([{"sku": {"$in": list(variant_skus)}}, {"variants.sku": {"$in": list(variant_skus)}}])
    if variant_eans:
        identifier_queries.extend([{"ean": {"$in": list(variant_eans)}}, {"variants.ean": {"$in": list(variant_eans)}}])
    if identifier_queries:
        candidates = await access.products.find({"$or": identifier_queries}).to_list(500)
        for candidate in candidates:
            if candidate.get("id") == product_id:
                continue
            existing_skus = {str(candidate.get("sku") or "").strip(), *(str(row.get("sku") or "").strip() for row in candidate.get("variants", []))}
            existing_eans = {str(candidate.get("ean") or "").strip(), *(str(row.get("ean") or "").strip() for row in candidate.get("variants", []))}
            if variant_skus.intersection(existing_skus) or variant_eans.intersection(existing_eans):
                raise HTTPException(status_code=409, detail="SKU oder EAN/GTIN ist bereits vergeben")


@api_router.get("/products")
async def get_products(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    q: Annotated[str, Query(max_length=200)] = "",
):
    # Wholesale catalog is B2B-only. Shop customers (shopusers) and any other
    # role must never receive cost/floor/standard pricing. They use /shop/products.
    if user["role"] not in ("admin", "sales", "customer"):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf den Großhandelskatalog")
    query = {} if user["role"] == "admin" else {
        "active": {"$ne": False},
        "b2bAvailable": {"$ne": False},
    }
    terms = [term for term in re.split(r"\s+", q.strip()) if term][:8]
    if terms:
        query["$and"] = [{"$or": [
            {field: {"$regex": re.escape(term), "$options": "i"}}
            for field in ("name", "brand", "sku", "ean", "description", "searchKeywords")
        ]} for term in terms]
    prods = await bounded_list(
        access.products.find(query).sort([("name", 1), ("id", 1)]),
        limit=limit, offset=offset,
    )
    return [_product_response(product, user["role"]) for product in prods]


@api_router.post("/products")
async def create_product(
    body: ProductIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    await _validate_product_references(access, body)
    seq = await next_seq("product")
    payload = _product_payload(body, access.context.default_currency, access.context.tenant_id)
    if body.brandId:
        payload["brand"] = (await access.business_brands.find_one({"id": body.brandId}))["name"]
    prod = {"id": f"p{seq}", **payload}
    await access.products.insert_one(prod)
    await tenant_audit(access, user, "product.create", prod["id"], {"name": prod["name"], "sku": prod.get("sku", "")})
    return strip_id(prod)


@api_router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    body: ProductIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    await _validate_product_references(access, body, product_id)
    payload = _product_payload(body, access.context.default_currency, access.context.tenant_id)
    if body.brandId:
        payload["brand"] = (await access.business_brands.find_one({"id": body.brandId}))["name"]
    if "metadata" not in body.model_fields_set:
        payload.pop("metadata", None)
    res = await access.products.update_one(
        {"id": product_id},
        {"$set": payload},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    p = await access.products.find_one({"id": product_id})
    await tenant_audit(access, user, "product.update", product_id, {
        "changedFields": sorted(payload.keys()),
    })
    return strip_id(p)


@api_router.put("/products/{product_id}/active")
async def set_product_active(
    product_id: str,
    body: ActiveIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    res = await access.products.update_one({"id": product_id}, {"$set": {"active": body.active}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    return {"ok": True, "active": body.active}


@api_router.put("/products/{product_id}/stock")
async def set_product_stock(
    product_id: str,
    body: StockIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    stock = None if body.stock is None else max(0, body.stock)
    res = await access.products.update_one({"id": product_id}, {"$set": {"stock": stock}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    await tenant_audit(access, user, "product.stock", product_id, {"stock": stock})
    return {"ok": True, "stock": stock}


@api_router.get("/product-categories")
async def list_product_categories(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    query = {} if user["role"] == "admin" else {"active": {"$ne": False}}
    rows = await access.product_categories.find(query).sort("name", 1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.get("/shop/categories")
async def list_public_product_categories(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    rows = await access.product_categories.find({"active": {"$ne": False}}).sort("name", 1).to_list(1000)
    return [_public_taxonomy_row(row) for row in rows]


@api_router.get("/shop/collections")
async def list_public_shop_collections(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    rows = await access.shop_collections.find({"active": {"$ne": False}}).sort("sortOrder", 1).to_list(1000)
    return [_public_taxonomy_row(row) for row in rows]


@api_router.get("/shop-collections")
async def list_shop_collections(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.shop_collections.find({}).sort("sortOrder", 1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.post("/shop-collections", status_code=201)
async def create_shop_collection(
    body: ShopCollectionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name der Shop-Collection ist erforderlich")
    if await access.shop_collections.find_one({"name": name}):
        raise HTTPException(status_code=409, detail="Shop-Collection existiert bereits")
    row = {"id": "collection-" + secrets.token_hex(6), **body.model_dump(), "name": name,
           "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    try:
        await access.shop_collections.insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Shop-Collection existiert bereits") from exc
    await tenant_audit(access, user, "shop_collection.create", row["id"], {"name": name})
    return strip_id(row)


@api_router.put("/shop-collections/{collection_id}")
async def update_shop_collection(
    collection_id: str, body: ShopCollectionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name der Shop-Collection ist erforderlich")
    duplicate = await access.shop_collections.find_one({"name": name})
    if duplicate and duplicate.get("id") != collection_id:
        raise HTTPException(status_code=409, detail="Shop-Collection existiert bereits")
    try:
        result = await access.shop_collections.update_one(
            {"id": collection_id}, {"$set": {**body.model_dump(), "name": name}}
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Shop-Collection existiert bereits") from exc
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Shop-Collection nicht gefunden")
    await tenant_audit(access, user, "shop_collection.update", collection_id,
                       {"name": name, "active": body.active, "sortOrder": body.sortOrder})
    return strip_id(await access.shop_collections.find_one({"id": collection_id}))


@api_router.delete("/shop-collections/{collection_id}")
async def archive_shop_collection(
    collection_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await access.shop_collections.find_one({"id": collection_id}):
        raise HTTPException(status_code=404, detail="Shop-Collection nicht gefunden")
    referenced = await access.products.count_documents({"collectionIds": collection_id})
    await access.shop_collections.update_one({"id": collection_id}, {"$set": {"active": False}})
    await tenant_audit(access, user, "shop_collection.archive", collection_id, {"referencedProducts": referenced})
    return {"ok": True, "archived": True, "referencedProducts": referenced}


@api_router.post("/product-categories")
async def create_product_category(
    body: ProductCategoryIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Kategoriename ist erforderlich")
    if await access.product_categories.find_one({"name": name}):
        raise HTTPException(status_code=409, detail="Kategorie existiert bereits")
    slug = _slug(body.slug or name)
    if await access.product_categories.find_one({"slug": slug}):
        raise HTTPException(status_code=409, detail="Kategorie-Slug existiert bereits")
    if body.parentId and not await access.product_categories.find_one({"id": body.parentId}):
        raise HTTPException(status_code=400, detail="Übergeordnete Kategorie nicht gefunden")
    row = {"id": "cat-" + secrets.token_hex(6), **body.model_dump(), "name": name, "slug": slug,
           "slugKey": f"{access.context.tenant_id}:{slug}",
           "createdAt": datetime.now(timezone.utc).isoformat()}
    await access.product_categories.insert_one(row)
    await tenant_audit(access, user, "product_category.create", row["id"], {"name": name})
    return strip_id(row)


@api_router.put("/product-categories/{category_id}")
async def update_product_category(
    category_id: str,
    body: ProductCategoryIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Kategoriename ist erforderlich")
    slug = _slug(body.slug or name)
    duplicate = await access.product_categories.find_one({"slug": slug})
    if duplicate and duplicate.get("id") != category_id:
        raise HTTPException(status_code=409, detail="Kategorie-Slug existiert bereits")
    current = body.parentId
    visited = set()
    while current:
        if current == category_id or current in visited:
            raise HTTPException(status_code=400, detail="Kategoriehierarchie enthält einen Zyklus")
        visited.add(current)
        parent = await access.product_categories.find_one({"id": current})
        if not parent:
            raise HTTPException(status_code=400, detail="Übergeordnete Kategorie nicht gefunden")
        current = parent.get("parentId")
    result = await access.product_categories.update_one({"id": category_id}, {"$set": {**body.model_dump(), "name": name, "slug": slug, "slugKey": f"{access.context.tenant_id}:{slug}"}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    row = await access.product_categories.find_one({"id": category_id})
    await tenant_audit(access, user, "product_category.update", category_id,
                       {"name": name, "active": body.active, "sortOrder": body.sortOrder})
    return strip_id(row)


@api_router.delete("/product-categories/{category_id}")
async def archive_product_category(
    category_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if await access.product_categories.find_one({"parentId": category_id, "active": {"$ne": False}}):
        raise HTTPException(status_code=409, detail="Kategorie mit aktiven Unterkategorien kann nicht archiviert werden")
    result = await access.product_categories.update_one(
        {"id": category_id}, {"$set": {"active": False}}
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    referenced = await access.products.count_documents({"categoryId": category_id})
    await tenant_audit(access, user, "product_category.archive", category_id, {"referencedProducts": referenced})
    return {"ok": True, "archived": True, "referencedProducts": referenced}


async def create_equipment_financing_request(
    body: EquipmentFinancingRequestIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    if operation_id:
        existing = await access.equipment_requests.find_one({"operationId": operation_id})
        if existing:
            return {"id": existing["id"], "status": existing["status"]}
    product = await access.products.find_one({
        "id": body.productId, "active": {"$ne": False},
        "b2cAvailable": {"$ne": False}, "financingRequestAllowed": True,
    })
    if not product:
        raise HTTPException(status_code=404, detail="Produkt ist für eine Finanzierungsanfrage nicht verfügbar")
    email = body.email.strip().lower()
    if not body.name.strip() or not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Name und gültige E-Mail-Adresse sind erforderlich")
    row = {
        "id": "equipment-request-" + secrets.token_hex(8), "productId": product["id"],
        "productSnapshot": {"id": product["id"], "sku": product.get("sku"),
                            "name": product.get("name", ""), "brand": product.get("brand", "")},
        "requestType": "financing", "contact": {"name": body.name.strip(), "email": email,
                                                   "phone": body.phone.strip()},
        "message": body.message.strip(), "status": "Angefragt",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    if operation_id:
        row["operationId"] = operation_id
    await access.equipment_requests.insert_one(row)
    return {"id": row["id"], "status": row["status"]}


@api_router.post("/shop/equipment-requests", status_code=201)
async def create_equipment_financing_request_endpoint(
    body: EquipmentFinancingRequestIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    email = body.email.strip().lower()
    actor_id = "guest:" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]
    service = IdempotencyService(
        access, actor_id=actor_id, operation="equipment_request.create",
        key=idempotency_key or "", payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_equipment_financing_request(
            body, access, operation_id=claim.record_id
        )
        await service.complete(claim, response, {"equipmentRequestId": response["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="equipment_request_failed", exception=exc)
        raise


@api_router.get("/equipment-requests")
async def list_equipment_financing_requests(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    rows = await bounded_list(
        access.equipment_requests.find({}).sort([("createdAt", -1), ("id", -1)]),
        limit=limit, offset=offset,
    )
    return [strip_id(row) for row in rows]


async def serve_file(path: str, access: TenantBusinessAccess):
    """Compatibility shim for internal callers; routing lives in files.py."""
    from .files import serve_public_file
    return await serve_public_file(path, access)
