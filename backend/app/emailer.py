"""Emergent Managed Email (Resend) — transactional notifications with a safety gate."""
import os
import re
import ipaddress
import httpx
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from typing import Optional

from .tenant_access import TenantBusinessAccess

EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "ORDO Connect by S&S")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> Optional[str]:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{EMAIL_BASE_URL}/api/v1/email/send",
            headers={"X-Email-Key": EMAIL_KEY},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json().get("id")


async def company_recipient(access: TenantBusinessAccess, company_id: str):
    c = await access.companies.find_one({"id": company_id})
    if not c:
        return None, ""
    return c.get("email"), c.get("name", "")


async def items_html(access: TenantBusinessAccess, items: list) -> str:
    rows = ""
    for it in items:
        p = await access.products.find_one({"id": it["productId"]})
        label = f"{p['brand']} {p['name']}" if p else it["productId"]
        unit = (p or {}).get("unit", "kg")
        rows += (
            "<tr>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF'>{escape(label)}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{it['qty']:g} {escape(unit)}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{it['price']:.2f} &euro;</td>"
            "</tr>"
        )
    return rows


def email_shell(heading: str, intro: str, body_inner: str) -> str:
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#F4F6FB'>"
        "<tr><td align='center' style='padding:24px'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='max-width:560px;background:#FFFFFF;border-radius:16px;overflow:hidden;font-family:Arial,Helvetica,sans-serif'>"
        "<tr><td style='background:#0B1B3D;padding:20px 24px'>"
        f"<span style='color:#FFFFFF;font-size:18px;font-weight:bold'>{escape(EMAIL_FROM_NAME)}</span></td></tr>"
        "<tr><td style='padding:24px'>"
        f"<h2 style='margin:0 0 8px;color:#0B1B3D;font-size:20px'>{escape(heading)}</h2>"
        f"<p style='margin:0 0 16px;color:#3A4256;font-size:15px;line-height:1.5'>{intro}</p>"
        f"{body_inner}"
        "<p style='margin:20px 0 0;font-size:12px;color:#8A90A2;line-height:1.5'>"
        f"Gesendet von {escape(EMAIL_FROM_NAME)}. Wir fragen Sie niemals per E-Mail nach Passwort oder Zahlungsdaten."
        "</p>"
        "</td></tr></table></td></tr></table>"
    )
