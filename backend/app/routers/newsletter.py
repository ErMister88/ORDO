"""Newsletter signup with Double-Opt-In + welcome discount code."""
import os
import secrets
from datetime import datetime, timezone
from html import escape

from fastapi import Depends, HTTPException
from fastapi.responses import HTMLResponse
from typing import Annotated

from ..core import api_router, logger
from ..deps import public_tenant_business_access
from ..models import NewsletterIn, ValidateCodeIn
from ..emailer import send_email, email_shell
from ..tenant_access import TenantBusinessAccess

APP_URL = os.environ.get("APP_URL", "https://ordo-connect.app")


def _safe_base(base: str | None) -> str:
    # Security: never trust a client-supplied base URL for links in brand-sent
    # emails (open-redirect / phishing). Always use the server-configured APP_URL.
    return APP_URL.rstrip("/")


async def _shop_settings(access: TenantBusinessAccess):
    s = await access.settings.find_one({"key": "shop"})
    return s or {}


def _new_code() -> str:
    return "SS-" + secrets.token_hex(3).upper()


async def _send_welcome(email: str, code: str, percent: int, unsub_token: str = "", base: str = "") -> None:
    unsub = f"{_safe_base(base)}/api/newsletter/unsubscribe?token={unsub_token}" if unsub_token else ""
    unsub_html = (
        f"<p style='margin:16px 0 0;color:#8A90A2;font-size:12px'>Sie k&ouml;nnen sich jederzeit "
        f"<a href='{escape(unsub)}' style='color:#8A90A2'>vom Newsletter abmelden</a>.</p>"
        if unsub else
        "<p style='margin:16px 0 0;color:#8A90A2;font-size:12px'>Sie k&ouml;nnen sich jederzeit vom Newsletter "
        "abmelden, indem Sie uns kurz eine E-Mail schreiben.</p>"
    )
    inner = (
        f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Willkommen im S&amp;S Newsletter! "
        f"Als Dankesch&ouml;n erhalten Sie <strong>{percent}% Rabatt</strong> auf Ihre Bestellungen im Kaffee-Shop.</p>"
        "<div style='margin:8px 0 4px;padding:16px;border:2px dashed #0B1B3D;border-radius:12px;text-align:center'>"
        "<div style='color:#8A90A2;font-size:12px;letter-spacing:1px'>IHR RABATTCODE</div>"
        f"<div style='color:#0B1B3D;font-size:26px;font-weight:bold;letter-spacing:2px;margin-top:4px'>{escape(code)}</div>"
        "</div>"
        f"<p style='margin:16px 0 0;color:#3A4256;font-size:14px'>Geben Sie den Code einfach im Warenkorb ein "
        f"und sparen Sie {percent}%.</p>"
        f"{unsub_html}"
    )
    html = email_shell("Willkommen &amp; Ihr Rabattcode", "Ihre Anmeldung ist best&auml;tigt.", inner)
    await send_email(to=email, subject=f"Willkommen – {percent}% Rabatt im S&S Kaffee-Shop", html=html)


