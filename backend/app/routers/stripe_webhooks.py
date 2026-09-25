"""Authenticated Stripe event intake and tenant-safe payment operations."""

from typing import Annotated, Literal

from fastapi import Depends, Header, HTTPException, Query, Request

from ..core import api_router, strip_id
from ..deps import public_tenant_business_access, require_roles, tenant_business_access
from ..payment_integrity import (
    construct_stripe_event,
    handle_stripe_event,
    retry_stripe_event,
)
from ..tenant_access import TenantBusinessAccess

MAX_STRIPE_WEBHOOK_BYTES = 1024 * 1024


async def _raw_webhook_body(request: Request) -> bytes:
    declared_length = request.headers.get("content-length")
    if declared_length:
        try:
            length = int(declared_length)
            if length < 0:
                raise ValueError
            if length > MAX_STRIPE_WEBHOOK_BYTES:
                raise HTTPException(status_code=413, detail="Stripe-Ereignis ist zu groß")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Content-Length ist ungültig") from exc
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > MAX_STRIPE_WEBHOOK_BYTES:
            raise HTTPException(status_code=413, detail="Stripe-Ereignis ist zu groß")
    return bytes(payload)


@api_router.post("/payments/stripe/webhook")
async def stripe_webhook(
    request: Request,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    stripe_signature: Annotated[str | None, Header(alias="Stripe-Signature")] = None,
):
    payload = await _raw_webhook_body(request)
    event = construct_stripe_event(payload, stripe_signature)
    return await handle_stripe_event(access, event)


@api_router.get("/payment-events")
async def list_payment_events(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    status: Annotated[
        Literal["processing", "processed", "failed_retryable", "failed_terminal"] | None,
        Query(),
    ] = None,
):
    query = {"status": status} if status else {"status": {"$ne": "processed"}}
    rows = await access.payment_provider_events.find(query).sort("updatedAt", -1).to_list(500)
    allowed = {
        "id", "provider", "eventId", "eventType", "providerCreated",
        "providerObjectId", "resourceType", "resourceId", "status", "attempts",
        "receivedAt", "updatedAt", "processedAt", "lastErrorCode", "result",
    }
    return [
        {key: value for key, value in strip_id(row).items() if key in allowed}
        for row in rows
    ]


@api_router.post("/payment-events/{ledger_id}/retry")
async def retry_payment_event(
    ledger_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    event = await access.payment_provider_events.find_one({"id": ledger_id})
    if not event:
        raise HTTPException(status_code=404, detail="Zahlungsereignis nicht gefunden")
    return await retry_stripe_event(access, ledger_id, user)
