"""Offers."""
from html import escape
from fastapi import Depends, Header, HTTPException
from typing import Annotated, Optional
from datetime import datetime, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..core import api_router, strip_id, next_seq, logger
from ..audit_service import tenant_audit
from ..customer_master import company_snapshot, resolve_address_snapshot
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import OfferCreate, DecisionIn, AcceptOfferIn
from ..emailer import send_email, email_shell, company_recipient, items_html
from ..money import amount_minor, from_minor, line_total_minor, to_minor
from ..pricing_engine import PricingEngine, PricingError
from ..snapshots import clone_snapshot_items, items_total_minor, product_item_snapshot, redact_internal_snapshot_fields
from ..tenant_access import TenantBusinessAccess
from ..idempotency import IdempotencyService


def _offer_response(offer: dict, user: dict) -> dict:
    payload = offer if user.get("role") == "admin" else redact_internal_snapshot_fields(offer)
    return strip_id(payload)


async def offer_references_visible(access: TenantBusinessAccess, offer: dict) -> bool:
    company_id = offer.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in offer.get("items", []):
        if item.get("snapshotVersion") == 1 and item.get("currency") == offer.get("currency"):
            continue
        product_id = item.get("productId")
        if not isinstance(product_id, str) or not await access.products.find_one({"id": product_id}):
            return False
    return True