@api_router.post("/newsletter/subscribe")
async def newsletter_subscribe(
    body: NewsletterIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    email = (body.email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Bitte eine gültige E-Mail-Adresse angeben")

    s = await _shop_settings(access)
    percent = int(s.get("newsletterDiscountPercent", 10))
    enabled = bool(s.get("newsletterDiscountEnabled", True))

    existing = await access.newsletter.find_one({"email": email})

    # Already confirmed → just re-send the welcome mail with the existing code.
    if existing and existing.get("confirmed"):
        code = existing["code"]
        if enabled:
            try:
                await _send_welcome(email, code, percent, existing.get("unsubToken", ""), body.baseUrl)
            except Exception as e:
                logger.warning(f"Newsletter-Willkommensmail fehlgeschlagen: {e}")
        return {"ok": True, "confirmed": True, "code": code,
                "percent": percent if enabled else 0, "enabled": enabled}

    # Pending → (re)send a confirmation link (Double-Opt-In).
    if existing:
        token = existing.get("confirmToken") or secrets.token_urlsafe(24)
        if not existing.get("confirmToken"):
            await access.newsletter.update_one({"email": email}, {"$set": {"confirmToken": token}})
    else:
        token = secrets.token_urlsafe(24)
        await access.newsletter.insert_one({
            "email": email,
            "name": (body.name or "").strip(),
            "confirmed": False,
            "confirmToken": token,
            "unsubToken": secrets.token_urlsafe(16),
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

    link = f"{_safe_base(body.baseUrl)}/api/newsletter/confirm?token={token}"
    try:
        inner = (
            f"<p style='margin:0 0 16px;color:#3A4256;font-size:15px'>Fast geschafft! Bitte best&auml;tigen Sie Ihre "
            f"Newsletter-Anmeldung. Danach erhalten Sie Ihren <strong>{percent}% Rabattcode</strong>.</p>"
            "<table role='presentation' cellpadding='0' cellspacing='0' style='margin:8px 0'>"
            f"<tr><td style='border-radius:12px;background:#0B1B3D'>"
            f"<a href='{escape(link)}' style='display:inline-block;padding:14px 28px;color:#FFFFFF;"
            "font-size:15px;font-weight:bold;text-decoration:none'>E-Mail best&auml;tigen</a></td></tr></table>"
            "<p style='margin:16px 0 0;color:#8A90A2;font-size:12px'>Falls Sie sich nicht angemeldet haben, "
            "k&ouml;nnen Sie diese E-Mail ignorieren.</p>"
        )
        html = email_shell("Bitte best&auml;tigen Sie Ihre Anmeldung", "Nur noch ein Klick.", inner)
        await send_email(to=email, subject="Bitte bestätigen Sie Ihre Newsletter-Anmeldung", html=html)
    except Exception as e:
        logger.warning(f"Newsletter-Bestätigungsmail fehlgeschlagen: {e}")

    return {"ok": True, "pending": True}


def _confirm_page(body_html: str, status: int = 200) -> HTMLResponse:
    page = (
        "<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Newsletter · S&amp;S Kaffee-Shop</title></head>"
        "<body style='margin:0;background:#F4F6FB;font-family:Arial,Helvetica,sans-serif'>"
        "<div style='max-width:480px;margin:40px auto;background:#fff;border-radius:16px;overflow:hidden'>"
        "<div style='background:#0B1B3D;padding:20px 24px;color:#fff;font-size:18px;font-weight:bold'>"
        "ORDO Connect by S&amp;S</div>"
        f"<div style='padding:28px 24px;color:#3A4256;font-size:16px;line-height:1.6'>{body_html}</div>"
        "</div></body></html>"
    )
    return HTMLResponse(page, status_code=status)


@api_router.get("/newsletter/confirm")
async def newsletter_confirm(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    sub = await access.newsletter.find_one({"confirmToken": token})
    if not sub:
        return _confirm_page("<h2 style='color:#0B1B3D'>Link ungültig</h2>"
                             "<p>Dieser Bestätigungslink ist ungültig oder wurde bereits verwendet.</p>", status=404)
    s = await _shop_settings(access)
    percent = int(s.get("newsletterDiscountPercent", 10))
    if not sub.get("confirmed"):
        code = _new_code()
        unsub_token = sub.get("unsubToken") or secrets.token_urlsafe(16)
        await access.newsletter.update_one(
            {"confirmToken": token},
            {"$set": {"confirmed": True, "code": code, "unsubToken": unsub_token,
                      "confirmedAt": datetime.now(timezone.utc).isoformat()}},
        )
        try:
            await _send_welcome(sub["email"], code, percent, unsub_token)
        except Exception as e:
            logger.warning(f"Newsletter-Willkommensmail fehlgeschlagen: {e}")
    else:
        code = sub["code"]
    return _confirm_page(
        "<h2 style='color:#0B1B3D'>E-Mail bestätigt ✓</h2>"
        f"<p>Vielen Dank! Ihr <strong>{percent}% Rabattcode</strong> lautet:</p>"
        "<div style='margin:12px 0;padding:16px;border:2px dashed #0B1B3D;border-radius:12px;text-align:center;"
        f"color:#0B1B3D;font-size:24px;font-weight:bold;letter-spacing:2px'>{escape(code)}</div>"
        "<p style='color:#8A90A2;font-size:14px'>Geben Sie den Code im Warenkorb ein und sparen Sie.</p>"
    )


@api_router.get("/newsletter/unsubscribe")
async def newsletter_unsubscribe(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    sub = await access.newsletter.find_one({"unsubToken": token})
    if not sub:
        return _confirm_page("<h2 style='color:#0B1B3D'>Link ungültig</h2>"
                             "<p>Dieser Abmeldelink ist ungültig oder Sie sind bereits abgemeldet.</p>", status=404)
    await access.newsletter.delete_one({"unsubToken": token})
    return _confirm_page(
        "<h2 style='color:#0B1B3D'>Abgemeldet ✓</h2>"
        "<p>Sie wurden erfolgreich vom Newsletter abgemeldet und erhalten keine weiteren E-Mails von uns.</p>"
    )


@api_router.post("/shop/validate-code")
async def validate_code(
    body: ValidateCodeIn,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    code = (body.code or "").strip().upper()
    if not code:
        return {"valid": False, "percent": 0}
    s = await _shop_settings(access)
    if not bool(s.get("newsletterDiscountEnabled", True)):
        return {"valid": False, "percent": 0, "detail": "Rabatt derzeit nicht aktiv"}
    sub = await access.newsletter.find_one({"code": code, "confirmed": True})
    if not sub:
        return {"valid": False, "percent": 0, "detail": "Code ungültig"}
    return {"valid": True, "percent": int(s.get("newsletterDiscountPercent", 10))}


async def resolve_discount(code: str | None, access: TenantBusinessAccess) -> int:
    """Return discount percent for a promo code, or 0 if invalid/disabled."""
    if not code:
        return 0
    code = code.strip().upper()
    s = await _shop_settings(access)
    if not bool(s.get("newsletterDiscountEnabled", True)):
        return 0
    sub = await access.newsletter.find_one({"code": code, "confirmed": True})
    if not sub:
        return 0
    return int(s.get("newsletterDiscountPercent", 10))
