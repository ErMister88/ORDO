"""B2C shop: public catalog, settings, guest orders, Stripe checkout."""
import os
import stripe
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone
from starlette.concurrency import run_in_threadpool

from ..core import api_router, db, strip_id, next_seq, logger, audit
from ..deps import require_roles
from ..models import ShopSettingsIn, ShopOrderIn

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = os.environ.get("APP_URL", "https://ordo-connect.app")


async def _settings():
    s = await db.settings.find_one({"_id": "shop"})
    if not s:
        s = {"_id": "shop", "freeShippingThreshold": 50.0, "shippingFee": 4.90}
        await db.settings.insert_one(s)
    return s


@api_router.get("/shop/products")
async def shop_products():
    prods = await db.products.find({"active": True, "b2cPrice": {"$gt": 0}}).to_list(1000)
    return [
        {
            "id": p["id"], "brand": p["brand"], "name": p["name"], "unit": p.get("unit", "kg"),
            "imageUrl": p.get("imageUrl", ""), "description": p.get("description", ""),
            "b2cPrice": p["b2cPrice"], "taxRate": p.get("taxRate", 7), "stock": p.get("stock"),
        }
        for p in prods
    ]


@api_router.get("/shop/settings")
async def shop_settings_get():
    s = await _settings()
    return {"freeShippingThreshold": s["freeShippingThreshold"], "shippingFee": s["shippingFee"]}


@api_router.put("/shop/settings")
async def shop_settings_put(body: ShopSettingsIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    await db.settings.update_one({"_id": "shop"}, {"$set": body.model_dump()}, upsert=True)
    await audit(user, "shop.settings", "shop", body.model_dump())
    return {"ok": True, **body.model_dump()}


@api_router.post("/shop/orders")
async def create_shop_order(body: ShopOrderIn):
    if not body.items:
        raise HTTPException(status_code=400, detail="Warenkorb ist leer")
    if not body.customer.name.strip() or "@" not in body.customer.email:
        raise HTTPException(status_code=400, detail="Name und gültige E-Mail erforderlich")
    s = await _settings()
    lines = []
    subtotal = 0.0
    tax_map: dict = {}
    for it in body.items:
        p = await db.products.find_one({"id": it.productId, "active": True})
        if not p or not p.get("b2cPrice"):
            raise HTTPException(status_code=400, detail="Ein Produkt ist nicht mehr verfügbar")
        price = float(p["b2cPrice"])
        qty = float(it.qty)
        if qty <= 0:
            continue
        rate = int(p.get("taxRate", 7))
        gross = price * qty
        vat = gross - gross / (1 + rate / 100)
        tax_map[str(rate)] = round(tax_map.get(str(rate), 0.0) + vat, 2)
        subtotal += gross
        lines.append({"productId": p["id"], "name": f"{p['brand']} {p['name']}", "qty": qty, "price": price, "taxRate": rate})
    if not lines:
        raise HTTPException(status_code=400, detail="Warenkorb ist leer")
    subtotal = round(subtotal, 2)
    shipping = 0.0 if subtotal >= s["freeShippingThreshold"] else float(s["shippingFee"])
    total = round(subtotal + shipping, 2)
    tax_total = round(sum(tax_map.values()), 2)
    now = datetime.now(timezone.utc)
    seq = await next_seq("shop")
    oid = f"S-{now.year}-{seq:05d}"
    doc = {
        "id": oid, "items": lines, "customer": body.customer.model_dump(),
        "subtotal": subtotal, "shipping": shipping, "total": total,
        "taxBreakdown": tax_map, "taxTotal": tax_total,
        "status": "Neu", "paymentStatus": "Offen", "createdAt": now.isoformat(),
    }
    await db.shop_orders.insert_one(doc)
    return {"id": oid, "subtotal": subtotal, "shipping": shipping, "total": total,
            "taxBreakdown": tax_map, "taxTotal": tax_total}


@api_router.post("/shop/orders/{order_id}/checkout")
async def shop_checkout(order_id: str):
    o = await db.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    if o["paymentStatus"] == "Bezahlt":
        raise HTTPException(status_code=409, detail="Bereits bezahlt")
    try:
        session = await run_in_threadpool(
            lambda: stripe.checkout.Session.create(
                mode="payment",
                currency="eur",
                locale="de",
                customer_email=(o.get("customer") or {}).get("email") or None,
                line_items=[{
                    "price_data": {
                        "currency": "eur",
                        "unit_amount": round(o["total"] * 100),
                        "product_data": {"name": f"Bestellung {order_id}"},
                    },
                    "quantity": 1,
                }],
                success_url=f"{APP_URL}/?shop_paid={order_id}",
                cancel_url=f"{APP_URL}/?shop_cancel={order_id}",
                metadata={"shopOrderId": order_id},
            )
        )
        await db.shop_orders.update_one({"id": order_id}, {"$set": {"stripeSessionId": session.id}})
        return {"url": session.url}
    except Exception as e:
        logger.warning(f"Shop-Checkout fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail="Zahlung konnte nicht gestartet werden (Stripe-Testschlüssel erst nach Deploy aktiv).")


@api_router.get("/shop/orders/{order_id}/payment-status")
async def shop_payment_status(order_id: str):
    o = await db.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    if o["paymentStatus"] == "Bezahlt":
        return {"status": "Bezahlt"}
    sid = o.get("stripeSessionId")
    if sid:
        try:
            sess = await run_in_threadpool(lambda: stripe.checkout.Session.retrieve(sid))
            if sess.get("payment_status") == "paid":
                await db.shop_orders.update_one({"id": order_id}, {"$set": {"paymentStatus": "Bezahlt", "status": "Bezahlt"}})
                return {"status": "Bezahlt"}
        except Exception as e:
            logger.warning(f"Shop payment-status: {e}")
    return {"status": o.get("paymentStatus", "Offen")}


@api_router.get("/shop/orders")
async def list_shop_orders(user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    rows = await db.shop_orders.find({}).sort("createdAt", -1).to_list(1000)
    return [strip_id(r) for r in rows]
