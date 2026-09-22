"""Offers."""
from html import escape
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id, next_seq, logger
from ..audit_service import tenant_audit
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import OfferCreate, DecisionIn, AcceptOfferIn
from ..emailer import send_email, email_shell, company_recipient, items_html
from ..money import amount_minor, from_minor, line_total_minor, to_minor
from ..snapshots import clone_snapshot_items, items_total_minor, product_item_snapshot, redact_internal_snapshot_fields
from ..tenant_access import TenantBusinessAccess


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


@api_router.post("/offers")
async def create_offer(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    company = await access.companies.find_one({"id": body.companyId})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    needs_approval = False
    currency = access.context.default_currency
    snapshots = []
    for it in body.items:
        prod = await access.products.find_one({"id": it.productId})
        if not prod:
            raise HTTPException(status_code=400, detail="Produkt unbekannt")
        offer_minor = to_minor(it.price)
        if offer_minor < amount_minor(prod, "absoluteFloor", expected_currency=currency):
            raise HTTPException(status_code=400, detail="Preis unter absoluter Grenze – nicht zulässig")
        if offer_minor < amount_minor(prod, "salesFloor", expected_currency=currency):
            needs_approval = True
        snapshots.append(product_item_snapshot(
            prod,
            quantity=it.qty,
            unit_price_minor=offer_minor,
            currency=currency,
            price_source="offer_manual",
        ))
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
        },
        "companySnapshot": {
            "companyId": company["id"], "name": company.get("name", ""),
            "email": company.get("email", ""), "vatId": company.get("vatId", ""),
            "city": company.get("city", ""),
        },
        "reason": body.reason or ("Preis unter Vertriebslimit" if needs_approval else ""),
        "termMonths": body.termMonths,
        "createdAt": now.isoformat(),
    }
    await access.offers.insert_one(offer)
    return _offer_response(offer, user)


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


@api_router.post("/offers/{offer_id}/accept")
async def accept_offer(
    offer_id: str,
    body: AcceptOfferIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
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
        "fromOffer": offer_id,
        "customerNote": (body.note or "").strip(),
        "createdAt": now.isoformat(),
    }
    await access.orders.insert_one(order)
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Angenommen", "orderId": order_no}})
    await tenant_audit(access, user, "offer.accept", offer_id, {"orderId": order_no})
    return strip_id(redact_internal_snapshot_fields(order))


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
