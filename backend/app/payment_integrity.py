"""Provider-neutral payment integrity with Stripe as the current adapter.

ORDO remains authoritative for commercial documents.  This module accepts a
payment only after Stripe has authenticated the provider event and the event
matches the server-persisted checkout expectation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import secrets
from typing import Any, Mapping
from urllib.parse import quote, urlsplit
from html import escape

import stripe
from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from starlette.concurrency import run_in_threadpool

from .audit_service import tenant_audit
from .emailer import email_shell, send_email
from .money import MoneyError, amount_minor, currency_code, from_minor
from .observability import report_operational_failure
from .tenant_access import TenantBusinessAccess, TenantScopedCollection


logger = logging.getLogger("ss.payments")
PROVIDER = "stripe"
PROVIDER_ACCOUNT = "platform"
EVENT_LEASE_SECONDS = 60
SYSTEM_ACTOR = {"id": "system:stripe", "role": "system", "email": None}

PAYMENT_EVENT_TYPES = frozenset({
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "checkout.session.expired",
})
REVIEW_EVENT_PREFIXES = ("charge.dispute.", "charge.refund", "refund.")


class PaymentIntegrityError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


class PaymentEventBusy(PaymentIntegrityError):
    def __init__(self) -> None:
        super().__init__("event_processing", retryable=True)


def _environment() -> str:
    raw = (os.getenv("APP_ENV") or "").strip().lower()
    aliases = {
        "dev": "development",
        "development": "development",
        "test": "test",
        "testing": "test",
        "stage": "staging",
        "staging": "staging",
        "prod": "production",
        "production": "production",
    }
    environment = aliases.get(raw)
    if environment is None:
        raise PaymentIntegrityError("payment_environment_invalid", retryable=True)
    return environment


def stripe_api_key() -> str:
    key = (os.getenv("STRIPE_API_KEY") or "").strip()
    environment = _environment()
    if environment == "production" and not key.startswith("sk_live_"):
        raise PaymentIntegrityError("stripe_production_requires_live_key")
    if environment != "production" and not key.startswith("sk_test_"):
        raise PaymentIntegrityError("stripe_non_production_requires_test_key")
    stripe.api_key = key
    return key


def stripe_webhook_secret() -> str:
    secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not secret.startswith("whsec_"):
        raise PaymentIntegrityError("stripe_webhook_secret_missing")
    return secret


def checkout_return_urls(
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> tuple[str, str]:
    raw = (os.getenv("APP_URL") or "").strip().rstrip("/")
    environment = _environment()
    if not raw:
        if environment == "test":
            raw = "http://testserver"
        else:
            raise PaymentIntegrityError("app_url_missing")
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PaymentIntegrityError("app_url_invalid")
    if environment in {"staging", "production"} and parsed.scheme != "https":
        raise PaymentIntegrityError("app_url_must_use_https")
    context = ""
    if resource_type is not None or resource_id is not None:
        if not all(isinstance(value, str) and value for value in (resource_type, resource_id)):
            raise PaymentIntegrityError("checkout_return_context_invalid")
        context = f"&type={quote(resource_type, safe='')}&id={quote(resource_id, safe='')}"
    return f"{raw}/zahlung?status=success{context}", f"{raw}/zahlung?status=cancel{context}"


def construct_stripe_event(payload: bytes, signature: str | None) -> Mapping[str, Any]:
    if not signature or not signature.strip():
        raise HTTPException(status_code=400, detail="Stripe-Signatur fehlt")
    try:
        event = stripe.Webhook.construct_event(
            payload=payload,
            sig_header=signature,
            secret=stripe_webhook_secret(),
        )
    except PaymentIntegrityError as exc:
        logger.error("payment_config_error code=%s", exc.code)
        raise HTTPException(status_code=503, detail="Zahlungsdienst ist nicht vollständig konfiguriert") from exc
    except (ValueError, stripe.error.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Stripe-Signatur ist ungültig") from exc
    if hasattr(event, "to_dict"):
        event = event.to_dict()
    if not isinstance(event, Mapping):
        raise HTTPException(status_code=400, detail="Stripe-Ereignis ist ungültig")
    return event


def _value(source: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = source.get(key, default)
    return value


def normalize_stripe_event(event: Mapping[str, Any]) -> dict[str, Any]:
    event_id = _value(event, "id")
    event_type = _value(event, "type")
    data = _value(event, "data", {})
    provider_object = data.get("object") if isinstance(data, Mapping) else None
    if not isinstance(event_id, str) or not event_id.startswith("evt_"):
        raise HTTPException(status_code=400, detail="Stripe-Ereignis-ID ist ungültig")
    if not isinstance(event_type, str) or not event_type:
        raise HTTPException(status_code=400, detail="Stripe-Ereignistyp ist ungültig")
    if not isinstance(provider_object, Mapping):
        provider_object = {}
    metadata = provider_object.get("metadata")
    metadata = dict(metadata) if isinstance(metadata, Mapping) else {}
    created = _value(event, "created")
    created = created if isinstance(created, int) and not isinstance(created, bool) else 0
    account = _value(event, "account")
    account = account if isinstance(account, str) and account else PROVIDER_ACCOUNT
    return {
        "eventId": event_id,
        "eventType": event_type,
        "providerAccount": account,
        "providerCreated": created,
        "liveMode": _value(event, "livemode"),
        "providerObject": {
            "id": provider_object.get("id"),
            "object": provider_object.get("object"),
            "metadata": {
                key: metadata.get(key)
                for key in ("tenantId", "resourceType", "resourceId", "checkoutOperationId")
                if isinstance(metadata.get(key), str)
            },
            "amountTotal": provider_object.get("amount_total"),
            "currency": provider_object.get("currency"),
            "paymentStatus": provider_object.get("payment_status"),
            "status": provider_object.get("status"),
            "paymentIntentId": provider_object.get("payment_intent")
            if isinstance(provider_object.get("payment_intent"), str)
            else None,
            "clientReferenceId": provider_object.get("client_reference_id")
            if isinstance(provider_object.get("client_reference_id"), str)
            else None,
        },
    }


@dataclass(frozen=True)
class EventClaim:
    event_id: str
    ledger_id: str
    lease_token: str | None
    duplicate_status: str | None = None

    @property
    def is_duplicate(self) -> bool:
        return self.lease_token is None


class StripeEventLedger:
    def __init__(self, access: TenantBusinessAccess) -> None:
        self.access = access

    async def claim(self, normalized: Mapping[str, Any]) -> EventClaim:
        now = datetime.now(timezone.utc)
        lease_token = secrets.token_urlsafe(18)
        ledger_id = "stripe-event-" + secrets.token_hex(12)
        identity = {
            "provider": PROVIDER,
            "providerAccount": normalized["providerAccount"],
            "eventId": normalized["eventId"],
        }
        provider_object = normalized["providerObject"]
        document = {
            "id": ledger_id,
            **identity,
            "eventType": normalized["eventType"],
            "providerCreated": normalized["providerCreated"],
            "providerObjectId": provider_object.get("id"),
            "resourceType": (provider_object.get("metadata") or {}).get("resourceType"),
            "resourceId": (provider_object.get("metadata") or {}).get("resourceId"),
            "status": "processing",
            "attempts": 1,
            "signatureVerified": True,
            "eventSnapshot": dict(normalized),
            "leaseToken": lease_token,
            "leaseUntil": now + timedelta(seconds=EVENT_LEASE_SECONDS),
            "receivedAt": now.isoformat(),
            "updatedAt": now.isoformat(),
        }
        try:
            await self.access.payment_provider_events.insert_one(document)
            return EventClaim(normalized["eventId"], ledger_id, lease_token)
        except DuplicateKeyError:
            existing = await self.access.payment_provider_events.find_one(identity)
        if not existing:
            raise PaymentIntegrityError("provider_event_owned_by_other_tenant")
        if existing.get("eventType") != normalized["eventType"]:
            raise PaymentIntegrityError("provider_event_identity_mismatch")
        if existing.get("status") == "processed":
            return EventClaim(normalized["eventId"], existing["id"], None, "processed")
        if existing.get("status") == "failed_terminal":
            return EventClaim(normalized["eventId"], existing["id"], None, "failed_terminal")
        lease_until = existing.get("leaseUntil")
        if isinstance(lease_until, datetime) and lease_until.tzinfo is None:
            lease_until = lease_until.replace(tzinfo=timezone.utc)
        if existing.get("status") == "processing" and isinstance(lease_until, datetime) and lease_until > now:
            raise PaymentEventBusy()
        claimed = await self.access.payment_provider_events.find_one_and_update(
            {
                **identity,
                "$or": [
                    {"status": "failed_retryable"},
                    {"status": "processing", "leaseUntil": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "leaseToken": lease_token,
                    "leaseUntil": now + timedelta(seconds=EVENT_LEASE_SECONDS),
                    "updatedAt": now.isoformat(),
                    "eventSnapshot": dict(normalized),
                },
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            raise PaymentEventBusy()
        return EventClaim(normalized["eventId"], claimed["id"], lease_token)

    async def claim_retry(self, ledger_id: str) -> tuple[EventClaim, Mapping[str, Any]]:
        now = datetime.now(timezone.utc)
        lease_token = secrets.token_urlsafe(18)
        existing = await self.access.payment_provider_events.find_one({"id": ledger_id})
        snapshot = (existing or {}).get("eventSnapshot")
        if not isinstance(snapshot, Mapping):
            raise HTTPException(status_code=409, detail="Zahlungsereignis besitzt keine sichere Wiederholungsgrundlage")
        claimed = await self.access.payment_provider_events.find_one_and_update(
            {
                "id": ledger_id,
                "$or": [
                    {"status": "failed_retryable"},
                    {"status": "processing", "leaseUntil": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "leaseToken": lease_token,
                    "leaseUntil": now + timedelta(seconds=EVENT_LEASE_SECONDS),
                    "updatedAt": now.isoformat(),
                },
                "$inc": {"attempts": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            raise HTTPException(status_code=409, detail="Zahlungsereignis kann nicht erneut verarbeitet werden")
        return EventClaim(claimed["eventId"], claimed["id"], lease_token), snapshot

    async def complete(self, claim: EventClaim, result: Mapping[str, Any]) -> None:
        if not claim.lease_token:
            return
        now = datetime.now(timezone.utc)
        update = await self.access.payment_provider_events.update_one(
            {"id": claim.ledger_id, "status": "processing", "leaseToken": claim.lease_token},
            {
                "$set": {
                    "status": "processed",
                    "result": dict(result),
                    "processedAt": now.isoformat(),
                    "updatedAt": now.isoformat(),
                },
                "$unset": {"leaseToken": "", "leaseUntil": "", "lastErrorCode": ""},
            },
        )
        if update.matched_count != 1:
            raise PaymentIntegrityError("event_lease_lost", retryable=True)

    async def fail(self, claim: EventClaim, error: PaymentIntegrityError) -> None:
        if not claim.lease_token:
            return
        now = datetime.now(timezone.utc)
        result = await self.access.payment_provider_events.update_one(
            {"id": claim.ledger_id, "status": "processing", "leaseToken": claim.lease_token},
            {
                "$set": {
                    "status": "failed_retryable" if error.retryable else "failed_terminal",
                    "lastErrorCode": error.code,
                    "updatedAt": now.isoformat(),
                },
                "$unset": {"leaseToken": "", "leaseUntil": ""},
            },
        )
        if result.matched_count != 1:
            raise PaymentIntegrityError("event_failure_state_not_persisted", retryable=True)


async def _persist_event_failure(
    ledger: StripeEventLedger,
    claim: EventClaim,
    error: PaymentIntegrityError,
) -> None:
    try:
        await ledger.fail(claim, error)
    except PaymentIntegrityError as state_error:
        logger.error(
            "payment_event_failure_state_missing event_id=%s code=%s",
            claim.event_id,
            state_error.code,
        )
        raise HTTPException(
            status_code=503,
            detail="Zahlungsereignis konnte nicht sicher protokolliert werden",
        ) from state_error


def _resource_collection(access: TenantBusinessAccess, resource_type: str) -> TenantScopedCollection:
    mapping = {
        "invoice": access.invoices,
        "shop_order": access.shop_orders,
        "machine_request": access.machine_requests,
    }
    collection = mapping.get(resource_type)
    if collection is None:
        raise PaymentIntegrityError("unsupported_payment_resource")
    return collection


def _session_value(session: Any, key: str) -> Any:
    """Read StripeObject/dict values and the lightweight objects used by tests."""
    if isinstance(session, Mapping):
        return session.get(key)
    return getattr(session, key, None)


async def create_stripe_checkout(
    access: TenantBusinessAccess,
    *,
    resource_type: str,
    resource_id: str,
    operation_id: str,
    expected_amount_minor: int,
    currency: str,
    product_name: str,
    success_url: str,
    cancel_url: str,
    customer_email: str | None = None,
) -> dict[str, Any]:
    if expected_amount_minor <= 0:
        raise HTTPException(status_code=400, detail="Ungültiger Zahlungsbetrag")
    normalized_currency = currency_code(currency)
    try:
        stripe_api_key()
    except PaymentIntegrityError as exc:
        logger.error("payment_config_error code=%s", exc.code)
        raise HTTPException(status_code=503, detail="Zahlungsdienst ist nicht vollständig konfiguriert") from exc
    collection = _resource_collection(access, resource_type)
    resource = await collection.find_one({"id": resource_id})
    if not resource:
        raise HTTPException(status_code=404, detail="Zahlungsobjekt nicht gefunden")

    existing_session = resource.get("stripeSessionId")
    existing_state = resource.get("stripeCheckoutState")
    if existing_session and existing_state not in {"expired", "failed"}:
        if (
            resource.get("stripeExpectedAmountMinor") != expected_amount_minor
            or resource.get("stripeExpectedCurrency") != normalized_currency
        ):
            raise HTTPException(status_code=409, detail="Offener Checkout passt nicht mehr zum Zahlungsbetrag")
        if resource.get("stripeCheckoutUrl"):
            return {
                "url": resource["stripeCheckoutUrl"],
                "sessionId": existing_session,
                "status": resource.get("paymentStatus") or resource.get("status") or "Offen",
            }

    current_operation = resource.get("stripeCheckoutOperationId")
    restart = existing_state in {"expired", "failed"}
    if not isinstance(current_operation, str) or restart:
        query: dict[str, Any] = {"id": resource_id}
        if restart:
            query["stripeCheckoutOperationId"] = current_operation
            query["stripeCheckoutState"] = existing_state
        else:
            query["stripeCheckoutOperationId"] = {"$exists": False}
        update: dict[str, Any] = {
            "$set": {
                "paymentProvider": PROVIDER,
                "stripeCheckoutOperationId": operation_id,
                "stripeCheckoutState": "creating",
                "stripeExpectedAmountMinor": expected_amount_minor,
                "stripeExpectedCurrency": normalized_currency,
                "stripeCheckoutUpdatedAt": datetime.now(timezone.utc).isoformat(),
            }
        }
        if restart:
            update["$unset"] = {
                "stripeSessionId": "",
                "stripeCheckoutUrl": "",
                "stripePaymentIntentId": "",
            }
        await collection.update_one(query, update)
        resource = await collection.find_one({"id": resource_id})
        current_operation = resource.get("stripeCheckoutOperationId") if resource else None
    if not isinstance(current_operation, str):
        raise HTTPException(status_code=409, detail="Checkout wird bereits vorbereitet")
    if (
        resource.get("stripeExpectedAmountMinor") != expected_amount_minor
        or resource.get("stripeExpectedCurrency") != normalized_currency
    ):
        raise HTTPException(status_code=409, detail="Checkout wurde bereits mit einem anderen Zahlungsbetrag begonnen")

    metadata = {
        "tenantId": access.context.tenant_id,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "checkoutOperationId": current_operation,
    }

    def _create():
        return stripe.checkout.Session.create(
            mode="payment",
            currency=normalized_currency.lower(),
            locale="de",
            customer_email=customer_email or None,
            line_items=[{
                "price_data": {
                    "currency": normalized_currency.lower(),
                    "unit_amount": expected_amount_minor,
                    "product_data": {"name": product_name},
                },
                "quantity": 1,
            }],
            client_reference_id=resource_id,
            metadata=metadata,
            success_url=success_url,
            cancel_url=cancel_url,
            idempotency_key=f"{access.context.tenant_id}:{current_operation}",
        )

    try:
        session = await run_in_threadpool(_create)
    except Exception as exc:
        logger.warning(
            "stripe_checkout_failed tenant=%s resource_type=%s resource_id=%s operation_id=%s",
            access.context.tenant_id,
            resource_type,
            resource_id,
            current_operation,
        )
        raise HTTPException(status_code=502, detail="Zahlung konnte nicht gestartet werden") from exc
    session_id = _session_value(session, "id")
    session_url = _session_value(session, "url")
    if not isinstance(session_id, str) or not isinstance(session_url, str):
        raise HTTPException(status_code=502, detail="Zahlungsdienst lieferte keine gültige Checkout-Sitzung")
    result = await collection.update_one(
        {"id": resource_id, "stripeCheckoutOperationId": current_operation},
        {
            "$set": {
                "stripeSessionId": session_id,
                "stripeCheckoutUrl": session_url,
                "stripeCheckoutState": "pending",
                "stripeCheckoutUpdatedAt": datetime.now(timezone.utc).isoformat(),
            }
        },
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="Checkout-Zuordnung konnte nicht bestätigt werden")
    try:
        await tenant_audit(
            access,
            SYSTEM_ACTOR,
            "payment.checkout_initiated",
            resource_id,
            {
                "provider": PROVIDER,
                "resourceType": resource_type,
                "providerSessionId": session_id,
                "operationId": current_operation,
            },
        )
    except Exception:
        await report_operational_failure(
            access, logger,
            operation="payment.checkout_audit",
            category="audit_persistence",
        )
    return {"url": session_url, "sessionId": session_id, "status": "Offen"}


def _provider_payment_record(
    *,
    resource_id: str,
    amount: int,
    currency: str,
    session_id: str,
    payment_intent_id: str | None,
    event_id: str,
    paid_at: str,
) -> dict[str, Any]:
    return {
        "id": "pay-" + secrets.token_hex(8),
        "amount": from_minor(amount),
        "amountMinor": amount,
        "currency": currency,
        "method": "card",
        "reference": session_id,
        "provider": PROVIDER,
        "providerSessionId": session_id,
        "providerPaymentId": payment_intent_id,
        "providerEventId": event_id,
        "operationId": f"stripe:{session_id}",
        "paidAt": paid_at,
        "createdBy": SYSTEM_ACTOR["id"],
        "createdAt": paid_at,
        "resourceId": resource_id,
    }


async def _settle_invoice(
    access: TenantBusinessAccess,
    invoice: Mapping[str, Any],
    *,
    amount: int,
    currency: str,
    session_id: str,
    payment_intent_id: str | None,
    event_id: str,
    event_created: int,
) -> bool:
    records = invoice.get("paymentRecords") or []
    if any(record.get("providerSessionId") == session_id for record in records if isinstance(record, Mapping)):
        return False
    try:
        total = amount_minor(invoice, "amount", expected_currency=currency)
    except MoneyError as exc:
        raise PaymentIntegrityError("invoice_money_invalid") from exc
    paid = invoice.get("paidAmountMinor", 0)
    if not isinstance(paid, int) or isinstance(paid, bool) or paid < 0:
        raise PaymentIntegrityError("invoice_paid_amount_invalid")
    if invoice.get("status") == "Storniert":
        raise PaymentIntegrityError("invoice_cancelled")
    if paid + amount != total:
        raise PaymentIntegrityError("invoice_amount_mismatch")
    paid_at = datetime.now(timezone.utc).isoformat()
    payment = _provider_payment_record(
        resource_id=invoice["id"], amount=amount, currency=currency,
        session_id=session_id, payment_intent_id=payment_intent_id,
        event_id=event_id, paid_at=paid_at,
    )
    payment["invoiceId"] = invoice["id"]
    payment["companyId"] = invoice.get("companyId")
    expected: dict[str, Any] = {
        "id": invoice["id"],
        "stripeSessionId": session_id,
        "status": invoice.get("status", "Offen"),
    }
    expected["paidAmountMinor"] = paid if "paidAmountMinor" in invoice else {"$exists": False}
    result = await access.invoices.update_one(
        expected,
        {
            "$set": {
                "paidAmountMinor": total,
                "status": "Bezahlt",
                "paymentMethod": "card",
                "paidAt": paid_at,
                "stripeCheckoutState": "paid",
                "stripePaymentStatus": "paid",
                "stripePaymentIntentId": payment_intent_id,
                "stripeLastEventId": event_id,
                "stripeLastEventCreated": event_created,
            },
            "$push": {"paymentRecords": payment},
        },
    )
    if result.matched_count != 1:
        current = await access.invoices.find_one({"id": invoice["id"]})
        current_records = (current or {}).get("paymentRecords") or []
        if any(record.get("providerSessionId") == session_id for record in current_records if isinstance(record, Mapping)):
            return False
        raise PaymentIntegrityError("invoice_concurrent_change")
    return True


async def _settle_simple_resource(
    collection: TenantScopedCollection,
    resource: Mapping[str, Any],
    *,
    resource_type: str,
    amount: int,
    currency: str,
    session_id: str,
    payment_intent_id: str | None,
    event_id: str,
    event_created: int,
) -> bool:
    records = resource.get("paymentRecords") or []
    if any(record.get("providerSessionId") == session_id for record in records if isinstance(record, Mapping)):
        return False
    if resource.get("paymentStatus") == "Bezahlt":
        raise PaymentIntegrityError("resource_already_paid_by_other_payment")
    paid_at = datetime.now(timezone.utc).isoformat()
    payment = _provider_payment_record(
        resource_id=resource["id"], amount=amount, currency=currency,
        session_id=session_id, payment_intent_id=payment_intent_id,
        event_id=event_id, paid_at=paid_at,
    )
    set_fields: dict[str, Any] = {
        "paymentStatus": "Bezahlt",
        "paidAt": paid_at,
        "stripeCheckoutState": "paid",
        "stripePaymentStatus": "paid",
        "stripePaymentIntentId": payment_intent_id,
        "stripeLastEventId": event_id,
        "stripeLastEventCreated": event_created,
    }
    if resource_type == "shop_order":
        set_fields["status"] = "Bezahlt"
    elif resource_type == "machine_request":
        set_fields["status"] = "Gekauft"
    result = await collection.update_one(
        {"id": resource["id"], "stripeSessionId": session_id, "paymentStatus": {"$ne": "Bezahlt"}},
        {"$set": set_fields, "$push": {"paymentRecords": payment}},
    )
    if result.matched_count != 1:
        current = await collection.find_one({"id": resource["id"]})
        current_records = (current or {}).get("paymentRecords") or []
        if any(record.get("providerSessionId") == session_id for record in current_records if isinstance(record, Mapping)):
            return False
        raise PaymentIntegrityError("resource_concurrent_change")
    return True


async def _send_shop_payment_confirmation_once(
    access: TenantBusinessAccess,
    resource: Mapping[str, Any],
    event_id: str,
) -> None:
    customer = resource.get("customer")
    email = customer.get("email") if isinstance(customer, Mapping) else None
    if not isinstance(email, str) or not email.strip():
        return
    claimed = await access.shop_orders.update_one(
        {
            "id": resource["id"],
            "paymentStatus": "Bezahlt",
            "paymentConfirmationEmailState": {"$exists": False},
        },
        {"$set": {
            "paymentConfirmationEmailState": "sending",
            "paymentConfirmationEmailEventId": event_id,
            "paymentConfirmationEmailClaimedAt": datetime.now(timezone.utc).isoformat(),
        }},
    )
    if claimed.matched_count != 1:
        return
    order_id = str(resource["id"])
    total_minor = resource.get("totalMinor")
    currency = resource.get("currency")
    total_text = ""
    if isinstance(total_minor, int) and not isinstance(total_minor, bool) and isinstance(currency, str):
        total_text = f"{total_minor // 100},{total_minor % 100:02d} {currency.upper()}"
    inner = (
        "<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>"
        "Vielen Dank! Wir haben Ihre Zahlung für die Bestellung "
        f"<strong>{escape(order_id)}</strong>"
        f"{f' über {escape(total_text)}' if total_text else ''} erhalten. "
        "Ihre Bestellung wird jetzt bearbeitet.</p>"
    )
    try:
        await send_email(
            access=access,
            to=email.strip(),
            subject=f"Zahlung erhalten – {order_id}",
            html=email_shell("Zahlung erhalten", "Ihre Zahlung war erfolgreich.", inner),
            idempotency_key=f"shop-order:{order_id}:payment-confirmed",
            template_key="shop.order.payment_confirmed",
            resource_type="order",
            resource_id=order_id,
        )
        await access.shop_orders.update_one(
            {
                "id": resource["id"],
                "paymentConfirmationEmailState": "sending",
                "paymentConfirmationEmailEventId": event_id,
            },
            {"$set": {
                "paymentConfirmationEmailState": "queued",
                "paymentConfirmationEmailQueuedAt": datetime.now(timezone.utc).isoformat(),
            }},
        )
    except Exception:
        await access.shop_orders.update_one(
            {
                "id": resource["id"],
                "paymentConfirmationEmailState": "sending",
                "paymentConfirmationEmailEventId": event_id,
            },
            {"$set": {"paymentConfirmationEmailState": "failed_terminal"}},
        )
        await report_operational_failure(
            access, logger,
            operation="payment.shop_confirmation_email",
            category="email_delivery",
        )


async def _set_non_paid_state(
    collection: TenantScopedCollection,
    resource: Mapping[str, Any],
    *,
    state: str,
    event_id: str,
    event_created: int,
) -> bool:
    if resource.get("paymentStatus") == "Bezahlt" or resource.get("status") == "Bezahlt":
        return False
    last_created = resource.get("stripeLastEventCreated", -1)
    if isinstance(last_created, int) and last_created > event_created:
        return False
    result = await collection.update_one(
        {
            "id": resource["id"],
            "stripeSessionId": resource.get("stripeSessionId"),
            "paymentStatus": {"$ne": "Bezahlt"},
            "$or": [
                {"stripeLastEventCreated": {"$exists": False}},
                {"stripeLastEventCreated": {"$lte": event_created}},
            ],
        },
        {"$set": {
            "stripeCheckoutState": state,
            "stripePaymentStatus": state,
            "stripeLastEventId": event_id,
            "stripeLastEventCreated": event_created,
        }},
    )
    return result.matched_count == 1


async def process_normalized_stripe_event(
    access: TenantBusinessAccess,
    normalized: Mapping[str, Any],
) -> dict[str, Any]:
    event_type = normalized["eventType"]
    live_mode = normalized.get("liveMode")
    environment = _environment()
    if environment != "production" and live_mode is not False:
        raise PaymentIntegrityError("provider_mode_mismatch")
    if environment == "production" and live_mode is not True:
        raise PaymentIntegrityError("provider_mode_mismatch")
    if event_type not in PAYMENT_EVENT_TYPES:
        review = any(event_type.startswith(prefix) for prefix in REVIEW_EVENT_PREFIXES)
        if review:
            await tenant_audit(
                access, SYSTEM_ACTOR, "payment.provider_review_required",
                normalized["eventId"], {"eventType": event_type, "provider": PROVIDER},
            )
        return {"outcome": "review_required" if review else "ignored", "eventType": event_type}

    provider_object = normalized["providerObject"]
    if provider_object.get("object") != "checkout.session":
        raise PaymentIntegrityError("provider_object_mismatch")
    metadata = provider_object.get("metadata") or {}
    if metadata.get("tenantId") != access.context.tenant_id:
        raise PaymentIntegrityError("tenant_mismatch")
    resource_type = metadata.get("resourceType")
    resource_id = metadata.get("resourceId")
    checkout_operation_id = metadata.get("checkoutOperationId")
    session_id = provider_object.get("id")
    if not all(isinstance(value, str) and value for value in (
        resource_type, resource_id, checkout_operation_id, session_id,
    )):
        raise PaymentIntegrityError("checkout_metadata_missing")
    collection = _resource_collection(access, resource_type)
    resource = await collection.find_one({"id": resource_id})
    if not resource:
        raise PaymentIntegrityError("payment_resource_missing")
    if resource.get("stripeSessionId") != session_id:
        raise PaymentIntegrityError("provider_session_mismatch")
    if resource.get("stripeCheckoutOperationId") != checkout_operation_id:
        raise PaymentIntegrityError("checkout_operation_mismatch")
    if provider_object.get("clientReferenceId") != resource_id:
        raise PaymentIntegrityError("client_reference_mismatch")

    expected_amount = resource.get("stripeExpectedAmountMinor")
    expected_currency = resource.get("stripeExpectedCurrency")
    event_amount = provider_object.get("amountTotal")
    event_currency = provider_object.get("currency")
    if not isinstance(expected_amount, int) or isinstance(expected_amount, bool) or expected_amount <= 0:
        raise PaymentIntegrityError("expected_amount_missing")
    if not isinstance(event_amount, int) or isinstance(event_amount, bool) or event_amount != expected_amount:
        raise PaymentIntegrityError("provider_amount_mismatch")
    if not isinstance(expected_currency, str) or not isinstance(event_currency, str):
        raise PaymentIntegrityError("provider_currency_missing")
    if event_currency.upper() != expected_currency.upper():
        raise PaymentIntegrityError("provider_currency_mismatch")

    event_id = normalized["eventId"]
    event_created = normalized["providerCreated"]
    if event_type in {"checkout.session.async_payment_failed", "checkout.session.expired"}:
        state = "failed" if event_type.endswith("failed") else "expired"
        changed = await _set_non_paid_state(
            collection, resource, state=state, event_id=event_id, event_created=event_created,
        )
        return {"outcome": state, "resourceType": resource_type, "resourceId": resource_id, "changed": changed}

    if provider_object.get("paymentStatus") != "paid":
        if event_type == "checkout.session.async_payment_succeeded":
            raise PaymentIntegrityError("provider_payment_not_paid")
        changed = await _set_non_paid_state(
            collection, resource, state="pending", event_id=event_id, event_created=event_created,
        )
        return {"outcome": "pending", "resourceType": resource_type, "resourceId": resource_id, "changed": changed}

    payment_intent_id = provider_object.get("paymentIntentId")
    if resource_type == "invoice":
        changed = await _settle_invoice(
            access, resource, amount=event_amount, currency=expected_currency,
            session_id=session_id, payment_intent_id=payment_intent_id,
            event_id=event_id, event_created=event_created,
        )
    else:
        changed = await _settle_simple_resource(
            collection, resource, resource_type=resource_type,
            amount=event_amount, currency=expected_currency,
            session_id=session_id, payment_intent_id=payment_intent_id,
            event_id=event_id, event_created=event_created,
        )
        if resource_type == "shop_order":
            settled_resource = await collection.find_one({"id": resource_id})
            records = (settled_resource or {}).get("paymentRecords") or []
            settled_by_session = any(
                record.get("providerSessionId") == session_id
                for record in records
                if isinstance(record, Mapping)
            )
            if settled_resource and settled_by_session:
                await _send_shop_payment_confirmation_once(access, settled_resource, event_id)
    if changed:
        try:
            await tenant_audit(
                access, SYSTEM_ACTOR, "payment.confirmed", resource_id,
                {"provider": PROVIDER, "resourceType": resource_type, "providerEventId": event_id},
            )
        except Exception:
            await report_operational_failure(
                access, logger,
                operation="payment.confirmation_audit",
                category="audit_persistence",
            )
    return {"outcome": "paid", "resourceType": resource_type, "resourceId": resource_id, "changed": changed}


async def handle_stripe_event(access: TenantBusinessAccess, event: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_stripe_event(event)
    ledger = StripeEventLedger(access)
    try:
        claim = await ledger.claim(normalized)
    except PaymentEventBusy as exc:
        raise HTTPException(
            status_code=503,
            detail="Zahlungsereignis wird bereits verarbeitet",
            headers={"Retry-After": str(EVENT_LEASE_SECONDS)},
        ) from exc
    except PaymentIntegrityError as exc:
        logger.warning(
            "stripe_event_claim_rejected tenant=%s event_id=%s code=%s",
            access.context.tenant_id,
            normalized["eventId"],
            exc.code,
        )
        return {"status": "rejected", "eventId": normalized["eventId"]}
    if claim.is_duplicate:
        return {"status": "duplicate", "eventId": claim.event_id}
    try:
        result = await process_normalized_stripe_event(access, normalized)
        await ledger.complete(claim, result)
        logger.info(
            "stripe_event_processed tenant=%s event_id=%s event_type=%s outcome=%s",
            access.context.tenant_id,
            claim.event_id,
            normalized["eventType"],
            result.get("outcome"),
        )
        return {"status": "processed", "eventId": claim.event_id}
    except PaymentIntegrityError as exc:
        await _persist_event_failure(ledger, claim, exc)
        try:
            await tenant_audit(
                access,
                SYSTEM_ACTOR,
                "payment.webhook_failed",
                claim.event_id,
                {
                    "provider": PROVIDER,
                    "eventType": normalized["eventType"],
                    "errorCode": exc.code,
                    "retryable": exc.retryable,
                },
            )
        except Exception:
            await report_operational_failure(
                access, logger,
                operation="payment.failure_audit",
                category="audit_persistence",
            )
        logger.warning(
            "stripe_event_failed tenant=%s event_id=%s event_type=%s code=%s retryable=%s",
            access.context.tenant_id,
            claim.event_id,
            normalized["eventType"],
            exc.code,
            exc.retryable,
        )
        if exc.retryable:
            raise HTTPException(status_code=503, detail="Zahlungsereignis konnte nicht verarbeitet werden") from exc
        return {"status": "rejected", "eventId": claim.event_id}
    except Exception as exc:
        error = PaymentIntegrityError("internal_payment_processing_error", retryable=True)
        await _persist_event_failure(ledger, claim, error)
        logger.error(
            "stripe_event_internal_error tenant=%s event_id=%s event_type=%s",
            access.context.tenant_id,
            claim.event_id,
            normalized["eventType"],
        )
        raise HTTPException(status_code=503, detail="Zahlungsereignis konnte nicht verarbeitet werden") from exc


async def retry_stripe_event(
    access: TenantBusinessAccess,
    ledger_id: str,
    user: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = StripeEventLedger(access)
    claim, normalized = await ledger.claim_retry(ledger_id)
    try:
        await tenant_audit(access, user, "payment.event_retry", claim.event_id, {"ledgerId": ledger_id})
    except Exception:
        await report_operational_failure(
            access, logger,
            operation="payment.retry_audit",
            category="audit_persistence",
        )
    try:
        result = await process_normalized_stripe_event(access, normalized)
        await ledger.complete(claim, result)
        return {"status": "processed", "eventId": claim.event_id, "result": result}
    except PaymentIntegrityError as exc:
        await _persist_event_failure(ledger, claim, exc)
        if exc.retryable:
            raise HTTPException(status_code=503, detail="Zahlungsereignis konnte nicht verarbeitet werden") from exc
        raise HTTPException(status_code=409, detail="Zahlungsereignis wurde sicher abgewiesen") from exc
    except Exception as exc:
        error = PaymentIntegrityError("internal_payment_retry_error", retryable=True)
        await _persist_event_failure(ledger, claim, error)
        logger.error(
            "payment_retry_internal_error tenant=%s event_id=%s",
            access.context.tenant_id,
            claim.event_id,
        )
        raise HTTPException(status_code=503, detail="Zahlungsereignis konnte nicht verarbeitet werden") from exc
