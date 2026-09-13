"""Emergent managed push notifications (SuprSend relay)."""
import os
from datetime import datetime, timezone
from typing import Annotated, Optional

import httpx
import jwt
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel

from ..core import api_router, db, logger, JWT_SECRET, JWT_ALGORITHM
from ..deps import require_roles
from ..models import PushBroadcastIn

PUSH_BASE_URL = "https://integrations.emergentagent.com"
PUSH_KEY = os.environ.get("EMERGENT_PUSH_KEY", "placeholder")

_client = httpx.AsyncClient(
    base_url=PUSH_BASE_URL,
    headers={"X-Push-Key": PUSH_KEY},
    timeout=10.0,
)


class RegisterPushBody(BaseModel):
    user_id: str
    platform: str  # "android" | "ios"
    device_token: str


def _caller_id(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        try:
            return jwt.decode(auth[7:], JWT_SECRET, algorithms=[JWT_ALGORITHM]).get("sub")
        except Exception:
            return None
    return None


@api_router.post("/register-push", status_code=201)
async def register_push(body: RegisterPushBody, request: Request):
    # Authenticated callers are bound to their own account id (cannot spoof
    # another user's id); anonymous device ids are namespaced so they can never
    # collide with or impersonate a real user account.
    caller = _caller_id(request)
    reg_id = caller if caller else f"anon:{body.user_id}"
    await db.push_registrations.update_one(
        {"userId": reg_id},
        {"$set": {"userId": reg_id, "platform": body.platform,
                  "updatedAt": datetime.now(timezone.utc).isoformat()}},
        upsert=True,
    )
    resp = await _client.post("/api/v1/push/users/register",
                              json={"user_id": reg_id, "platform": body.platform, "device_token": body.device_token})
    if resp.status_code == 401:
        raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
    if resp.status_code >= 500:
        raise HTTPException(502, "Push provider unavailable")
    resp.raise_for_status()
    return {"status": "registered"}


async def send_push(recipients: list[str], data: dict, idempotency_key: Optional[str] = None) -> None:
    if not recipients:
        return
    if "title" not in data or "message" not in data:
        raise ValueError("data must include title and message")
    for i in range(0, len(recipients), 100):
        chunk = recipients[i:i + 100]
        payload: dict = {"recipients": chunk, "data": data}
        if idempotency_key:
            payload["$idempotency_key"] = f"{idempotency_key}-{i}"
        resp = await _client.post("/api/v1/push/trigger", json=payload)
        if resp.status_code == 401:
            raise HTTPException(500, "EMERGENT_PUSH_KEY missing or invalid")
        if resp.status_code >= 500:
            raise HTTPException(502, "Push provider unavailable")
        resp.raise_for_status()


@api_router.post("/push/broadcast")
async def push_broadcast(body: PushBroadcastIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    if not body.title.strip() or not body.message.strip():
        raise HTTPException(status_code=400, detail="Titel und Nachricht sind erforderlich")
    regs = await db.push_registrations.find({}).to_list(10000)
    recipients = [r["userId"] for r in regs if r.get("userId")]
    data: dict = {"title": body.title.strip(), "message": body.message.strip()}
    if body.actionUrl:
        data["action_url"] = body.actionUrl.strip()
    sent = 0
    try:
        await send_push(recipients, data, idempotency_key=f"bc-{datetime.now(timezone.utc).timestamp()}")
        sent = len(recipients)
    except Exception as e:
        logger.warning(f"Push-Broadcast fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail="Push konnte nicht gesendet werden (erst nach Deploy/Build aktiv).")
    return {"ok": True, "recipients": sent}


@api_router.get("/push/stats")
async def push_stats(user: Annotated[dict, Depends(require_roles("admin"))]):
    count = await db.push_registrations.count_documents({})
    return {"registered": count}
