"""Tenant-scoped device registration and provider-neutral push delivery."""

from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from ..core import api_router, logger
from ..deps import optional_shop_actor_id, public_tenant_business_access, require_roles, tenant_business_access
from ..models import PushBroadcastIn
from ..observability import report_operational_failure
from ..push_provider import PushMessage, PushProviderError, get_push_provider
from ..tenant_access import TenantBusinessAccess


class RegisterPushBody(BaseModel):
    user_id: str
    platform: str
    device_token: str


def _valid_device_token(value: str) -> bool:
    return value.startswith("ExponentPushToken[") or value.startswith("ExpoPushToken[")


@api_router.post("/register-push", status_code=201)
async def register_push(
    body: RegisterPushBody,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    caller_id: Annotated[Optional[str], Depends(optional_shop_actor_id)] = None,
):
    if body.platform not in {"android", "ios"} or not _valid_device_token(body.device_token.strip()):
        raise HTTPException(status_code=400, detail="Ungültige Push-Registrierung")
    reg_id = caller_id if caller_id else f"anon:{body.user_id}"
    await access.push_registrations.update_one(
        {"userId": reg_id},
        {"$set": {
            "userId": reg_id, "platform": body.platform,
            "deviceToken": body.device_token.strip(),
            "updatedAt": datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    return {"status": "registered"}


async def send_push(device_tokens: list[str], data: dict) -> int:
    if not device_tokens:
        return 0
    if not isinstance(data.get("title"), str) or not isinstance(data.get("message"), str):
        raise ValueError("Push title and message are required")
    provider = get_push_provider()
    sent = 0
    for index in range(0, len(device_tokens), 100):
        sent += await provider.send(PushMessage(
            device_tokens=tuple(device_tokens[index:index + 100]),
            title=data["title"], body=data["message"],
            data={key: value for key, value in data.items() if key not in {"title", "message"}},
        ))
    return sent


@api_router.post("/push/broadcast")
async def push_broadcast(
    body: PushBroadcastIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not body.title.strip() or not body.message.strip():
        raise HTTPException(status_code=400, detail="Titel und Nachricht sind erforderlich")
    registrations = await access.push_registrations.find({}).to_list(10000)
    tokens = [row["deviceToken"] for row in registrations if _valid_device_token(row.get("deviceToken", ""))]
    data: dict = {"title": body.title.strip(), "message": body.message.strip()}
    if body.actionUrl:
        data["action_url"] = body.actionUrl.strip()
    try:
        sent = await send_push(tokens, data)
    except PushProviderError as exc:
        await report_operational_failure(
            access, logger, operation="push.broadcast", category="push_delivery",
        )
        raise HTTPException(status_code=503, detail="Push-Anbieter ist nicht verfügbar") from exc
    return {"ok": True, "recipients": sent}


@api_router.get("/push/stats")
async def push_stats(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    return {"registered": await access.push_registrations.count_documents({})}
