"""Subscriptions (recurring orders)."""
import secrets
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import api_router, strip_id, next_seq
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..models import SubscriptionIn
from ..tenant_access import TenantBusinessAccess


async def _references_are_visible(access: TenantBusinessAccess, subscription: dict) -> bool:
    company_id = subscription.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in subscription.get("items", []):
        product_id = item.get("productId")
        if not isinstance(product_id, str) or not await access.products.find_one({"id": product_id}):
            return False
    return True


@api_router.get("/subscriptions")
async def list_subscriptions(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    subs = await access.subscriptions.find({"companyId": {"$in": ids}}).to_list(1000)
    return [strip_id(s) for s in subs]


@api_router.post("/subscriptions")
async def create_subscription(
    body: SubscriptionIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    now = datetime.now(timezone.utc)
    sub = {
        "id": "sub-" + secrets.token_hex(5),
        "companyId": body.companyId,
        "items": [it.model_dump() for it in body.items],
        "intervalDays": body.intervalDays,
        "active": True,
        "createdBy": user["id"],
        "createdAt": now.isoformat(),
        "nextRun": (now + timedelta(days=body.intervalDays)).date().isoformat(),
        "lastRun": None,
    }
    if not await _references_are_visible(access, sub):
        raise HTTPException(status_code=404, detail="Abo-Referenz nicht gefunden")
    await access.subscriptions.insert_one(sub)
    return strip_id(sub)


@api_router.put("/subscriptions/{sub_id}/toggle")
async def toggle_subscription(
    sub_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    s = await access.subscriptions.find_one({"id": sub_id})
    if not s or not await _references_are_visible(access, s):
        raise HTTPException(status_code=404, detail="Abo nicht gefunden")
    ids = await visible_company_ids(user, access)
    if s["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await access.subscriptions.update_one({"id": sub_id}, {"$set": {"active": not s.get("active", True)}})
    return {"ok": True, "active": not s.get("active", True)}


@api_router.delete("/subscriptions/{sub_id}")
async def delete_subscription(
    sub_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    s = await access.subscriptions.find_one({"id": sub_id})
    if not s or not await _references_are_visible(access, s):
        raise HTTPException(status_code=404, detail="Abo nicht gefunden")
    ids = await visible_company_ids(user, access)
    if s["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await access.subscriptions.delete_one({"id": sub_id})
    return {"ok": True}


@api_router.post("/subscriptions/run")
async def run_due_subscriptions(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    subs = await access.subscriptions.find({"active": True}).to_list(1000)
    due = [s for s in subs if (s.get("nextRun") or "9999") <= today]
    for subscription in due:
        if not await _references_are_visible(access, subscription):
            raise HTTPException(status_code=404, detail="Abo-Referenz nicht gefunden")
    created = []
    for s in due:
        seq = await next_seq("order")
        order_no = f"B-{now.year}-{seq:05d}"
        order = {
            "id": order_no,
            "companyId": s["companyId"],
            "createdBy": user["id"],
            "status": "Neu",
            "items": s["items"],
            "fromSubscription": s["id"],
            "createdAt": now.isoformat(),
        }
        await access.orders.insert_one(order)
        next_run = (now + timedelta(days=s.get("intervalDays", 28))).date().isoformat()
        await access.subscriptions.update_one({"id": s["id"]}, {"$set": {"lastRun": today, "nextRun": next_run}})
        created.append(order_no)
    return {"ok": True, "created": created, "count": len(created)}
