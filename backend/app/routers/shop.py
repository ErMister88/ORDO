"""B2C shop: public catalog, settings, guest orders, Stripe checkout."""
import os
import secrets
import jwt
import stripe
from fastapi import Depends, HTTPException, Header
from typing import Annotated, Optional
from datetime import datetime, timezone
from starlette.concurrency import run_in_threadpool

from ..core import (api_router, db, strip_id, next_seq, logger,
                    JWT_SECRET, JWT_ALGORITHM, create_token, hash_pw, verify_pw)
from ..audit_service import tenant_audit
from ..deps import (
    current_user,
    public_tenant_business_access,
    require_roles,
    tenant_business_access,
)
from ..models import ShopSettingsIn, ShopOrderIn, ShopRegisterIn, ShopLoginIn, ShopStatusIn, ShopAddressIn
from ..emailer import send_email, email_shell
from ..tenant_access import TenantBusinessAccess
from html import escape


async def _optional_uid(authorization: Optional[str] = Header(None)) -> Optional[str]:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(authorization.split(" ", 1)[1], JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload.get("sub")
    except Exception:
        return None

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = os.environ.get("APP_URL", "https://ordo-connect.app")


SHOP_SETTINGS_KEY = "shop"
SHOP_SETTINGS_DEFAULTS = {
    "freeShippingThreshold": 50.0,
    "shippingFee": 4.90,
    "newsletterDiscountPercent": 10,
    "newsletterDiscountEnabled": True,
}


async def _settings(access: TenantBusinessAccess):
    settings = await access.settings.find_one({"key": SHOP_SETTINGS_KEY})
    return {**SHOP_SETTINGS_DEFAULTS, **(settings or {})}


@api_router.get("/shop/products")
async def shop_products(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    prods = await access.products.find(
        {"active": True, "b2cPrice": {"$gt": 0}}
    ).to_list(1000)
    return [
        {
            "id": p["id"], "brand": p["brand"], "name": p["name"], "unit": p.get("unit", "kg"),
            "imageUrl": p.get("imageUrl", ""), "description": p.get("description", ""),
            "b2cPrice": p["b2cPrice"], "taxRate": p.get("taxRate", 7), "stock": p.get("stock"),
        }
        for p in prods
    ]


@api_router.get("/shop/settings")
async def shop_settings_get(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    s = await _settings(access)
    return {"freeShippingThreshold": s["freeShippingThreshold"], "shippingFee": s["shippingFee"],
            "newsletterDiscountPercent": int(s.get("newsletterDiscountPercent", 10)),
            "newsletterDiscountEnabled": bool(s.get("newsletterDiscountEnabled", True))}


@api_router.put("/shop/settings")
async def shop_settings_put(
    body: ShopSettingsIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    await access.settings.update_one(
        {"key": SHOP_SETTINGS_KEY},
        {"$set": body.model_dump(), "$setOnInsert": {"key": SHOP_SETTINGS_KEY}},
        upsert=True,
    )
    await tenant_audit(access, user, "shop.settings", "shop", body.model_dump())
    return {"ok": True, **body.model_dump()}


@api_router.post("/shop/orders")
async def create_shop_order(
    body: ShopOrderIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    uid: Annotated[Optional[str], Depends(_optional_uid)] = None,
):
    if not body.items:
        raise HTTPException(status_code=400, detail="Warenkorb ist leer")
    if not body.customer.name.strip() or "@" not in body.customer.email:
        raise HTTPException(status_code=400, detail="Name und gültige E-Mail erforderlich")
    s = await _settings(access)
    lines = []
    subtotal = 0.0
    tax_map: dict = {}
    for it in body.items:
        p = await access.products.find_one({"id": it.productId, "active": True})
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
    gross_subtotal = subtotal
    # Newsletter discount applies to B2C customers only (guests or shopusers),
    # never to B2B accounts (admin/sales/customer) who have their own pricing.
    is_b2c = True
    if uid:
        ordering_user = await db.users.find_one({"id": uid})
        if ordering_user and ordering_user.get("role") != "shopuser":
            is_b2c = False
    from .newsletter import resolve_discount
    percent = await resolve_discount(body.promoCode, access) if is_b2c else 0
    discount = 0.0
    if percent > 0:
        factor = 1 - percent / 100
        discount = round(subtotal * percent / 100, 2)
        subtotal = round(subtotal - discount, 2)
        tax_map = {r: round(v * factor, 2) for r, v in tax_map.items()}
    shipping = 0.0 if subtotal >= s["freeShippingThreshold"] else float(s["shippingFee"])
    total = round(subtotal + shipping, 2)
    tax_total = round(sum(tax_map.values()), 2)
    now = datetime.now(timezone.utc)
    seq = await next_seq("shop")
    oid = f"S-{now.year}-{seq:05d}"
    doc = {
        "id": oid, "items": lines, "customer": body.customer.model_dump(),
        "subtotal": subtotal, "shipping": shipping, "total": total,
        "discount": discount, "promoCode": (body.promoCode or "").strip().upper() or None,
        "discountPercent": percent,
        "taxBreakdown": tax_map, "taxTotal": tax_total,
        "status": "Neu", "paymentStatus": "Offen", "userId": uid,
        "token": secrets.token_urlsafe(16),
        "statusHistory": [{"status": "Neu", "at": now.isoformat()}],
        "createdAt": now.isoformat(),
    }
    await access.shop_orders.insert_one(doc)
    try:
        email = (body.customer.email or "").strip()
        if email and "@" in email:
            rows = "".join(
                f"<tr><td style='padding:6px 8px;border-bottom:1px solid #E6E8EF'>{escape(li['name'])}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{li['qty']:g}</td>"
                f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{li['price']:.2f} &euro;</td></tr>"
                for li in lines
            )
            vat_rows = "".join(
                f"<div style='text-align:right;color:#8A90A2;font-size:13px'>inkl. MwSt {r}%: {a:.2f} &euro;</div>"
                for r, a in tax_map.items()
            )
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(body.customer.name)},<br>"
                f"vielen Dank f&uuml;r Ihre Bestellung <strong>{oid}</strong>. Hier Ihre &Uuml;bersicht:</p>"
                "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                f"{rows}</table>"
                f"<div style='text-align:right;margin-top:8px'>Zwischensumme: {gross_subtotal:.2f} &euro;</div>"
                + (f"<div style='text-align:right;color:#1a7f4b'>Rabatt ({percent}%): -{discount:.2f} &euro;</div>" if discount else "")
                + f"<div style='text-align:right'>Versand: {'Gratis' if shipping == 0 else f'{shipping:.2f} €'}</div>"
                f"{vat_rows}"
                f"<div style='text-align:right;font-weight:bold;font-size:16px;margin-top:6px'>Gesamt: {total:.2f} &euro;</div>"
                "<p style='margin:16px 0 0;color:#8A90A2;font-size:13px'>Die Zahlung erfolgt sicher &uuml;ber unser Bezahlfenster. "
                "Sobald sie best&auml;tigt ist, wird Ihre Bestellung bearbeitet.</p>"
            )
            html = email_shell("Bestellbest&auml;tigung", "Ihre Bestellung ist bei uns eingegangen.", inner)
            await send_email(to=email, subject=f"Bestellbestätigung {oid}", html=html)
    except Exception as e:
        logger.warning(f"E-Mail (Bestellbestätigung Shop) fehlgeschlagen: {e}")
    return {"id": oid, "token": doc["token"], "subtotal": subtotal, "shipping": shipping, "total": total,
            "discount": discount, "discountPercent": percent,
            "taxBreakdown": tax_map, "taxTotal": tax_total}


def _authorize_shop_order(o: dict, token: Optional[str], uid: Optional[str]) -> None:
    """Owner (registered shop user) or a valid per-order token may access it."""
    if o.get("userId") and uid and o["userId"] == uid:
        return
    if o.get("token") and token and secrets.compare_digest(str(token), str(o["token"])):
        return
    # Legacy orders created before per-order tokens: allow (no token stored).
    if not o.get("token"):
        return
    raise HTTPException(status_code=403, detail="Kein Zugriff auf diese Bestellung")


@api_router.post("/shop/orders/{order_id}/checkout")
async def shop_checkout(
    order_id: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    token: Optional[str] = None,
    uid: Annotated[Optional[str], Depends(_optional_uid)] = None,
):
    o = await access.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    _authorize_shop_order(o, token, uid)
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
        await access.shop_orders.update_one({"id": order_id}, {"$set": {"stripeSessionId": session.id}})
        return {"url": session.url}
    except Exception as e:
        logger.warning(f"Shop-Checkout fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail="Zahlung konnte nicht gestartet werden (Stripe-Testschlüssel erst nach Deploy aktiv).")


@api_router.get("/shop/orders/{order_id}/payment-status")
async def shop_payment_status(
    order_id: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    token: Optional[str] = None,
    uid: Annotated[Optional[str], Depends(_optional_uid)] = None,
):
    o = await access.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    _authorize_shop_order(o, token, uid)
    if o["paymentStatus"] == "Bezahlt":
        return {"status": "Bezahlt"}
    sid = o.get("stripeSessionId")
    if sid:
        try:
            sess = await run_in_threadpool(lambda: stripe.checkout.Session.retrieve(sid))
            if sess.get("payment_status") == "paid":
                await access.shop_orders.update_one({"id": order_id}, {"$set": {"paymentStatus": "Bezahlt", "status": "Bezahlt"}})
                try:
                    email = (o.get("customer") or {}).get("email")
                    if email:
                        inner = (
                            f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Vielen Dank! Wir haben Ihre Zahlung "
                            f"f&uuml;r die Bestellung <strong>{order_id}</strong> &uuml;ber {o['total']:.2f} &euro; erhalten. "
                            "Ihre Bestellung wird jetzt bearbeitet.</p>"
                        )
                        await send_email(to=email, subject=f"Zahlung erhalten – {order_id}",
                                         html=email_shell("Zahlung erhalten", "Ihre Zahlung war erfolgreich.", inner))
                except Exception as e:
                    logger.warning(f"E-Mail (Zahlung erhalten) fehlgeschlagen: {e}")
                return {"status": "Bezahlt"}
        except Exception as e:
            logger.warning(f"Shop payment-status: {e}")
    return {"status": o.get("paymentStatus", "Offen")}


@api_router.get("/shop/orders")
async def list_shop_orders(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.shop_orders.find({}).sort("createdAt", -1).to_list(1000)
    return [strip_id(r) for r in rows]


SHOP_STATUSES = ["Neu", "Bestätigt", "In Bearbeitung", "Versendet", "Abgeschlossen", "Storniert"]


def _gls_track_url(tracking: str, zip_code: str = "") -> str:
    t = (tracking or "").strip()
    if not t:
        return ""
    z = (zip_code or "").strip()
    if z:
        return f"https://gls-group.eu/track/{t}/postalcode/{z}"
    return f"https://gls-group.eu/DE/de/paketverfolgung?match={t}"


_STATUS_MAIL = {
    "Bestätigt": ("Bestellung bestätigt",
                  "wir haben Ihre Bestellung <strong>{oid}</strong> bestätigt und bereiten sie vor."),
    "In Bearbeitung": ("Bestellung in Bearbeitung",
                       "Ihre Bestellung <strong>{oid}</strong> wird gerade bearbeitet."),
    "Versendet": ("Ihre Bestellung ist unterwegs",
                  "gute Nachrichten! Ihre Bestellung <strong>{oid}</strong> wurde versendet und ist unterwegs zu Ihnen."),
    "Abgeschlossen": ("Bestellung abgeschlossen",
                      "Ihre Bestellung <strong>{oid}</strong> ist abgeschlossen. Vielen Dank für Ihren Einkauf!"),
    "Storniert": ("Bestellung storniert",
                  "Ihre Bestellung <strong>{oid}</strong> wurde storniert. Bei Fragen erreichen Sie uns jederzeit."),
}


@api_router.put("/shop/orders/{order_id}/status")
async def update_shop_order_status(
    order_id: str,
    body: ShopStatusIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.status not in SHOP_STATUSES:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    o = await access.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    now = datetime.now(timezone.utc)
    tracking = (body.trackingNumber or "").strip()
    set_fields = {"status": body.status}
    if tracking:
        set_fields["trackingNumber"] = tracking
        set_fields["carrier"] = "GLS"
    await access.shop_orders.update_one(
        {"id": order_id},
        {"$set": set_fields,
         "$push": {"statusHistory": {"status": body.status, "at": now.isoformat()}}},
    )
    await tenant_audit(
        access,
        user,
        "shop_order_status",
        order_id,
        {"status": body.status, "tracking": tracking or None},
    )

    track_url = _gls_track_url(tracking, (o.get("customer") or {}).get("zip", "")) if tracking else ""
    mail = _STATUS_MAIL.get(body.status)
    email = (o.get("customer") or {}).get("email")
    if mail and email:
        subject, line = mail
        name = escape((o.get("customer") or {}).get("name", "").split(" ")[0] or "")
        greet = f"Hallo {name}," if name else "Hallo,"
        track_html = ""
        if body.status == "Versendet" and track_url:
            track_html = (
                f"<p style='margin:12px 0 0;color:#3A4256;font-size:14px'>Sendungsnummer (GLS): "
                f"<strong>{escape(tracking)}</strong></p>"
                f"<p style='margin:8px 0 0'><a href='{escape(track_url)}' "
                "style='color:#0B1B3D;font-weight:bold'>Sendung verfolgen</a></p>"
            )
        inner = (
            f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>{greet}</p>"
            f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>{line.format(oid=escape(order_id))}</p>"
            f"<p style='margin:0;color:#8A90A2;font-size:13px'>Aktueller Status: <strong>{escape(body.status)}</strong></p>"
            f"{track_html}"
        )
        try:
            await send_email(to=email, subject=f"{subject} – {order_id}",
                             html=email_shell(subject, "Statusaktualisierung Ihrer Bestellung", inner))
        except Exception as e:
            logger.warning(f"Status-E-Mail fehlgeschlagen: {e}")

    updated = await access.shop_orders.find_one({"id": order_id})
    return strip_id(updated)


def _shop_user_public(u: dict) -> dict:
    return {"id": u["id"], "name": u.get("name", ""), "email": u["email"],
            "address": u.get("address") or {}}


@api_router.post("/shop/register")
async def shop_register(body: ShopRegisterIn):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-Mail ist bereits registriert")
    uid = "sc-" + secrets.token_hex(5)
    doc = {"id": uid, "name": body.name.strip() or email, "email": email, "role": "shopuser",
           "hashed_password": hash_pw(body.password), "companyId": None, "salesRepId": None,
           "createdAt": datetime.now(timezone.utc).isoformat()}
    await db.users.insert_one(doc)
    return {"access_token": create_token(doc), "user": _shop_user_public(doc)}


@api_router.post("/shop/login")
async def shop_login(body: ShopLoginIn):
    email = body.email.strip().lower()
    u = await db.users.find_one({"email": email})
    if not u or not verify_pw(body.password, u["hashed_password"]):
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    return {"access_token": create_token(u), "user": _shop_user_public(u)}


@api_router.get("/shop/me")
async def shop_me(user: Annotated[dict, Depends(current_user)]):
    return _shop_user_public(user)


@api_router.put("/shop/me/address")
async def shop_save_address(body: ShopAddressIn, user: Annotated[dict, Depends(current_user)]):
    addr = body.model_dump()
    await db.users.update_one({"id": user["id"]}, {"$set": {"address": addr}})
    return {"address": addr}


@api_router.get("/shop/my-orders")
async def shop_my_orders(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.shop_orders.find({"userId": user["id"]}).sort("createdAt", -1).to_list(1000)
    return [strip_id(r) for r in rows]
