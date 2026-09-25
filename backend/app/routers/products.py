"""Products + image upload/serving."""
import secrets
import uuid
import requests
from pymongo.errors import DuplicateKeyError
from fastapi import Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from typing import Annotated
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
from ..storage import put_object, get_object, APP_NAME
from ..tenant_access import TenantBusinessAccess

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


def _product_payload(body: ProductIn, currency: str) -> dict:
    payload = body.model_dump()
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


INTERNAL_PRODUCT_FIELDS = {
    "cost", "costMinor", "salesFloor", "salesFloorMinor",
    "absoluteFloor", "absoluteFloorMinor", "internalCosts", "margin", "profitability",
    "metadata",
}


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
    if body.categoryId and not await access.product_categories.find_one({"id": body.categoryId, "active": {"$ne": False}}):
        raise HTTPException(status_code=400, detail="Kategorie ist nicht verfügbar")
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


@api_router.get("/products")
async def get_products(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    # Wholesale catalog is B2B-only. Shop customers (shopusers) and any other
    # role must never receive cost/floor/standard pricing. They use /shop/products.
    if user["role"] not in ("admin", "sales", "customer"):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf den Großhandelskatalog")
    query = {} if user["role"] == "admin" else {
        "active": {"$ne": False},
        "b2bAvailable": {"$ne": False},
    }
    prods = await access.products.find(query).to_list(1000)
    return [_product_response(product, user["role"]) for product in prods]


@api_router.post("/products")
async def create_product(
    body: ProductIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    await _validate_product_references(access, body)
    seq = await next_seq("product")
    prod = {"id": f"p{seq}", **_product_payload(body, access.context.default_currency)}
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
    payload = _product_payload(body, access.context.default_currency)
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
    return [strip_id(row) for row in rows]


@api_router.get("/shop/collections")
async def list_public_shop_collections(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    rows = await access.shop_collections.find({"active": {"$ne": False}}).sort("sortOrder", 1).to_list(1000)
    return [strip_id(row) for row in rows]


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
    row = {"id": "cat-" + secrets.token_hex(6), **body.model_dump(), "name": name,
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
    result = await access.product_categories.update_one({"id": category_id}, {"$set": {**body.model_dump(), "name": name}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Kategorie nicht gefunden")
    row = await access.product_categories.find_one({"id": category_id})
    return strip_id(row)


@api_router.post("/shop/equipment-requests", status_code=201)
async def create_equipment_financing_request(
    body: EquipmentFinancingRequestIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
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
    await access.equipment_requests.insert_one(row)
    return {"id": row["id"], "status": row["status"]}


@api_router.get("/equipment-requests")
async def list_equipment_financing_requests(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.equipment_requests.find({}).sort("createdAt", -1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.post("/upload")
async def upload_image(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    file: UploadFile = File(...),
):
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Nur Bilddateien sind erlaubt")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Bild ist zu groß (max. 8 MB)")
    ext = (file.filename or "img.jpg").rsplit(".", 1)[-1].lower()
    path = f"{APP_NAME}/uploads/{user['id']}/{uuid.uuid4().hex}.{ext}"
    try:
        result = await run_in_threadpool(put_object, path, data, content_type)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 500
        if code == 402:
            raise HTTPException(status_code=402, detail="Speicher-Kontingent aufgebraucht")
        raise HTTPException(status_code=502, detail="Upload fehlgeschlagen")
    stored = result["path"]
    await access.uploads.insert_one({
        "storagePath": stored,
        "ownerId": user["id"],
        "contentType": content_type,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    })
    return {"url": f"/api/files/{stored}", "path": stored}


@api_router.get("/files/{path:path}")
async def serve_file(
    path: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    doc = await access.uploads.find_one({"storagePath": path})
    if not doc:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    try:
        content, content_type = await run_in_threadpool(get_object, path)
    except requests.HTTPError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})
