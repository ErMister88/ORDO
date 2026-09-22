"""B2C shop: public catalog, settings, guest orders, Stripe checkout."""
import os
import secrets
import hashlib
import stripe
from fastapi import Depends, Header, HTTPException, Request
from pymongo.errors import DuplicateKeyError
from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone
from starlette.concurrency import run_in_threadpool

from ..core import (api_router, db, strip_id, next_seq, logger,
                    DUMMY_HASH, hash_pw, verify_pw)
from ..audit_service import tenant_audit
from ..auth_security import (
    AuthRateLimitExceeded,
    identity_is_active,
    MongoAuthRateLimiter,
    RateLimitKey,
    issue_shop_token,
    login_rate_keys,
    retry_minutes,
)
from ..deps import (
    optional_shop_actor_id,
    public_tenant_business_access,
    require_roles,
    shop_actor_identity,
    tenant_business_access,
)
from ..models import (ShopSettingsIn, ShopOrderIn, ShopQuoteIn, ShopRegisterIn,
                      ShopLoginIn, ShopStatusIn, ShopAddressIn)
from ..emailer import send_email, email_shell
from ..tenant_access import TenantBusinessAccess
from ..money import MoneyError, amount_minor, currency_code, from_minor, included_tax_minor, to_minor
from ..pricing_engine import BasketQuote, PricingEngine, PricingError
from ..snapshots import redact_internal_snapshot_fields
from html import escape


def _client_ip(request: Request | None) -> str:
    return request.client.host if request and request.client else "unknown"


