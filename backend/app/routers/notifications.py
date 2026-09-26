"""Current-user in-app notification API."""

from typing import Annotated

from fastapi import Depends, HTTPException

from ..core import api_router, strip_id
from ..deps import current_user, tenant_business_access
from ..tenant_access import TenantBusinessAccess


def _public_notification(row: dict) -> dict:
    payload = strip_id(row)
    payload.pop("tenantId", None)
    return payload


@api_router.get("/notifications")
async def list_notifications(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.notifications.find({
        "recipientUserId": user["id"], "status": "active",
    }).sort("createdAt", -1).to_list(100)
    return [_public_notification(row) for row in rows]


@api_router.post("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    from datetime import datetime, timezone
    result = await access.notifications.update_one(
        {"id": notification_id, "recipientUserId": user["id"], "status": "active"},
        {"$set": {"readAt": datetime.now(timezone.utc)}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Benachrichtigung nicht gefunden")
    return {"ok": True}


@api_router.post("/notifications/read-all")
async def mark_all_notifications_read(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.notifications.find({
        "recipientUserId": user["id"], "status": "active", "readAt": None,
    }).to_list(500)
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    for row in rows:
        await access.notifications.update_one(
            {"id": row["id"], "recipientUserId": user["id"]}, {"$set": {"readAt": now}},
        )
    return {"ok": True, "updated": len(rows)}
