"""Tenant-safe upload, public asset and private document endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from pathlib import PurePath
from typing import Annotated, Literal

from fastapi import Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..core import api_router, logger, strip_id
from ..deps import current_user, public_tenant_business_access, require_roles, tenant_business_access, visible_company_ids
from ..observability import report_operational_failure
from ..storage import (
    MAX_UPLOAD_BYTES, StorageError, StorageNotConfigured, StorageObjectNotFound, build_storage_key,
    get_storage_provider, validate_upload,
)
from ..tenant_access import TenantBusinessAccess


PUBLIC_RESOURCE_TYPES = frozenset({"product", "machine", "shop_collection"})
PRIVATE_RESOURCE_TYPES = frozenset({"customer", "offer", "order", "invoice", "contract", "machine_request"})
RESOURCE_COLLECTIONS = {
    "customer": "companies", "offer": "offers", "order": "orders", "invoice": "invoices",
    "contract": "contracts", "machine_request": "machine_requests",
}
PUBLIC_RESOURCE_COLLECTIONS = {
    "product": "products", "machine": "machines", "shop_collection": "shop_collections",
}


class ProductImageOrderIn(BaseModel):
    sortOrder: int = Field(default=0, ge=0, le=10000)
    isPrimary: bool = False


class FileAssociationIn(BaseModel):
    resourceType: str
    resourceId: str = Field(min_length=1, max_length=160)


def _safe_filename(value: str | None) -> str:
    name = PurePath((value or "upload").replace("\\", "/")).name
    cleaned = "".join(character for character in name if character.isprintable()).strip()
    return (cleaned or "upload")[:180]


def _public_file(row: dict) -> dict:
    payload = strip_id(row)
    for key in ("tenantId", "storageKey", "etag", "createdBy"):
        payload.pop(key, None)
    payload["url"] = f"/api/files/{row['id']}"
    return payload


async def _private_file_allowed(row: dict, user: dict, access: TenantBusinessAccess) -> bool:
    if user.get("role") == "admin":
        return True
    resource_type = row.get("resourceType")
    resource_id = row.get("resourceId")
    visible = set(await visible_company_ids(user, access))
    if resource_type == "customer":
        return resource_id in visible
    collection_name = RESOURCE_COLLECTIONS.get(resource_type)
    if not collection_name:
        return False
    resource = await getattr(access, collection_name).find_one({"id": resource_id})
    company_id = resource.get("companyId") if resource else None
    if resource_type == "machine_request" and resource:
        company_id = (resource.get("customer") or {}).get("companyId")
    return bool(resource and company_id in visible)


@api_router.post("/upload")
async def upload_file(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    file: UploadFile = File(...),
    resource_type: str = Form(default="product"),
    resource_id: str = Form(default="unassigned"),
    visibility: Literal["public", "private"] = Form(default="public"),
):
    resource_type = resource_type.strip().lower()
    resource_id = resource_id.strip()[:160] or "unassigned"
    allowed_types = PUBLIC_RESOURCE_TYPES if visibility == "public" else PRIVATE_RESOURCE_TYPES
    if resource_type not in allowed_types:
        raise HTTPException(status_code=400, detail="Dateityp und Sichtbarkeit passen nicht zusammen")
    if resource_id != "unassigned":
        collection_name = (
            PUBLIC_RESOURCE_COLLECTIONS.get(resource_type)
            if visibility == "public"
            else RESOURCE_COLLECTIONS.get(resource_type)
        )
        if not collection_name or not await getattr(access, collection_name).find_one({"id": resource_id}):
            raise HTTPException(status_code=404, detail="Zugeordnete Ressource nicht gefunden")
    elif visibility == "private":
        raise HTTPException(status_code=400, detail="Private Dateien benötigen eine Ressourcenzuordnung")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    declared = (file.content_type or "application/octet-stream").lower()
    try:
        content_type = validate_upload(data, declared, visibility=visibility)
        file_id = "file_" + secrets.token_hex(12)
        key = build_storage_key(
            tenant_id=access.context.tenant_id, visibility=visibility,
            resource_type=resource_type, resource_id=resource_id, file_id=file_id,
            content_type=content_type,
        )
    except StorageError as exc:
        raise HTTPException(status_code=400, detail="Datei ist ungültig") from exc
    try:
        provider = get_storage_provider()
        stored = await run_in_threadpool(provider.put, key, data, content_type)
    except StorageNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Dateispeicher ist nicht konfiguriert") from exc
    except StorageError as exc:
        await report_operational_failure(
            access, logger, operation="storage.upload", category="storage",
        )
        raise HTTPException(status_code=503, detail="Datei konnte nicht gespeichert werden") from exc

    now = datetime.now(timezone.utc)
    document = {
        "id": file_id,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "storageProvider": provider.name,
        "storageKey": stored.key,
        "originalFilename": _safe_filename(file.filename),
        "contentType": stored.content_type,
        "size": stored.size,
        "checksumSha256": stored.checksum_sha256,
        "etag": stored.etag,
        "visibility": visibility,
        "createdAt": now,
        "createdBy": user["id"],
        "status": "staged" if resource_id == "unassigned" else "active",
        "sortOrder": 0,
        "isPrimary": False,
    }
    try:
        await access.uploads.insert_one(document)
    except Exception:
        try:
            await run_in_threadpool(provider.delete, stored.key)
        except Exception:
            await report_operational_failure(
                access, logger, operation="storage.orphan_cleanup", category="storage",
            )
        raise
    return {"id": file_id, "url": f"/api/files/{file_id}", "path": file_id}


@api_router.get("/files/{file_ref:path}")
async def serve_public_file(
    file_ref: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    row = await access.uploads.find_one({"id": file_ref, "status": "active", "visibility": "public"})
    if not row:
        # Legacy metadata remains discoverable in the tenant boundary, but the
        # retired Emergent object store is never contacted at runtime.
        legacy = await access.uploads.find_one({"storagePath": file_ref})
        if legacy:
            raise HTTPException(status_code=410, detail="Legacy-Datei benötigt eine kontrollierte Speichermigration")
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    try:
        provider = get_storage_provider()
        if provider.name != row.get("storageProvider"):
            raise StorageObjectNotFound("Provider mismatch")
        content, content_type = await run_in_threadpool(provider.read, row["storageKey"])
    except StorageNotConfigured as exc:
        await report_operational_failure(access, logger, operation="storage.public_read", category="storage")
        raise HTTPException(status_code=503, detail="Dateispeicher ist nicht verfügbar") from exc
    except StorageObjectNotFound:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    except (StorageError, KeyError) as exc:
        await report_operational_failure(access, logger, operation="storage.public_read", category="storage")
        raise HTTPException(status_code=503, detail="Datei konnte nicht geladen werden") from exc
    return Response(
        content=content, media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"},
    )


@api_router.get("/documents/{file_id}")
async def serve_private_document(
    file_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    row = await access.uploads.find_one({"id": file_id, "status": "active", "visibility": "private"})
    if not row or not await _private_file_allowed(row, user, access):
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    try:
        provider = get_storage_provider()
        if provider.name != row.get("storageProvider"):
            raise StorageObjectNotFound("Provider mismatch")
        content, content_type = await run_in_threadpool(provider.read, row["storageKey"])
    except StorageNotConfigured as exc:
        await report_operational_failure(access, logger, operation="storage.private_read", category="storage")
        raise HTTPException(status_code=503, detail="Dateispeicher ist nicht verfügbar") from exc
    except StorageObjectNotFound:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    except (StorageError, KeyError) as exc:
        await report_operational_failure(access, logger, operation="storage.private_read", category="storage")
        raise HTTPException(status_code=503, detail="Datei konnte nicht geladen werden") from exc
    return Response(
        content=content, media_type=content_type,
        headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "attachment",
        },
    )


@api_router.delete("/files/{file_id}")
async def archive_file(
    file_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    row = await access.uploads.find_one({"id": file_id, "status": {"$ne": "archived"}})
    if not row:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    if row.get("storageProvider") and row.get("storageKey"):
        try:
            provider = get_storage_provider()
            if provider.name != row["storageProvider"]:
                raise StorageError("Provider mismatch")
            await run_in_threadpool(provider.delete, row["storageKey"])
        except StorageError as exc:
            await report_operational_failure(
                access, logger, operation="storage.delete", category="storage",
            )
            raise HTTPException(status_code=503, detail="Datei konnte nicht sicher entfernt werden") from exc
    await access.uploads.update_one(
        {"id": file_id},
        {"$set": {"status": "archived", "archivedAt": datetime.now(timezone.utc), "archivedBy": user["id"]}},
    )
    if row.get("resourceType") == "product":
        await access.products.update_one(
            {"id": row.get("resourceId"), "imageUrl": f"/api/files/{file_id}"},
            {"$set": {"imageUrl": ""}},
        )
    return {"ok": True}


@api_router.put("/files/{file_id}/association")
async def associate_public_file(
    file_id: str,
    body: FileAssociationIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    resource_type = body.resourceType.strip().lower()
    collection_name = PUBLIC_RESOURCE_COLLECTIONS.get(resource_type)
    row = await access.uploads.find_one({
        "id": file_id, "status": {"$in": ["staged", "active"]}, "visibility": "public",
    })
    if not row or not collection_name:
        raise HTTPException(status_code=404, detail="Datei oder Ressource nicht gefunden")
    if row.get("resourceType") != resource_type or row.get("resourceId") not in {"unassigned", body.resourceId}:
        raise HTTPException(status_code=409, detail="Datei gehört bereits zu einer anderen Ressource")
    resource = await getattr(access, collection_name).find_one({"id": body.resourceId})
    if not resource:
        raise HTTPException(status_code=404, detail="Datei oder Ressource nicht gefunden")
    await access.uploads.update_one(
        {"id": file_id, "resourceType": resource_type, "resourceId": row.get("resourceId")},
        {"$set": {"resourceId": body.resourceId, "status": "active"}},
    )
    updated = await access.uploads.find_one({"id": file_id})
    return _public_file(updated)


@api_router.get("/products/{product_id}/images")
async def list_product_images(
    product_id: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    product = await access.products.find_one({"id": product_id, "active": {"$ne": False}})
    if not product:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    rows = await access.uploads.find({
        "resourceType": "product", "resourceId": product_id, "visibility": "public", "status": "active",
    }).sort([("isPrimary", -1), ("sortOrder", 1), ("createdAt", 1)]).to_list(100)
    return [_public_file(row) for row in rows]


@api_router.put("/products/{product_id}/images/{file_id}")
async def update_product_image(
    product_id: str,
    file_id: str,
    body: ProductImageOrderIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    product = await access.products.find_one({"id": product_id})
    row = await access.uploads.find_one({
        "id": file_id, "visibility": "public", "status": {"$in": ["staged", "active"]},
    })
    if not product or not row:
        raise HTTPException(status_code=404, detail="Produktbild nicht gefunden")
    if row.get("resourceType") != "product" or row.get("resourceId") not in {product_id, "unassigned"}:
        raise HTTPException(status_code=409, detail="Datei gehört nicht zu diesem Produkt")
    if body.isPrimary:
        others = await access.uploads.find({
            "resourceType": "product", "resourceId": product_id, "isPrimary": True, "status": "active",
        }).to_list(100)
        for other in others:
            await access.uploads.update_one({"id": other["id"]}, {"$set": {"isPrimary": False}})
    await access.uploads.update_one(
        {"id": file_id},
        {"$set": {"resourceType": "product", "resourceId": product_id,
                  "sortOrder": body.sortOrder, "isPrimary": body.isPrimary, "status": "active"}},
    )
    if body.isPrimary:
        await access.products.update_one({"id": product_id}, {"$set": {"imageUrl": f"/api/files/{file_id}"}})
    updated = await access.uploads.find_one({"id": file_id})
    return _public_file(updated)
