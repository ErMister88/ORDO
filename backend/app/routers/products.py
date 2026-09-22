"""Products + image upload/serving."""
import uuid
import requests
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
from ..models import ProductIn, ActiveIn, StockIn
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
    })
    return payload


@api_router.get("/products")
async def get_products(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    # Wholesale catalog is B2B-only. Shop customers (shopusers) and any other
    # role must never receive cost/floor/standard pricing. They use /shop/products.
    if user["role"] not in ("admin", "sales", "customer"):
        raise HTTPException(status_code=403, detail="Kein Zugriff auf den Großhandelskatalog")
    prods = await access.products.find({}).to_list(1000)
    result = []
    for p in prods:
        p = strip_id(p)
        if user["role"] == "customer":
            p.pop("cost", None)
            p.pop("costMinor", None)
            p.pop("salesFloor", None)
            p.pop("salesFloorMinor", None)
            p.pop("absoluteFloor", None)
            p.pop("absoluteFloorMinor", None)
        elif user["role"] == "sales":
            p.pop("cost", None)
            p.pop("costMinor", None)
        result.append(p)
    return result


@api_router.post("/products")
async def create_product(
    body: ProductIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    seq = await next_seq("product")
    prod = {"id": f"p{seq}", **_product_payload(body, access.context.default_currency)}
    await access.products.insert_one(prod)
    return strip_id(prod)


@api_router.put("/products/{product_id}")
async def update_product(
    product_id: str,
    body: ProductIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    res = await access.products.update_one(
        {"id": product_id},
        {"$set": _product_payload(body, access.context.default_currency)},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    p = await access.products.find_one({"id": product_id})
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
