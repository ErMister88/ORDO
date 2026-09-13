"""Newsletter signup + welcome discount code."""
import secrets
from datetime import datetime, timezone
from html import escape

from fastapi import HTTPException

from ..core import api_router, db, logger
from ..models import NewsletterIn, ValidateCodeIn
from ..emailer import send_email, email_shell


async def _shop_settings():
    s = await db.settings.find_one({"_id": "shop"})
    return s or {}


def _new_code() -> str:
    return "SS-" + secrets.token_hex(3).upper()


@api_router.post("/newsletter/subscribe")
async def newsletter_subscribe(body: NewsletterIn):
    email = (body.email or "").strip().lower()
    if "@" not in email or "." not in email.split("@")[-1]:
        raise HTTPException(status_code=400, detail="Bitte eine gültige E-Mail-Adresse angeben")

    s = await _shop_settings()
    percent = int(s.get("newsletterDiscountPercent", 10))
    enabled = bool(s.get("newsletterDiscountEnabled", True))

    existing = await db.newsletter.find_one({"email": email})
    if existing:
        code = existing["code"]
    else:
        code = _new_code()
        await db.newsletter.insert_one({
            "email": email,
            "name": (body.name or "").strip(),
            "code": code,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })

    if enabled:
        try:
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Willkommen im S&amp;S Newsletter! "
                f"Als Dankesch&ouml;n erhalten Sie <strong>{percent}% Rabatt</strong> auf Ihre Bestellungen im Kaffee-Shop.</p>"
                "<div style='margin:8px 0 4px;padding:16px;border:2px dashed #0B1B3D;border-radius:12px;text-align:center'>"
                "<div style='color:#8A90A2;font-size:12px;letter-spacing:1px'>IHR RABATTCODE</div>"
                f"<div style='color:#0B1B3D;font-size:26px;font-weight:bold;letter-spacing:2px;margin-top:4px'>{escape(code)}</div>"
                "</div>"
                f"<p style='margin:16px 0 0;color:#3A4256;font-size:14px'>Geben Sie den Code einfach im Warenkorb ein "
                f"und sparen Sie {percent}%.</p>"
            )
            html = email_shell("Willkommen &amp; 10% Rabatt", f"Ihr pers&ouml;nlicher Rabattcode wartet auf Sie.", inner)
            await send_email(to=email, subject=f"Willkommen – {percent}% Rabatt im S&S Kaffee-Shop", html=html)
        except Exception as e:
            logger.warning(f"Newsletter-E-Mail fehlgeschlagen: {e}")

    return {"ok": True, "code": code, "percent": percent if enabled else 0, "enabled": enabled}


@api_router.post("/shop/validate-code")
async def validate_code(body: ValidateCodeIn):
    code = (body.code or "").strip().upper()
    if not code:
        return {"valid": False, "percent": 0}
    s = await _shop_settings()
    if not bool(s.get("newsletterDiscountEnabled", True)):
        return {"valid": False, "percent": 0, "detail": "Rabatt derzeit nicht aktiv"}
    sub = await db.newsletter.find_one({"code": code})
    if not sub:
        return {"valid": False, "percent": 0, "detail": "Code ungültig"}
    return {"valid": True, "percent": int(s.get("newsletterDiscountPercent", 10))}


async def resolve_discount(code: str | None) -> int:
    """Return discount percent for a promo code, or 0 if invalid/disabled."""
    if not code:
        return 0
    code = code.strip().upper()
    s = await _shop_settings()
    if not bool(s.get("newsletterDiscountEnabled", True)):
        return 0
    sub = await db.newsletter.find_one({"code": code})
    if not sub:
        return 0
    return int(s.get("newsletterDiscountPercent", 10))