@api_router.get("/offers")
async def get_offers(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    offers = await access.offers.find({"companyId": {"$in": ids}}).sort("createdAt", -1).to_list(1000)
    return [_offer_response(o, user) for o in offers]


async def create_offer(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if operation_id:
        existing = await access.offers.find_one({"operationId": operation_id})
        if existing:
            return _offer_response(existing, user)
    company = await access.companies.find_one({"id": body.companyId})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    needs_approval = False
    currency = access.context.default_currency
    snapshots = []
    approvals_to_consume = []
    for it in body.items:
        approved_once = False
        try:
            prod, base_quote = await PricingEngine(access).quote_b2b(
                body.companyId, it.productId, it.qty
            )
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        offer_minor = to_minor(it.price)
        if it.approvalId:
            approval_filter = {
                "id": it.approvalId, "status": "approved", "persistence": "one_time",
                "companyId": body.companyId, "productId": it.productId,
                "currency": currency,
                "consumedAt": {"$exists": False},
            }
            if user.get("role") == "sales":
                approval_filter["requestedBy"] = user["id"]
            approval = await access.price_approvals.find_one(approval_filter)
            if not approval:
                raise HTTPException(status_code=409, detail="Preisfreigabe ist nicht verfügbar")
            approved_quantity = approval.get("quantity")
            if approved_quantity is not None and it.qty < approved_quantity:
                raise HTTPException(status_code=409, detail="Preisfreigabe ist für diese Menge nicht verfügbar")
            offer_minor = approval["requestedPriceMinor"]
            approvals_to_consume.append(approval["id"])
            approved_once = True
        if offer_minor < amount_minor(prod, "absoluteFloor", expected_currency=currency):
            detail = (
                "Preis unter absoluter Grenze – nicht zulässig"
                if user.get("role") == "admin"
                else "Dieser Preis benötigt eine Freigabe durch einen Administrator."
            )
            raise HTTPException(status_code=409, detail=detail)
        if not approved_once and offer_minor < amount_minor(prod, "salesFloor", expected_currency=currency):
            needs_approval = True
        snapshot = product_item_snapshot(
            prod,
            quantity=it.qty,
            unit_price_minor=offer_minor,
            currency=currency,
            price_source="offer_manual",
        )
        snapshot.update({
            "pricingContext": "b2b", "priceSemantics": "net",
            "baseUnitPriceMinor": base_quote.final_unit_price_minor,
            "basePriceSource": base_quote.price_source,
            "taxRate": base_quote.tax_rate,
        })
        snapshots.append(snapshot)
    now = datetime.now(timezone.utc)
    seq = await next_seq("offer")
    offer_no = f"A-{now.year}-{seq:04d}"
    offer = {
        "id": offer_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Freigabe nötig" if needs_approval else "Freigegeben",
        "items": snapshots,
        "currency": currency,
        "netTotalMinor": items_total_minor(snapshots, currency=currency),
        "snapshotVersion": 1,
        "salesAttribution": {
            "actorUserId": user["id"], "actorName": user.get("name", ""),
            "actorRole": user.get("role"), "membershipId": access.context.membership_id,
            "salesRepId": company.get("assignedSalesRepId"),
            "salesRepName": company.get("assignedSalesRepName", ""),
        },
        "companySnapshot": company_snapshot(company),
        "billingAddressSnapshot": await resolve_address_snapshot(
            access, body.companyId, body.billingAddressId, preferred_type="billing"
        ),
        "deliveryAddressSnapshot": await resolve_address_snapshot(
            access, body.companyId, body.deliveryAddressId, preferred_type="shipping"
        ),
        "reason": body.reason or ("Preis unter Vertriebslimit" if needs_approval else ""),
        "termMonths": body.termMonths,
        "createdAt": now.isoformat(),
    }
    if operation_id:
        offer["operationId"] = operation_id
    claimed_approvals = []
    for approval_id in approvals_to_consume:
        claimed = await access.price_approvals.find_one_and_update(
            {"id": approval_id, "status": "approved", "consumedAt": {"$exists": False}},
            {"$set": {"consumedAt": now.isoformat(), "consumedByOfferId": offer_no}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            for claimed_id in claimed_approvals:
                await access.price_approvals.update_one(
                    {"id": claimed_id, "consumedByOfferId": offer_no},
                    {"$unset": {"consumedAt": "", "consumedByOfferId": ""}},
                )
            raise HTTPException(status_code=409, detail="Preisfreigabe wurde bereits verwendet")
        claimed_approvals.append(approval_id)
    try:
        await access.offers.insert_one(offer)
    except Exception:
        for approval_id in claimed_approvals:
            await access.price_approvals.update_one(
                {"id": approval_id, "consumedByOfferId": offer_no},
                {"$unset": {"consumedAt": "", "consumedByOfferId": ""}},
            )
        raise
    return _offer_response(offer, user)


@api_router.post("/offers")
async def create_offer_endpoint(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="offer.create",
        key=idempotency_key or "", payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_offer(
            body, user, access, operation_id=claim.record_id
        )
        await service.complete(claim, response, {"offerId": response["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="offer_create_failed", exception=exc)
        raise


@api_router.post("/offers/{offer_id}/approve")
async def approve_offer(
    offer_id: str,
    body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Freigegeben", "decisionNote": body.note}})
    try:
        email, cname = await company_recipient(access, o["companyId"])
        if email:
            rows = await items_html(access, o["items"])
            total = from_minor(o.get(
                "netTotalMinor",
                sum(line_total_minor(to_minor(it["price"]), it["qty"]) for it in o["items"]),
            ))
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(cname)},<br>"
                f"Ihr Angebot <strong>{escape(o['id'])}</strong> wurde freigegeben. Sie k&ouml;nnen es jetzt "
                f"direkt in der App in eine Bestellung umwandeln.</p>"
                "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                f"{rows}"
                f"<tr><td style='padding:8px 8px 0;font-weight:bold' colspan='2'>Gesamt netto</td>"
                f"<td style='padding:8px 8px 0;text-align:right;font-weight:bold'>{total:.2f} &euro;</td></tr>"
                "</table>"
            )
            html = email_shell("Angebot freigegeben", "Gute Neuigkeiten!", inner)
            await send_email(to=email, subject=f"Angebot {o['id']} freigegeben", html=html)
    except Exception as e:
        logger.warning(f"E-Mail (Angebot freigegeben) fehlgeschlagen: {e}")
    await tenant_audit(access, user, "offer.approve", offer_id, {})
    return {"ok": True, "status": "Freigegeben"}


async def accept_offer(
    offer_id: str,
    body: AcceptOfferIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    existing_order = await access.orders.find_one({"fromOffer": offer_id})
    if existing_order:
        await access.offers.update_one(
            {"id": offer_id},
            {"$set": {"status": "Angenommen", "orderId": existing_order["id"]}},
        )
        return strip_id(redact_internal_snapshot_fields(existing_order))
    if o["status"] != "Freigegeben":
        raise HTTPException(status_code=400, detail="Nur freigegebene Angebote können angenommen werden.")
    if o.get("orderId"):
        raise HTTPException(status_code=400, detail="Angebot wurde bereits in eine Bestellung umgewandelt.")
    now = datetime.now(timezone.utc)
    currency = o.get("currency")
    if not isinstance(currency, str):
        raise HTTPException(status_code=409, detail="Angebot besitzt keinen verlässlichen historischen Snapshot")
    try:
        items = clone_snapshot_items(o["items"], currency=currency)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Angebot besitzt keinen verlässlichen historischen Snapshot") from exc
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": o["companyId"],
        "createdBy": user["id"],
        "status": "Neu",
        "items": items,
        "currency": currency,
        "netTotalMinor": items_total_minor(items, currency=currency),
        "snapshotVersion": 1,
        "salesAttribution": o.get("salesAttribution") or {"actorUserId": o.get("createdBy")},
        "companySnapshot": o.get("companySnapshot"),
        "billingAddressSnapshot": o.get("billingAddressSnapshot"),
        "deliveryAddressSnapshot": o.get("deliveryAddressSnapshot"),
        "fromOffer": offer_id,
        "customerNote": (body.note or "").strip(),
        "createdAt": now.isoformat(),
    }
    if operation_id:
        order["operationId"] = operation_id
    order_created = True
    try:
        await access.orders.insert_one(order)
    except DuplicateKeyError:
        existing_order = await access.orders.find_one({"fromOffer": offer_id})
        if not existing_order:
            raise
        order = existing_order
        order_no = order["id"]
        order_created = False
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Angenommen", "orderId": order_no}})
    if order_created:
        await tenant_audit(access, user, "offer.accept", offer_id, {"orderId": order_no})
    return strip_id(redact_internal_snapshot_fields(order))


@api_router.post("/offers/{offer_id}/accept")
async def accept_offer_endpoint(
    offer_id: str,
    body: AcceptOfferIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access,
        actor_id=user["id"],
        operation="offer.accept",
        key=idempotency_key or "",
        payload={"offerId": offer_id, **body.model_dump(mode="json")},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await accept_offer(
            offer_id, body, user, access, operation_id=claim.record_id
        )
        await service.complete(
            claim, response, {"offerId": offer_id, "orderId": response["id"]}
        )
        return response
    except Exception as exc:
        await service.fail(claim, error_code="offer_accept_failed", exception=exc)
        raise


@api_router.post("/offers/{offer_id}/reject")
async def reject_offer(
    offer_id: str,
    body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Abgelehnt", "decisionNote": body.note}})
    return {"ok": True, "status": "Abgelehnt"}
