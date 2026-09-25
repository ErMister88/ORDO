"""Subscriptions (recurring orders)."""
import secrets
from fastapi import Depends, Header, HTTPException
from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..core import api_router, strip_id, next_seq
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..models import SubscriptionIn
from ..tenant_access import TenantBusinessAccess
from ..pricing_engine import PricingEngine, PricingError
from ..snapshots import clone_snapshot_items, items_total_minor, redact_internal_snapshot_fields
from ..idempotency import IdempotencyService


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
    return [strip_id(redact_internal_snapshot_fields(s)) for s in subs]


async def create_subscription(
    body: SubscriptionIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    now = datetime.now(timezone.utc)
    if operation_id:
        existing = await access.subscriptions.find_one({"operationId": operation_id})
        if existing:
            return strip_id(redact_internal_snapshot_fields(existing))
    currency = access.context.default_currency
    snapshots = []
    for item in body.items:
        try:
            product, quote = await PricingEngine(access).quote_b2b(
                body.companyId, item.productId, item.qty
            )
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        snapshots.append(quote.snapshot(product))
    sub = {
        "id": "sub-" + secrets.token_hex(5),
        "companyId": body.companyId,
        "items": snapshots,
        "currency": currency,
        "snapshotVersion": 1,
        "intervalDays": body.intervalDays,
        "active": True,
        "createdBy": user["id"],
        "salesAttribution": {
            "actorUserId": user["id"], "actorName": user.get("name", ""),
            "actorRole": user.get("role"), "membershipId": access.context.membership_id,
        },
        "createdAt": now.isoformat(),
        "nextRun": (now + timedelta(days=body.intervalDays)).date().isoformat(),
        "lastRun": None,
    }
    if operation_id:
        sub["operationId"] = operation_id
    if not await _references_are_visible(access, sub):
        raise HTTPException(status_code=404, detail="Abo-Referenz nicht gefunden")
    await access.subscriptions.insert_one(sub)
    return strip_id(redact_internal_snapshot_fields(sub))


@api_router.post("/subscriptions")
async def create_subscription_endpoint(
    body: SubscriptionIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="subscription.create",
        key=idempotency_key or "", payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_subscription(
            body, user, access, operation_id=claim.record_id
        )
        await service.complete(claim, response, {"subscriptionId": response["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="subscription_create_failed", exception=exc)
        raise


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


async def run_due_subscriptions(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
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
        run_key = f"{s['id']}:{s.get('nextRun')}"
        if operation_id:
            existing = await access.orders.find_one({"subscriptionRunKey": run_key})
            if existing:
                next_run = (now + timedelta(days=s.get("intervalDays", 28))).date().isoformat()
                await access.subscriptions.update_one(
                    {"id": s["id"], "nextRun": s.get("nextRun")},
                    {"$set": {"lastRun": today, "nextRun": next_run},
                     "$unset": {"processingRunKey": "", "processingRunLeaseUntil": ""}},
                )
                created.append(existing["id"])
                continue
            claimed = await access.subscriptions.find_one_and_update(
                {
                    "id": s["id"], "active": True, "nextRun": s.get("nextRun"),
                    "$or": [
                        {"processingRunKey": {"$exists": False}},
                        {"processingRunLeaseUntil": {"$lte": now}},
                    ],
                },
                {"$set": {
                    "processingRunKey": run_key,
                    "processingRunLeaseUntil": now + timedelta(seconds=60),
                }},
                return_document=ReturnDocument.AFTER,
            )
            if not claimed:
                continue
        currency = s.get("currency")
        if not isinstance(currency, str):
            raise HTTPException(status_code=409, detail="Abo besitzt keinen verlässlichen historischen Snapshot")
        try:
            items = clone_snapshot_items(s["items"], currency=currency)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="Abo besitzt keinen verlässlichen historischen Snapshot") from exc
        seq = await next_seq("order")
        order_no = f"B-{now.year}-{seq:05d}"
        order = {
            "id": order_no,
            "companyId": s["companyId"],
            "createdBy": user["id"],
            "status": "Neu",
            "items": items,
            "currency": currency,
            "netTotalMinor": items_total_minor(items, currency=currency),
            "snapshotVersion": 1,
            "salesAttribution": {
                **(s.get("salesAttribution") or {}),
                "executionActorUserId": user["id"],
                "subscriptionCreatedBy": s.get("createdBy"),
            },
            "fromSubscription": s["id"],
            "createdAt": now.isoformat(),
        }
        if operation_id:
            # One scheduler invocation may create several orders. Each child
            # order therefore needs a stable per-subscription operation ID.
            order["operationId"] = f"{operation_id}:{run_key}"
            order["subscriptionRunKey"] = run_key
        try:
            await access.orders.insert_one(order)
        except DuplicateKeyError:
            if not operation_id:
                raise
            existing = await access.orders.find_one({"subscriptionRunKey": run_key})
            if not existing:
                raise
            order_no = existing["id"]
        next_run = (now + timedelta(days=s.get("intervalDays", 28))).date().isoformat()
        update = {"$set": {"lastRun": today, "nextRun": next_run}}
        query = {"id": s["id"]}
        if operation_id:
            query["processingRunKey"] = run_key
            update["$unset"] = {"processingRunKey": "", "processingRunLeaseUntil": ""}
        await access.subscriptions.update_one(query, update)
        created.append(order_no)
    return {"ok": True, "created": created, "count": len(created)}


@api_router.post("/subscriptions/run")
async def run_due_subscriptions_endpoint(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="subscriptions.run",
        key=idempotency_key or "", payload={"runDate": datetime.now(timezone.utc).date().isoformat()},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await run_due_subscriptions(
            user, access, operation_id=claim.record_id
        )
        await service.complete(claim, response)
        return response
    except Exception as exc:
        await service.fail(claim, error_code="subscription_run_failed", exception=exc)
        raise
