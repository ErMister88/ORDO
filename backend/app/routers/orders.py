"""Orders."""
from html import escape
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import api_router, strip_id, next_seq, logger, ORDER_STATUS_FLOW
from ..audit_service import tenant_audit
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import OrderCreate, OrderStatusIn, OrderItemIn  # noqa: F401
from ..emailer import send_email, email_shell, company_recipient
from ..money import amount_minor, from_minor
from ..snapshots import items_total_minor, product_item_snapshot, redact_internal_snapshot_fields
from ..tenant_access import TenantBusinessAccess


async def order_references_visible(access: TenantBusinessAccess, order: dict) -> bool:
    company_id = order.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in order.get("items", []):
        if item.get("snapshotVersion") == 1 and item.get("currency") == order.get("currency"):
            continue
        product_id = item.get("productId")
        if not isinstance(product_id, str) or not await access.products.find_one({"id": product_id}):
            return False
    for field, collection in (
        ("fromOffer", access.offers),
        ("fromSubscription", access.subscriptions),
    ):
        reference_id = order.get(field)
        if reference_id is None:
            continue
        if not isinstance(reference_id, str):
            return False
        source = await collection.find_one({"id": reference_id})
        if not source or source.get("companyId") != company_id:
            return False
    return True


@api_router.get("/orders")
async def get_orders(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    orders = await access.orders.find({"companyId": {"$in": ids}}).sort("createdAt", -1).to_list(2000)
    return [strip_id(redact_internal_snapshot_fields(o)) for o in orders]


async def _resolve_unit_price(
    access: TenantBusinessAccess,
    company_id: str,
    product: dict,
    qty: float,
) -> float:
    """Authoritative server-side price: customer price -> contract price -> standard (with volume tiers)."""
    minor, _source = await _resolve_unit_money(access, company_id, product, qty)
    return from_minor(minor)


async def _resolve_unit_money(
    access: TenantBusinessAccess,
    company_id: str,
    product: dict,
    qty: float,
) -> tuple[int, str]:
    """Preserve the established B2B priority while using exact minor units."""
    currency = access.context.default_currency
    cp = await access.customer_prices.find_one(
        {"companyId": company_id, "productId": product["id"]}
    )
    if cp and cp.get("price") is not None:
        return amount_minor(cp, "price", expected_currency=currency), "customer_price"
    ct = await access.contracts.find_one({
        "companyId": company_id,
        "productId": product["id"],
    })
    if ct and ct.get("price"):
        return amount_minor(ct, "price", expected_currency=currency), "contract_price"
    price = amount_minor(product, "standardPrice", expected_currency=currency)
    source = "standard_price"
    for t in sorted(product.get("discountTiers", []), key=lambda x: x.get("minQty", 0)):
        if qty >= t.get("minQty", 0) and t.get("price") is not None:
            price = amount_minor(t, "price", expected_currency=currency)
            source = "volume_tier"
    return price, source


@api_router.post("/orders")
async def create_order(
    body: OrderCreate,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if not body.items:
        raise HTTPException(status_code=400, detail="Bestellung enthält keine Artikel")
    items = []
    currency = access.context.default_currency
    company = await access.companies.find_one({"id": body.companyId})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    for it in body.items:
        if it.qty <= 0:
            raise HTTPException(status_code=400, detail="Ungültige Menge")
        prod = await access.products.find_one({"id": it.productId})
        if not prod or prod.get("active") is False:
            raise HTTPException(status_code=400, detail="Produkt nicht verfügbar")
        price_minor, price_source = await _resolve_unit_money(access, body.companyId, prod, it.qty)
        items.append(product_item_snapshot(
            prod,
            quantity=it.qty,
            unit_price_minor=price_minor,
            currency=currency,
            price_source=price_source,
        ))
    now = datetime.now(timezone.utc)
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Neu",
        "items": items,
        "currency": currency,
        "netTotalMinor": items_total_minor(items, currency=currency),
        "snapshotVersion": 1,
        "companySnapshot": {
            "companyId": company["id"],
            "name": company.get("name", ""),
            "email": company.get("email", ""),
            "vatId": company.get("vatId", ""),
            "city": company.get("city", ""),
        },
        "salesAttribution": {
            "actorUserId": user["id"], "actorName": user.get("name", ""),
            "actorRole": user.get("role"), "membershipId": access.context.membership_id,
            "salesRepId": company.get("assignedSalesRepId"),
        },
        "createdAt": now.isoformat(),
    }
    await access.orders.insert_one(order)
    return strip_id(redact_internal_snapshot_fields(order))


@api_router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return strip_id(redact_internal_snapshot_fields(o))


@api_router.put("/orders/{order_id}/status")
async def set_order_status(
    order_id: str,
    body: OrderStatusIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.status not in ORDER_STATUS_FLOW:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    update = {"status": body.status}
    if body.status == "Versendet" and not o.get("trackingNumber"):
        now = datetime.now(timezone.utc)
        eta = now + timedelta(days=2)
        update["trackingNumber"] = f"SS{now.strftime('%y%m%d')}{o['id'].split('-')[-1]}"
        update["shippedAt"] = now.isoformat()
        update["estimatedDelivery"] = eta.date().isoformat()
    await access.orders.update_one({"id": order_id}, {"$set": update})
    await tenant_audit(access, user, "order.status", order_id, {"status": body.status})
    if body.status == "Versendet":
        try:
            email, cname = await company_recipient(access, o["companyId"])
            if email:
                tracking = update.get("trackingNumber") or o.get("trackingNumber") or "-"
                eta = update.get("estimatedDelivery") or o.get("estimatedDelivery")
                eta_row = (
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Voraussichtliche Lieferung</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(eta))}</td></tr>"
                    if eta else ""
                )
                inner = (
                    f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(cname)},<br>"
                    f"Ihre Bestellung <strong>{escape(o['id'])}</strong> wurde versendet und ist unterwegs.</p>"
                    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Sendungsnummer</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(tracking))}</td></tr>"
                    f"{eta_row}"
                    "</table>"
                )
                html = email_shell("Bestellung versendet", "Ihre Lieferung ist auf dem Weg.", inner)
                await send_email(to=email, subject=f"Bestellung {o['id']} ist unterwegs", html=html)
        except Exception as e:
            logger.warning(f"E-Mail (Bestellung versendet) fehlgeschlagen: {e}")
    return {"ok": True, "status": body.status}


@api_router.put("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o["status"] != "Neu":
        raise HTTPException(
            status_code=400,
            detail="Bestellung wird bereits bearbeitet und kann nicht mehr storniert werden.",
        )
    await access.orders.update_one(
        {"id": order_id},
        {"$set": {"status": "Storniert", "cancelledAt": datetime.now(timezone.utc).isoformat(), "cancelledBy": user["id"]}},
    )
    return {"ok": True, "status": "Storniert"}
