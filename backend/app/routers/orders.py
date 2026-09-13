"""Orders."""
from html import escape
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import api_router, db, strip_id, next_seq, logger, ORDER_STATUS_FLOW, audit
from ..deps import current_user, require_roles, visible_company_ids
from ..models import OrderCreate, OrderStatusIn
from ..emailer import send_email, email_shell, company_recipient


@api_router.get("/orders")
async def get_orders(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    orders = await db.orders.find({"companyId": {"$in": ids}}).sort("createdAt", -1).to_list(2000)
    return [strip_id(o) for o in orders]


@api_router.post("/orders")
async def create_order(body: OrderCreate, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    now = datetime.now(timezone.utc)
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Neu",
        "items": [it.model_dump() for it in body.items],
        "createdAt": now.isoformat(),
    }
    await db.orders.insert_one(order)
    return strip_id(order)


@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user: Annotated[dict, Depends(current_user)]):
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return strip_id(o)


@api_router.put("/orders/{order_id}/status")
async def set_order_status(order_id: str, body: OrderStatusIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    if body.status not in ORDER_STATUS_FLOW:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    update = {"status": body.status}
    if body.status == "Versendet" and not o.get("trackingNumber"):
        now = datetime.now(timezone.utc)
        eta = now + timedelta(days=2)
        update["trackingNumber"] = f"SS{now.strftime('%y%m%d')}{o['id'].split('-')[-1]}"
        update["shippedAt"] = now.isoformat()
        update["estimatedDelivery"] = eta.date().isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": update})
    await audit(user, "order.status", order_id, {"status": body.status})
    if body.status == "Versendet":
        try:
            email, cname = await company_recipient(o["companyId"])
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
async def cancel_order(order_id: str, user: Annotated[dict, Depends(current_user)]):
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o["status"] != "Neu":
        raise HTTPException(
            status_code=400,
            detail="Bestellung wird bereits bearbeitet und kann nicht mehr storniert werden.",
        )
    await db.orders.update_one(
        {"id": order_id},
        {"$set": {"status": "Storniert", "cancelledAt": datetime.now(timezone.utc).isoformat(), "cancelledBy": user["id"]}},
    )
    return {"ok": True, "status": "Storniert"}
