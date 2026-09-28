"""Secure offer document, delivery-link and public response workflows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from html import escape
import os
from pathlib import PurePath
import hmac
import secrets
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import Depends, Header, HTTPException, Query
from fastapi.responses import Response
from pymongo import ReturnDocument
from starlette.concurrency import run_in_threadpool

from ..audit_service import tenant_audit
from ..core import api_router, logger
from ..customer_activity import record_customer_activity
from ..deps import public_tenant_business_access, require_roles, tenant_business_access, visible_company_ids
from ..email_provider import email_configuration_status
from ..emailer import email_shell, send_email
from ..idempotency import IdempotencyService
from ..models import OfferDeliveryIn, OfferDocumentIn, OfferPublicLinkIn
from ..observability import report_operational_failure
from ..offer_documents import (
    OfferDocumentError, build_offer_document_snapshot, public_offer_payload, render_offer_pdf,
)
from ..storage import (
    StorageError, StorageNotConfigured, build_storage_key, get_storage_provider,
)
from ..tenant_access import TenantBusinessAccess


def _token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _app_origin() -> str:
    value = (os.getenv("APP_URL") or "").strip().rstrip("/")
    parsed = urlparse(value)
    environment = (os.getenv("APP_ENV") or "").strip().lower()
    allowed_schemes = {"http", "https"} if environment in {"dev", "development", "test", "testing"} else {"https"}
    if (
        parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username
        or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment
    ):
        raise HTTPException(status_code=503, detail="Öffentliche App-Adresse ist nicht sicher konfiguriert")
    return value


async def _staff_offer(
    offer_id: str,
    user: dict,
    access: TenantBusinessAccess,
    *,
    require_approved: bool = False,
) -> dict:
    offer = await access.offers.find_one({"id": offer_id})
    if not offer:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    if user.get("role") == "sales":
        if offer.get("companyId"):
            if offer["companyId"] not in await visible_company_ids(user, access):
                raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
        elif offer.get("createdBy") != user.get("id"):
            raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    if require_approved and offer.get("status") != "Freigegeben":
        raise HTTPException(status_code=409, detail="Nur freigegebene Angebote können versendet werden")
    return offer


async def _record_offer_activity(
    access: TenantBusinessAccess,
    offer: dict,
    *,
    actor: dict | None,
    activity_type: str,
    title: str,
) -> None:
    company_id = offer.get("companyId")
    if not isinstance(company_id, str) or not company_id:
        return
    try:
        await record_customer_activity(
            access,
            company_id=company_id,
            actor=actor or {"id": "system:offer-recipient", "name": "Angebotsempfänger"},
            activity_type=activity_type,
            title=title,
            internal=True,
            reference={"resourceType": "offer", "resourceId": offer["id"]},
        )
    except Exception:
        await report_operational_failure(
            access, logger, operation="offer.customer_activity", category="audit",
        )


async def _finalize_document(
    offer: dict,
    access: TenantBusinessAccess,
    *,
    locale: str,
    actor: dict,
) -> tuple[dict, bool]:
    existing = offer.get("documentSnapshot")
    if isinstance(existing, dict):
        return existing, False
    try:
        snapshot = build_offer_document_snapshot(offer, locale=locale)
    except OfferDocumentError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = await access.offers.find_one_and_update(
        {"id": offer["id"], "documentSnapshot": {"$exists": False}},
        {"$set": {
            "documentSnapshot": snapshot,
            "documentStatus": "FINALIZED",
            "deliveryStatus": offer.get("deliveryStatus") or "READY",
            "responseStatus": offer.get("responseStatus") or "OPEN",
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        updated = await access.offers.find_one({"id": offer["id"]})
    stored = (updated or {}).get("documentSnapshot")
    if not isinstance(stored, dict):
        raise HTTPException(status_code=409, detail="Angebotsdokument konnte nicht finalisiert werden")
    created = stored.get("finalizedAt") == snapshot.get("finalizedAt")
    if created:
        await tenant_audit(access, actor, "offer.document.finalize", offer["id"], {"locale": locale})
    return stored, created


async def _persist_pdf_if_available(
    offer: dict,
    snapshot: dict,
    access: TenantBusinessAccess,
    *,
    actor: dict,
) -> str | None:
    if isinstance(offer.get("documentFileId"), str):
        return offer["documentFileId"]
    try:
        provider = get_storage_provider()
    except StorageNotConfigured:
        return None
    pdf = render_offer_pdf(snapshot)
    file_id = "file_" + secrets.token_hex(12)
    key = build_storage_key(
        tenant_id=access.context.tenant_id,
        visibility="private",
        resource_type="offer",
        resource_id=offer["id"],
        file_id=file_id,
        content_type="application/pdf",
    )
    try:
        stored = await run_in_threadpool(provider.put, key, pdf, "application/pdf")
        now = datetime.now(timezone.utc)
        await access.uploads.insert_one({
            "id": file_id,
            "resourceType": "offer",
            "resourceId": offer["id"],
            "storageProvider": provider.name,
            "storageKey": stored.key,
            "originalFilename": f"Angebot-{PurePath(offer['id']).name}.pdf",
            "contentType": "application/pdf",
            "size": stored.size,
            "checksumSha256": stored.checksum_sha256,
            "etag": stored.etag,
            "visibility": "private",
            "createdAt": now,
            "createdBy": actor["id"],
            "status": "active",
            "sortOrder": 0,
            "isPrimary": False,
        })
        won = await access.offers.update_one(
            {"id": offer["id"], "documentFileId": {"$exists": False}},
            {"$set": {"documentFileId": file_id, "documentStorage": provider.name}},
        )
        if won.modified_count == 1:
            return file_id
        await access.uploads.delete_one({"id": file_id})
        await run_in_threadpool(provider.delete, key)
        current = await access.offers.find_one({"id": offer["id"]})
        return (current or {}).get("documentFileId")
    except (StorageError, OSError):
        await report_operational_failure(
            access, logger, operation="offer.document_storage", category="storage",
        )
        return None
    except Exception:
        try:
            await run_in_threadpool(provider.delete, key)
        except Exception:
            pass
        raise


async def _ensure_active_link(
    offer: dict,
    access: TenantBusinessAccess,
    *,
    locale: str,
    expires_in_days: int,
    actor: dict,
    token: str | None = None,
    link_id: str | None = None,
) -> tuple[str, dict]:
    now = datetime.now(timezone.utc)
    token = token or secrets.token_urlsafe(32)
    token_hash = _token_hash(token)
    existing = offer.get("publicAccess")
    if (
        link_id is not None
        and isinstance(existing, dict)
        and existing.get("id") == link_id
        and existing.get("tokenHash") == token_hash
        and existing.get("revokedAt") is None
    ):
        return f"{_app_origin()}/angebot/{token}", existing
    link = {
        "id": link_id or "olnk_" + secrets.token_hex(12),
        "tokenHash": token_hash,
        "locale": locale,
        "createdAt": now,
        "createdBy": actor["id"],
        "expiresAt": now + timedelta(days=expires_in_days),
        "revokedAt": None,
    }
    result = await access.offers.update_one(
        {"id": offer["id"], "responseStatus": {"$nin": ["ACCEPTED", "DECLINED"]}},
        {"$set": {"publicAccess": link, "responseStatus": "OPEN", "deliveryStatus": "READY"}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="Bereits beantwortete Angebote erhalten keinen neuen Link")
    await tenant_audit(access, actor, "offer.link.create", offer["id"], {"linkId": link["id"]})
    return f"{_app_origin()}/angebot/{token}", link


def _normalize_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _public_offer(token: str, access: TenantBusinessAccess) -> dict:
    if not isinstance(token, str) or len(token) < 32 or len(token) > 200:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    offer = await access.offers.find_one({"publicAccess.tokenHash": _token_hash(token)})
    if not offer:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    public = offer.get("publicAccess") or {}
    expires_at = _normalize_datetime(public.get("expiresAt"))
    if public.get("revokedAt") or expires_at is None or expires_at <= datetime.now(timezone.utc):
        if expires_at is not None and expires_at <= datetime.now(timezone.utc):
            await access.offers.update_one(
                {"id": offer["id"], "responseStatus": {"$in": ["OPEN", "VIEWED"]}},
                {"$set": {"responseStatus": "EXPIRED"}},
            )
        raise HTTPException(status_code=410, detail="Angebotslink ist nicht mehr gültig")
    return offer


@api_router.post("/offers/{offer_id}/document")
async def finalize_offer_document(
    offer_id: str,
    body: OfferDocumentIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    offer = await _staff_offer(offer_id, user, access, require_approved=True)
    snapshot, _created = await _finalize_document(offer, access, locale=body.locale, actor=user)
    file_id = await _persist_pdf_if_available(offer, snapshot, access, actor=user)
    return {
        "ok": True,
        "locale": snapshot["locale"],
        "storageMode": "private_object_storage" if file_id else "secure_on_demand",
        "fileId": file_id,
    }


@api_router.get("/offers/{offer_id}/pdf")
async def staff_offer_pdf(
    offer_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    locale: Annotated[str, Query(pattern="^(de|it|en)$")] = "de",
    disposition: Annotated[str, Query(pattern="^(inline|attachment)$")] = "inline",
):
    offer = await _staff_offer(offer_id, user, access, require_approved=True)
    snapshot, _created = await _finalize_document(offer, access, locale=locale, actor=user)
    await _persist_pdf_if_available(offer, snapshot, access, actor=user)
    return Response(
        content=render_offer_pdf(snapshot),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="Angebot-{offer_id}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@api_router.post("/offers/{offer_id}/public-link")
async def create_offer_public_link(
    offer_id: str,
    body: OfferPublicLinkIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    offer = await _staff_offer(offer_id, user, access, require_approved=True)
    await _finalize_document(offer, access, locale=body.locale, actor=user)
    url, link = await _ensure_active_link(
        offer, access, locale=body.locale, expires_in_days=body.expiresInDays,
        actor=user,
    )
    return {"url": url, "expiresAt": link["expiresAt"], "linkId": link["id"]}


@api_router.delete("/offers/{offer_id}/public-link")
async def revoke_offer_public_link(
    offer_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    offer = await _staff_offer(offer_id, user, access)
    now = datetime.now(timezone.utc)
    result = await access.offers.update_one(
        {"id": offer_id, "publicAccess.tokenHash": {"$exists": True}, "publicAccess.revokedAt": None},
        {"$set": {"publicAccess.revokedAt": now}},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=404, detail="Aktiver Angebotslink nicht gefunden")
    await access.offers.update_one(
        {"id": offer_id, "responseStatus": {"$in": ["OPEN", "VIEWED"]}},
        {"$set": {"responseStatus": "CANCELLED"}},
    )
    await tenant_audit(access, user, "offer.link.revoke", offer_id, {})
    return {"ok": True}


@api_router.post("/offers/{offer_id}/send")
async def send_offer_document(
    offer_id: str,
    body: OfferDeliveryIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="offer.delivery",
        key=idempotency_key or "", payload={"offerId": offer_id, **body.model_dump(mode="json")},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        status, _message = email_configuration_status()
        if status != "available":
            raise HTTPException(status_code=503, detail="E-Mail-Versand nicht konfiguriert oder derzeit nicht verfügbar")
        offer = await _staff_offer(offer_id, user, access, require_approved=True)
        snapshot, _created = await _finalize_document(offer, access, locale=body.locale, actor=user)
        recipient = (body.email or (offer.get("recipientSnapshot") or {}).get("email") or "").strip().lower()
        if "@" not in recipient:
            raise HTTPException(status_code=400, detail="Eine gültige Empfänger-E-Mail-Adresse ist erforderlich")
        secret = (os.getenv("JWT_SECRET") or "").encode("utf-8")
        if len(secret) < 16:
            raise HTTPException(status_code=503, detail="Sichere Link-Erzeugung ist nicht konfiguriert")
        deterministic_token = hmac.new(
            secret,
            f"offer-delivery:{access.context.tenant_id}:{offer_id}:{claim.record_id}".encode("utf-8"),
            sha256,
        ).hexdigest()
        link_url, link = await _ensure_active_link(
            offer, access, locale=body.locale, expires_in_days=30, actor=user,
            token=deterministic_token, link_id=f"olnk_{claim.record_id.removeprefix('op-')}",
        )
        file_id = await _persist_pdf_if_available(offer, snapshot, access, actor=user)
        copy = {
            "de": ("Ihr Angebot", "Angebot online ansehen"),
            "it": ("La Sua offerta", "Visualizza l'offerta online"),
            "en": ("Your quote", "View quote online"),
        }[body.locale]
        message = escape(body.message.strip())
        inner = (
            (f"<p>{message}</p>" if message else "")
            + f"<p><a href='{escape(link_url)}'>{copy[1]}</a></p>"
        )
        html = email_shell(copy[0], snapshot["offerNumber"], inner, locale=body.locale)
        row = await send_email(
            access=access,
            to=recipient,
            subject=f"{copy[0]} {snapshot['offerNumber']}",
            html=html,
            idempotency_key=f"offer:{offer_id}:delivery:{claim.record_id}",
            template_key="offer.delivery",
            resource_type="offer",
            resource_id=offer_id,
            locale=body.locale,
            attachment_file_ids=(file_id,) if file_id else (),
        )
        update: dict[str, Any] = {
            "deliveryRecipient": {"email": recipient, "savedForLater": body.saveRecipientEmail},
            "deliveryRequestedAt": datetime.now(timezone.utc),
            "deliveryRequestedBy": user["id"],
        }
        await access.offers.update_one({"id": offer_id}, {"$set": update})
        await tenant_audit(access, user, "offer.delivery.request", offer_id, {"outboxId": row["id"]})
        await _record_offer_activity(
            access, offer, actor=user,
            activity_type="offer_delivery_requested", title="Angebotsversand ausgelöst",
        )
        response = {"ok": True, "status": "READY", "outboxId": row["id"]}
        await service.complete(claim, response, {"offerId": offer_id, "outboxId": row["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="offer_delivery_failed", exception=exc)
        raise


@api_router.get("/public/offers/{token}")
async def view_public_offer(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    offer = await _public_offer(token, access)
    now = datetime.now(timezone.utc)
    transitioned = await access.offers.update_one(
        {
            "id": offer["id"],
            "publicAccess.tokenHash": _token_hash(token),
            "publicAccess.revokedAt": None,
            "publicAccess.expiresAt": {"$gt": now},
            "responseStatus": "OPEN",
        },
        {"$set": {"responseStatus": "VIEWED", "viewedAt": now}},
    )
    if transitioned.modified_count == 1:
        offer["responseStatus"] = "VIEWED"
        await tenant_audit(access, None, "offer.public.view", offer["id"], {"linkId": offer["publicAccess"]["id"]})
        await _record_offer_activity(
            access, offer, actor=None, activity_type="offer_viewed", title="Angebot geöffnet",
        )
    else:
        # A concurrent revoke or recipient decision must never leave the
        # public page rendering the stale pre-transition state.
        offer = await _public_offer(token, access)
    return public_offer_payload(offer)


@api_router.get("/public/offers/{token}/pdf")
async def public_offer_pdf(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
    disposition: Annotated[str, Query(pattern="^(inline|attachment)$")] = "inline",
):
    offer = await _public_offer(token, access)
    snapshot = offer.get("documentSnapshot")
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=404, detail="Angebotsdokument nicht gefunden")
    return Response(
        content=render_offer_pdf(snapshot), media_type="application/pdf",
        headers={
            "Content-Disposition": f'{disposition}; filename="Angebot-{offer["id"]}.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


async def _public_decision(token: str, access: TenantBusinessAccess, decision: str) -> dict:
    offer = await _public_offer(token, access)
    now = datetime.now(timezone.utc)
    token_hash = _token_hash(token)
    link_id = offer["publicAccess"]["id"]
    snapshot = offer.get("documentSnapshot") or {}
    updated = await access.offers.find_one_and_update(
        {
            "id": offer["id"],
            "publicAccess.tokenHash": token_hash,
            "publicAccess.revokedAt": None,
            "publicAccess.expiresAt": {"$gt": now},
            "responseStatus": {"$in": ["OPEN", "VIEWED"]},
        },
        {"$set": {
            "responseStatus": decision,
            "respondedAt": now,
            "acceptanceSnapshot": {
                "offerId": offer["id"],
                "documentVersion": snapshot.get("version"),
                "documentFinalizedAt": snapshot.get("finalizedAt"),
                "status": decision,
                "respondedAt": now,
                "tokenReference": link_id,
            },
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        current = await access.offers.find_one({"id": offer["id"], "publicAccess.tokenHash": token_hash})
        if current and current.get("responseStatus") == decision:
            return {"ok": True, "status": decision, "idempotentReplay": True}
        raise HTTPException(status_code=409, detail="Angebot wurde bereits beantwortet")
    action = "accept" if decision == "ACCEPTED" else "decline"
    if decision == "ACCEPTED":
        await tenant_audit(access, None, "offer.public.accept", offer["id"], {"linkId": link_id})
    else:
        await tenant_audit(access, None, "offer.public.decline", offer["id"], {"linkId": link_id})
    await _record_offer_activity(
        access, offer, actor=None,
        activity_type=f"offer_{action}ed",
        title="Angebot angenommen" if decision == "ACCEPTED" else "Angebot abgelehnt",
    )
    return {"ok": True, "status": decision, "idempotentReplay": False}


@api_router.post("/public/offers/{token}/accept")
async def accept_public_offer(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    return await _public_decision(token, access, "ACCEPTED")


@api_router.post("/public/offers/{token}/decline")
async def decline_public_offer(
    token: str,
    access: Annotated[TenantBusinessAccess, Depends(public_tenant_business_access)],
):
    return await _public_decision(token, access, "DECLINED")