def _rate_limit_error(exc: AuthRateLimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail=(
            "Zu viele Authentifizierungsversuche. Bitte in "
            f"{retry_minutes(exc.retry_after_seconds)} Minuten erneut versuchen."
        ),
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = os.environ.get("APP_URL", "https://ordo-connect.app")


SHOP_SETTINGS_KEY = "shop"
SHOP_REGISTER_WINDOW_SECONDS = 60 * 60
SHOP_REGISTER_IP_LIMIT = 20
ORDER_TOKEN_TTL_DAYS = 30


def _order_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _public_shop_order(document: dict) -> dict:
    public = strip_id(redact_internal_snapshot_fields(document))
    public.pop("accessTokenHash", None)
    public.pop("accessTokenExpiresAt", None)
    public.pop("token", None)
    return public


async def _settings(access: TenantBusinessAccess):
    settings = await access.settings.find_one({"key": SHOP_SETTINGS_KEY})
    if not settings:
        raise HTTPException(status_code=503, detail="Shop-Preis- und Versandkonfiguration fehlt")
    return settings


async def _quote(body: ShopQuoteIn | ShopOrderIn, access: TenantBusinessAccess) -> BasketQuote:
    settings = await _settings(access)
    from .newsletter import resolve_discount
    percent = await resolve_discount(body.promoCode, access)
    try:
        return await PricingEngine(access).quote_b2c_basket(
            [item.model_dump() for item in body.items], settings,
            subscription=body.subscription, basket_discount_percent=percent,
        )
    except (PricingError, MoneyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api_router.get("/shop/products")
async def shop_products(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    prods = await access.products.find(
        {"active": True, "b2cPrice": {"$gt": 0}}
    ).to_list(1000)
    result = []
    for p in prods:
        try:
            _product, quote = await PricingEngine(access).quote_b2c(p["id"], 1)
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        result.append({
            "id": p["id"], "brand": p["brand"], "name": p["name"], "unit": p.get("unit", "kg"),
            "imageUrl": p.get("imageUrl", ""), "description": p.get("description", ""),
            "b2cPrice": quote.public()["baseUnitPrice"],
            "b2cPriceMinor": quote.base_unit_price_minor,
            "currency": access.context.default_currency,
            "taxRate": quote.tax_rate, "stock": p.get("stock"),
            "b2cTiers": [{"minQty": float(t["minQty"]), "price": from_minor(t["priceMinor"]),
                           "priceMinor": t["priceMinor"]}
                          for t in PricingEngine(access)._validated_b2c_tiers(p)],
        })
    return result


@api_router.get("/shop/settings")
async def shop_settings_get(
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    s = await _settings(access)
    return {"freeShippingThreshold": s["freeShippingThreshold"], "shippingFee": s["shippingFee"],
            "freeShippingThresholdMinor": amount_minor(s, "freeShippingThreshold", expected_currency=access.context.default_currency),
            "shippingFeeMinor": amount_minor(s, "shippingFee", expected_currency=access.context.default_currency),
            "currency": access.context.default_currency,
            "newsletterDiscountPercent": int(s.get("newsletterDiscountPercent", 0)),
            "newsletterDiscountEnabled": bool(s.get("newsletterDiscountEnabled", False)),
            "subscriptionDiscountPercent": s.get("subscriptionDiscountPercent")}


@api_router.put("/shop/settings")
async def shop_settings_put(
    body: ShopSettingsIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    currency = access.context.default_currency
    payload = {
        **body.model_dump(),
        "currency": currency,
        "freeShippingThresholdMinor": to_minor(body.freeShippingThreshold),
        "shippingFeeMinor": to_minor(body.shippingFee),
    }
    await access.settings.update_one(
        {"key": SHOP_SETTINGS_KEY},
        {"$set": payload, "$setOnInsert": {"key": SHOP_SETTINGS_KEY}},
        upsert=True,
    )
    await tenant_audit(access, user, "shop.settings", "shop", body.model_dump())
    return {"ok": True, **body.model_dump()}


@api_router.post("/shop/quote")
async def shop_quote(
    body: ShopQuoteIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    return (await _quote(body, access)).public()


@api_router.post("/shop/orders")
async def create_shop_order(
    body: ShopOrderIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    uid: Annotated[Optional[str], Depends(optional_shop_actor_id)] = None,
):
    if not body.items:
        raise HTTPException(status_code=400, detail="Warenkorb ist leer")
    if not body.customer.name.strip() or "@" not in body.customer.email:
        raise HTTPException(status_code=400, detail="Name und gültige E-Mail erforderlich")
    if body.subscription:
        ordering_user = await db.users.find_one({"id": uid}) if uid else None
        if not ordering_user or ordering_user.get("role") != "shopuser":
            raise HTTPException(status_code=401, detail="Monats-Abos erfordern ein Shop-Konto")
    basket = await _quote(body, access)
    currency = basket.currency
    lines = []
    for product, quote in basket.lines:
        snapshot = quote.snapshot(product)
        snapshot["name"] = snapshot["productName"]
        snapshot["taxMinor"] = included_tax_minor(snapshot["lineTotalMinor"], quote.tax_rate)
        lines.append(snapshot)
    subtotal_minor = basket.payable_merchandise_minor
    gross_subtotal_minor = basket.merchandise_minor
    discount_minor = basket.basket_discount_minor
    shipping_minor = basket.shipping_minor
    total_minor = basket.total_minor
    tax_map_minor = dict(basket.tax_breakdown_minor)
    tax_total_minor = sum(tax_map_minor.values())
    percent = basket.basket_discount_percent
    subtotal = from_minor(subtotal_minor)
    gross_subtotal = from_minor(gross_subtotal_minor)
    discount = from_minor(discount_minor)
    shipping = from_minor(shipping_minor)
    total = from_minor(total_minor)
    tax_map = {rate: from_minor(value) for rate, value in tax_map_minor.items()}
    tax_total = from_minor(tax_total_minor)
    now = datetime.now(timezone.utc)
    seq = await next_seq("shop")
    oid = f"S-{now.year}-{seq:05d}"
    order_token = secrets.token_urlsafe(32)
    doc = {
        "id": oid, "items": lines, "customer": body.customer.model_dump(),
        "subtotal": subtotal, "shipping": shipping, "total": total,
        "subtotalMinor": subtotal_minor, "shippingMinor": shipping_minor, "totalMinor": total_minor,
        "grossSubtotalMinor": gross_subtotal_minor,
        "discount": discount, "promoCode": (body.promoCode or "").strip().upper() or None,
        "discountMinor": discount_minor,
        "discountPercent": percent,
        "taxBreakdown": tax_map, "taxTotal": tax_total,
        "taxBreakdownMinor": tax_map_minor, "taxTotalMinor": tax_total_minor,
        "currency": currency, "snapshotVersion": 1,
        "pricingContext": "b2c", "priceSemantics": "gross",
        "subscription": body.subscription,
        "subscriptionInterval": "monthly" if body.subscription else None,
        "status": "Neu", "paymentStatus": "Offen", "userId": uid,
        "accessTokenHash": _order_token_hash(order_token),
        "accessTokenExpiresAt": (now + timedelta(days=ORDER_TOKEN_TTL_DAYS)).isoformat(),
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
    return {"id": oid, "token": order_token, "subtotal": subtotal, "shipping": shipping, "total": total,
            "subtotalMinor": subtotal_minor, "shippingMinor": shipping_minor, "totalMinor": total_minor,
            "currency": currency,
            "discount": discount, "discountPercent": percent,
            "taxBreakdown": tax_map, "taxTotal": tax_total}


def _authorize_shop_order(o: dict, token: Optional[str], uid: Optional[str]) -> None:
    """Owner (registered shop user) or a valid per-order token may access it."""
    if o.get("userId") and uid and o["userId"] == uid:
        return
    token_hash = o.get("accessTokenHash")
    expires_at = o.get("accessTokenExpiresAt")
    if isinstance(token_hash, str) and token and isinstance(expires_at, str):
        try:
            expires = datetime.fromisoformat(expires_at)
            if expires.tzinfo is None:
                raise ValueError
        except ValueError:
            expires = None
        if expires is not None and expires > datetime.now(timezone.utc) and secrets.compare_digest(
            _order_token_hash(token), token_hash
        ):
            return
    raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")


@api_router.post("/shop/orders/{order_id}/checkout")
async def shop_checkout(
    order_id: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    token: Annotated[Optional[str], Header(alias="X-Order-Token")] = None,
    uid: Annotated[Optional[str], Depends(optional_shop_actor_id)] = None,
):
    o = await access.shop_orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    _authorize_shop_order(o, token, uid)
    if o["paymentStatus"] == "Bezahlt":
        raise HTTPException(status_code=409, detail="Bereits bezahlt")
    try:
        currency = currency_code(o.get("currency", "EUR"))
        checkout_total_minor = amount_minor(o, "total", expected_currency=currency)
    except MoneyError as exc:
        raise HTTPException(status_code=409, detail="Bestellung besitzt keinen gültigen Zahlungsbetrag") from exc
    try:
        session = await run_in_threadpool(
            lambda: stripe.checkout.Session.create(
                mode="payment",
                currency=currency.lower(),
                locale="de",
                customer_email=(o.get("customer") or {}).get("email") or None,
                line_items=[{
                    "price_data": {
                        "currency": currency.lower(),
                        "unit_amount": checkout_total_minor,
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
    token: Annotated[Optional[str], Header(alias="X-Order-Token")] = None,
    uid: Annotated[Optional[str], Depends(optional_shop_actor_id)] = None,
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
    return [_public_shop_order(r) for r in rows]


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
    return _public_shop_order(updated)


def _shop_user_public(u: dict) -> dict:
    return {"id": u["id"], "name": u.get("name", ""), "email": u["email"],
            "address": u.get("address") or {}}


@api_router.post("/shop/register")
async def shop_register(body: ShopRegisterIn, request: Request = None):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    limiter = MongoAuthRateLimiter(db)
    keys = (
        RateLimitKey(
            "ip",
            _client_ip(request),
            SHOP_REGISTER_IP_LIMIT,
            SHOP_REGISTER_WINDOW_SECONDS,
        ),
    )
    try:
        await limiter.ensure_allowed("shop_registration", keys)
        # Consume the quota before creating the identity. A limiter storage
        # failure can therefore never leave an account behind after a 5xx.
        await limiter.record("shop_registration", keys)
    except AuthRateLimitExceeded as exc:
        raise _rate_limit_error(exc) from exc
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-Mail ist bereits registriert")
    uid = "sc-" + secrets.token_hex(5)
    doc = {"id": uid, "name": body.name.strip() or email, "email": email, "role": "shopuser",
           "hashed_password": hash_pw(body.password), "companyId": None, "salesRepId": None,
           "active": True, "authVersion": 0, "must_change_password": False,
           "createdAt": datetime.now(timezone.utc).isoformat()}
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="E-Mail ist bereits registriert") from exc
    return {"access_token": issue_shop_token(doc), "user": _shop_user_public(doc)}


@api_router.post("/shop/login")
async def shop_login(body: ShopLoginIn, request: Request = None):
    email = body.email.strip().lower()
    keys = login_rate_keys(email, _client_ip(request))
    limiter = MongoAuthRateLimiter(db)
    try:
        await limiter.ensure_allowed("credential_login", keys)
    except AuthRateLimitExceeded as exc:
        raise _rate_limit_error(exc) from exc
    u = await db.users.find_one({"email": email})
    password_ok = verify_pw(
        body.password,
        u.get("hashed_password", DUMMY_HASH) if u else DUMMY_HASH,
    )
    if (
        not u
        or not identity_is_active(u)
        or u.get("role") != "shopuser"
        or not password_ok
    ):
        try:
            await limiter.record("credential_login", keys)
        except AuthRateLimitExceeded as exc:
            raise _rate_limit_error(exc) from exc
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    await limiter.clear("credential_login", keys[:1])
    return {"access_token": issue_shop_token(u), "user": _shop_user_public(u)}


@api_router.get("/shop/me")
async def shop_me(user: Annotated[dict, Depends(shop_actor_identity)]):
    return _shop_user_public(user)


@api_router.put("/shop/me/address")
async def shop_save_address(
    body: ShopAddressIn,
    user: Annotated[dict, Depends(shop_actor_identity)],
):
    addr = body.model_dump()
    await db.users.update_one({"id": user["id"]}, {"$set": {"address": addr}})
    return {"address": addr}


@api_router.get("/shop/my-orders")
async def shop_my_orders(
    user: Annotated[dict, Depends(shop_actor_identity)],
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    rows = await access.shop_orders.find({"userId": user["id"]}).sort("createdAt", -1).to_list(1000)
    return [_public_shop_order(r) for r in rows]
