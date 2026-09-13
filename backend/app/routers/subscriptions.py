"""Subscriptions (recurring orders)."""
import secrets
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import api_router, db, strip_id, next_seq
from ..deps import require_roles, visible_company_ids
from ..models import SubscriptionIn


@api_router.get("/subscriptions")
async def list_subscriptions(user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
    subs = await db.subscriptions.find({"companyId": {"$in": ids}}).to_list(1000)
    return [strip_id(s) for s in subs]


@api_router.post("/subscriptions")
async def create_subscription(body: SubscriptionIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
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
    await db.subscriptions.insert_one(sub)
    return strip_id(sub)


@api_router.put("/subscriptions/{sub_id}/toggle")
async def toggle_subscription(sub_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    s = await db.subscriptions.find_one({"id": sub_id})
    if not s:
        raise HTTPException(status_code=404, detail="Abo nicht gefunden")
    ids = await visible_company_ids(user)
    if s["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await db.subscriptions.update_one({"id": sub_id}, {"$set": {"active": not s.get("active", True)}})
    return {"ok": True, "active": not s.get("active", True)}


@api_router.delete("/subscriptions/{sub_id}")
async def delete_subscription(sub_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    s = await db.subscriptions.find_one({"id": sub_id})
    if not s:
        raise HTTPException(status_code=404, detail="Abo nicht gefunden")
    ids = await visible_company_ids(user)
    if s["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await db.subscriptions.delete_one({"id": sub_id})
    return {"ok": True}


@api_router.post("/subscriptions/run")
async def run_due_subscriptions(user: Annotated[dict, Depends(require_roles("admin"))]):
    now = datetime.now(timezone.utc)
    today = now.date().isoformat()
    subs = await db.subscriptions.find({"active": True}).to_list(1000)
    created = []
    for s in subs:
        if (s.get("nextRun") or "9999") > today:
            continue
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
        await db.orders.insert_one(order)
        next_run = (now + timedelta(days=s.get("intervalDays", 28))).date().isoformat()
        await db.subscriptions.update_one({"id": s["id"]}, {"$set": {"lastRun": today, "nextRun": next_run}})
        created.append(order_no)
    return {"ok": True, "created": created, "count": len(created)}
