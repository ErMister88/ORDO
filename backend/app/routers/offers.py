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
from ..tenant_access import TenantBusinessAccess


async def offer_references_visible(access: TenantBusinessAccess, offer: dict) -> bool:
    company_id = offer.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in offer.get("items", []):
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
    return [strip_id(o) for o in offers]


@api_router.post("/offers")
async def create_offer(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    needs_approval = False
    for it in body.items:
        prod = await access.products.find_one({"id": it.productId})
        if not prod:
            raise HTTPException(status_code=400, detail="Produkt unbekannt")
        if it.price < prod["absoluteFloor"]:
            raise HTTPException(status_code=400, detail="Preis unter absoluter Grenze – nicht zulässig")
        if it.price < prod["salesFloor"]:
            needs_approval = True
    now = datetime.now(timezone.utc)
    seq = await next_seq("offer")
    offer_no = f"A-{now.year}-{seq:04d}"
    offer = {
        "id": offer_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Freigabe nötig" if needs_approval else "Freigegeben",
        "items": [it.model_dump() for it in body.items],
        "reason": body.reason or ("Preis unter Vertriebslimit" if needs_approval else ""),
        "termMonths": body.termMonths,
        "createdAt": now.isoformat(),
    }
    await access.offers.insert_one(offer)
    return strip_id(offer)


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
            total = sum(it["price"] * it["qty"] for it in o["items"])
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
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": o["companyId"],
        "createdBy": user["id"],
        "status": "Neu",
        "items": o["items"],
        "fromOffer": offer_id,
        "customerNote": (body.note or "").strip(),
        "createdAt": now.isoformat(),
    }
    await access.orders.insert_one(order)
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Angenommen", "orderId": order_no}})
    await tenant_audit(access, user, "offer.accept", offer_id, {"orderId": order_no})
    return strip_id(order)


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
