"""Safe templates plus a tenant-scoped transactional email outbox."""
import os
import re
import ipaddress
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from .email_provider import EmailAttachment, TransactionalEmail, get_email_provider
from .storage import get_storage_provider
from .tenant_access import TenantBusinessAccess

EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "ORDO Connect by S&S")

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


async def send_email(
    *,
    access: TenantBusinessAccess,
    to: str,
    subject: str,
    html: str,
    idempotency_key: str,
    template_key: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    locale: str = "de",
    attachment_file_ids: tuple[str, ...] = (),
) -> dict:
    """Persist an email intent and schedule delivery without blocking the request."""
    _assert_safe_email(subject, html)
    recipient = to.strip().lower()
    if "@" not in recipient or len(recipient) > 320:
        raise ValueError("Invalid email recipient")
    if not idempotency_key or len(idempotency_key) > 180:
        raise ValueError("Invalid email idempotency key")
    if locale not in {"de", "it", "en"}:
        locale = "de"
    for file_id in attachment_file_ids:
        row = await access.uploads.find_one({"id": file_id, "status": "active"})
        if not row or row.get("visibility") != "private":
            raise ValueError("Email attachment is not an authorized private ORDO file")
    now = datetime.now(timezone.utc)
    document = {
        "id": "mail_" + secrets.token_hex(12),
        "deduplicationKey": idempotency_key,
        "templateKey": template_key,
        "locale": locale,
        "recipient": recipient,
        "subject": subject,
        "html": html,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "attachmentFileIds": list(attachment_file_ids),
        "status": "pending",
        "attempts": 0,
        "createdAt": now,
        "updatedAt": now,
        "nextAttemptAt": now,
        "retentionClass": "email_delivery",
    }
    try:
        await access.email_outbox.insert_one(document)
        row = document
    except DuplicateKeyError:
        row = await access.email_outbox.find_one({"deduplicationKey": idempotency_key})
        if not row:
            raise
    from .background_jobs import BackgroundJobQueue
    try:
        await BackgroundJobQueue(access).enqueue(
            "email.deliver",
            actor_id=access.context.actor_user_id or "system:email",
            idempotency_key=f"email:{row['id']}",
            payload={"outboxId": row["id"]},
        )
    except Exception:
        # The durable outbox row is authoritative. The persistent worker
        # repairs this narrow enqueue gap before claiming its next job.
        pass
    return row


async def deliver_outbox_email(
    access: TenantBusinessAccess,
    outbox_id: str,
    *,
    attempt: int,
) -> dict:
    """Deliver one outbox row. A sent row is an idempotent no-op."""
    existing = await access.email_outbox.find_one({"id": outbox_id})
    if not existing:
        raise RuntimeError("Email outbox row does not exist")
    if existing.get("status") == "sent":
        return {"resourceType": "email_outbox", "resourceId": outbox_id, "status": "sent"}
    now = datetime.now(timezone.utc)
    delivery_lease = secrets.token_urlsafe(18)
    row = await access.email_outbox.find_one_and_update(
        {
            "id": outbox_id,
            "$or": [
                {"status": {"$in": ["pending", "failed", "dead"]}},
                {"status": "processing", "deliveryLeaseUntil": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "status": "processing", "deliveryLease": delivery_lease,
                "deliveryLeaseUntil": now + timedelta(minutes=2), "updatedAt": now,
            },
            "$inc": {"attempts": 1},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not row:
        raise RuntimeError("Email outbox row cannot be claimed")
    attachments: list[EmailAttachment] = []
    try:
        storage = get_storage_provider() if row.get("attachmentFileIds") else None
        for file_id in row.get("attachmentFileIds") or []:
            file_row = await access.uploads.find_one({"id": file_id, "status": "active", "visibility": "private"})
            if not file_row or storage is None or storage.name != file_row.get("storageProvider"):
                raise RuntimeError("Email attachment is unavailable")
            content, content_type = await run_in_threadpool(storage.read, file_row["storageKey"])
            attachments.append(EmailAttachment(
                filename=file_row.get("originalFilename") or file_id,
                content_type=content_type,
                content=content,
            ))
        provider = get_email_provider()
        domain = (os.getenv("EMAIL_MESSAGE_ID_DOMAIN") or "ordo.invalid").strip()
        message_id = f"<{outbox_id}@{domain}>"
        receipt = await run_in_threadpool(provider.send, TransactionalEmail(
            recipient=row["recipient"], subject=row["subject"], html=row["html"],
            message_id=message_id, attachments=tuple(attachments),
        ))
    except Exception:
        status = "dead" if attempt >= 5 else "failed"
        await access.email_outbox.update_one(
            {"id": outbox_id, "status": "processing", "deliveryLease": delivery_lease},
            {
                "$set": {"status": status, "updatedAt": datetime.now(timezone.utc),
                         "lastErrorReference": "mailerr_" + secrets.token_hex(10)},
                "$unset": {"deliveryLease": "", "deliveryLeaseUntil": ""},
            },
        )
        raise
    update = await access.email_outbox.update_one(
        {"id": outbox_id, "status": "processing", "deliveryLease": delivery_lease},
        {"$set": {"status": "sent", "provider": provider.name,
                  "providerMessageReference": receipt.message_reference,
                  "sentAt": datetime.now(timezone.utc), "updatedAt": datetime.now(timezone.utc)},
         "$unset": {
             "lastErrorReference": "", "deliveryLease": "", "deliveryLeaseUntil": "",
             "html": "", "attachmentFileIds": "",
         }},
    )
    if update.matched_count != 1:
        raise RuntimeError("Email delivery lease was lost before completion")
    return {"resourceType": "email_outbox", "resourceId": outbox_id, "status": "sent"}


async def company_recipient(access: TenantBusinessAccess, company_id: str):
    c = await access.companies.find_one({"id": company_id})
    if not c:
        return None, ""
    return c.get("email"), c.get("name", "")


async def items_html(access: TenantBusinessAccess, items: list) -> str:
    rows = ""
    for it in items:
        if it.get("snapshotVersion") == 1:
            label = it.get("productName") or it["productId"]
            unit = it.get("unit", "kg")
        else:
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


_EMAIL_FOOTERS = {
    "de": ("Gesendet von", "Wir fragen Sie niemals per E-Mail nach Passwort oder Zahlungsdaten."),
    "it": ("Inviato da", "Non chiediamo mai password o dati di pagamento via email."),
    "en": ("Sent by", "We never ask for passwords or payment details by email."),
}


def email_shell(heading: str, intro: str, body_inner: str, *, locale: str = "de") -> str:
    sent_by, footer = _EMAIL_FOOTERS.get(locale, _EMAIL_FOOTERS["de"])
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
        f"{escape(sent_by)} {escape(EMAIL_FROM_NAME)}. {escape(footer)}"
        "</p>"
        "</td></tr></table></td></tr></table>"
    )
