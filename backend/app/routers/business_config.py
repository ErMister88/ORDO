"""Safe tenant-defined commercial classifications managed by administrators."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Annotated, Literal

from fastapi import Depends, HTTPException
from pymongo.errors import DuplicateKeyError

from ..audit_service import tenant_audit
from ..core import api_router, strip_id
from ..deps import require_roles, tenant_business_access
from ..models import BusinessClassificationIn
from ..tenant_access import TenantBusinessAccess, TenantScopedCollection


ConfigKind = Literal["brands", "customer-types", "customer-tags"]
COLLECTION_ATTR = {
    "brands": "business_brands",
    "customer-types": "customer_types",
    "customer-tags": "customer_tags",
}
ID_PREFIX = {"brands": "brand", "customer-types": "ctype", "customer-tags": "ctag"}


def _collection(access: TenantBusinessAccess, kind: ConfigKind) -> TenantScopedCollection:
    name = COLLECTION_ATTR.get(kind)
    if not name:
        raise HTTPException(status_code=404, detail="Konfigurationsbereich nicht gefunden")
    return getattr(access, name)


def _payload(body: BusinessClassificationIn) -> dict:
    name = body.name.strip()
    description = body.description.strip()
    if not name or len(name) > 120:
        raise HTTPException(status_code=400, detail="Name muss zwischen 1 und 120 Zeichen lang sein")
    if len(description) > 1000:
        raise HTTPException(status_code=400, detail="Beschreibung darf höchstens 1000 Zeichen enthalten")
    if body.sortOrder < -10000 or body.sortOrder > 10000:
        raise HTTPException(status_code=400, detail="Sortierung liegt außerhalb des zulässigen Bereichs")
    return {
        "name": name,
        "normalizedName": name.casefold(),
        "description": description,
        "sortOrder": body.sortOrder,
        "active": body.active,
    }


@api_router.get("/business-config/{kind}")
async def list_business_config(
    kind: ConfigKind,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    # Staff need archived entries to interpret historical customer/product
    # references. Assignment paths separately require active entries.
    rows = await _collection(access, kind).find({}).sort(
        [("sortOrder", 1), ("normalizedName", 1)]
    ).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.post("/business-config/{kind}", status_code=201)
async def create_business_config(
    kind: ConfigKind,
    body: BusinessClassificationIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    payload = _payload(body)
    row = {
        "id": f"{ID_PREFIX[kind]}-{secrets.token_hex(6)}",
        **payload,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "createdBy": user["id"],
    }
    try:
        await _collection(access, kind).insert_one(row)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Eintrag mit diesem Namen existiert bereits") from exc
    await tenant_audit(access, user, "business_config.create", row["id"], {"kind": kind, "name": row["name"]})
    return strip_id(row)


@api_router.put("/business-config/{kind}/{entry_id}")
async def update_business_config(
    kind: ConfigKind,
    entry_id: str,
    body: BusinessClassificationIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    try:
        result = await _collection(access, kind).update_one(
            {"id": entry_id}, {"$set": _payload(body)}
        )
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Eintrag mit diesem Namen existiert bereits") from exc
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Konfigurationseintrag nicht gefunden")
    await tenant_audit(access, user, "business_config.update", entry_id, {"kind": kind, "active": body.active})
    return strip_id(await _collection(access, kind).find_one({"id": entry_id}))


@api_router.delete("/business-config/{kind}/{entry_id}")
async def archive_business_config(
    kind: ConfigKind,
    entry_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    result = await _collection(access, kind).update_one(
        {"id": entry_id}, {"$set": {"active": False}}
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Konfigurationseintrag nicht gefunden")
    await tenant_audit(access, user, "business_config.archive", entry_id, {"kind": kind})
    return {"ok": True, "archived": True}
