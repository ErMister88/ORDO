"""Provider-neutral transactional email transport."""

from __future__ import annotations

from dataclasses import dataclass, field
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr
import os
import smtplib
import ssl
from typing import Protocol


class EmailProviderError(RuntimeError):
    pass


class EmailNotConfigured(EmailProviderError):
    pass


@dataclass(frozen=True)
class EmailAttachment:
    filename: str
    content_type: str
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class TransactionalEmail:
    recipient: str
    subject: str
    html: str
    message_id: str
    attachments: tuple[EmailAttachment, ...] = ()


@dataclass(frozen=True)
class ProviderReceipt:
    message_reference: str


class EmailProvider(Protocol):
    name: str
    def send(self, message: TransactionalEmail) -> ProviderReceipt: ...


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def email_configuration_status() -> tuple[str, str]:
    backend = _env("EMAIL_BACKEND").lower()
    smtp_present = bool(_env("SMTP_HOST") or _env("SMTP_FROM_EMAIL"))
    if not backend and not smtp_present:
        return "not_configured", "E-Mail-Versand nicht konfiguriert"
    if backend not in {"", "smtp"}:
        return "unavailable", "Unbekannter E-Mail-Provider"
    if not _env("SMTP_HOST") or not _env("SMTP_FROM_EMAIL"):
        return "degraded", "SMTP-Konfiguration unvollständig"
    if bool(_env("SMTP_USERNAME")) != bool(_env("SMTP_PASSWORD")):
        return "degraded", "SMTP-Anmeldedaten unvollständig"
    try:
        port = int(_env("SMTP_PORT") or ("465" if _env("SMTP_SECURITY").lower() == "tls" else "587"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError:
        return "degraded", "SMTP-Port ungültig"
    security = _env("SMTP_SECURITY").lower() or "starttls"
    if security not in {"starttls", "tls", "none"}:
        return "degraded", "SMTP-Sicherheitsmodus ungültig"
    if security == "none" and _env("APP_ENV").lower() not in {"dev", "development", "test", "testing"}:
        return "unavailable", "Unverschlüsseltes SMTP ist außerhalb der Entwicklung gesperrt"
    return "available", "SMTP-Versand konfiguriert"


class SMTPEmailProvider:
    name = "smtp"

    def __init__(self) -> None:
        status, message = email_configuration_status()
        if status != "available":
            raise EmailNotConfigured(message)
        self.host = _env("SMTP_HOST")
        self.port = int(_env("SMTP_PORT") or ("465" if _env("SMTP_SECURITY").lower() == "tls" else "587"))
        self.username = _env("SMTP_USERNAME")
        self.password = _env("SMTP_PASSWORD")
        self.security = _env("SMTP_SECURITY").lower() or "starttls"
        self.from_email = _env("SMTP_FROM_EMAIL")
        self.from_name = _env("EMAIL_FROM_NAME") or "ORDO Connect by S&S"
        self.reply_to = _env("EMAIL_REPLY_TO")

    def send(self, message: TransactionalEmail) -> ProviderReceipt:
        mime = MimeMessage()
        mime["To"] = message.recipient
        mime["From"] = formataddr((self.from_name, self.from_email))
        mime["Subject"] = message.subject
        mime["Message-ID"] = message.message_id
        if self.reply_to:
            mime["Reply-To"] = self.reply_to
        mime.set_content("Diese Nachricht ist als HTML-E-Mail verfügbar.")
        mime.add_alternative(message.html, subtype="html")
        for attachment in message.attachments:
            main_type, _, sub_type = attachment.content_type.partition("/")
            mime.add_attachment(
                attachment.content, maintype=main_type or "application",
                subtype=sub_type or "octet-stream", filename=attachment.filename,
            )
        context = ssl.create_default_context()
        try:
            if self.security == "tls":
                client = smtplib.SMTP_SSL(self.host, self.port, timeout=30, context=context)
            else:
                client = smtplib.SMTP(self.host, self.port, timeout=30)
            with client:
                if self.security == "starttls":
                    client.starttls(context=context)
                if self.username:
                    client.login(self.username, self.password)
                client.send_message(mime)
        except (OSError, smtplib.SMTPException) as exc:
            raise EmailProviderError("SMTP delivery failed") from exc
        return ProviderReceipt(message_reference=message.message_id)


def get_email_provider() -> EmailProvider:
    status, message = email_configuration_status()
    if status != "available":
        raise EmailNotConfigured(message)
    return SMTPEmailProvider()
