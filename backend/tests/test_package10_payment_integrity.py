from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_package10")
os.environ.setdefault("JWT_SECRET", "test-only-package10-secret-with-32-bytes")
os.environ.setdefault("APP_ENV", "test")

from app.migrations.versions import v0010_payment_integrity as migration10
from app.payment_integrity import (
    PaymentIntegrityError,
    StripeEventLedger,
    construct_stripe_event,
    create_stripe_checkout,
    handle_stripe_event,
    normalize_stripe_event,
    process_normalized_stripe_event,
    retry_stripe_event,
)
from app.routers import machines, payments, shop, stripe_webhooks
from test_customer_commerce_platform import AsyncDatabase, no_audit, principal, run, scoped


def prepare_ledger(database: AsyncDatabase) -> None:
    database.raw.payment_provider_events.create_index(
        [("provider", 1), ("providerAccount", 1), ("eventId", 1)], unique=True,
    )


def checkout_event(
    event_id: str = "evt_package10",
    *,
    event_type: str = "checkout.session.completed",
    tenant_id: str = "tenant-a",
    resource_type: str = "shop_order",
    resource_id: str = "resource-1",
    operation_id: str = "operation-1",
    session_id: str = "cs_package10",
    amount: int = 1234,
    currency: str = "eur",
    payment_status: str = "paid",
    created: int = 100,
    object_type: str = "checkout.session",
    live_mode: bool = False,
    client_reference_id: str | None = None,
) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created": created,
        "livemode": live_mode,
        "data": {"object": {
            "id": session_id,
            "object": object_type,
            "amount_total": amount,
            "currency": currency,
            "payment_status": payment_status,
            "status": "complete",
            "payment_intent": "pi_package10",
            "client_reference_id": client_reference_id or resource_id,
            "metadata": {
                "tenantId": tenant_id,
                "resourceType": resource_type,
                "resourceId": resource_id,
                "checkoutOperationId": operation_id,
            },
        }},
    }


def seed_payable(access, resource_type: str = "shop_order", **overrides):
    document = {
        "id": "resource-1",
        "paymentStatus": "Offen",
        "status": "Neu",
        "currency": "EUR",
        "stripeSessionId": "cs_package10",
        "stripeCheckoutOperationId": "operation-1",
        "stripeExpectedAmountMinor": 1234,
        "stripeExpectedCurrency": "EUR",
    }
    document.update(overrides)
    collection = {
        "invoice": access.invoices,
        "shop_order": access.shop_orders,
        "machine_request": access.machine_requests,
    }[resource_type]
    run(collection.insert_one(document))
    return collection


@pytest.fixture(autouse=True)
def isolate_environment_and_audit(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_package10")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_package10")
    monkeypatch.setenv("APP_URL", "http://testserver")
    monkeypatch.setattr("app.payment_integrity.tenant_audit", no_audit)
    monkeypatch.setattr("app.payment_integrity.send_email", no_audit)


def test_webhook_requires_valid_official_signature(monkeypatch):
    event = checkout_event()
    payload = json.dumps(event, separators=(",", ":")).encode()
    timestamp = int(time.time())
    digest = hmac.new(
        b"whsec_package10", f"{timestamp}.".encode() + payload, hashlib.sha256,
    ).hexdigest()
    signature = f"t={timestamp},v1={digest}"

    assert construct_stripe_event(payload, signature)["id"] == event["id"]
    with pytest.raises(HTTPException) as missing:
        construct_stripe_event(payload, None)
    assert missing.value.status_code == 400
    with pytest.raises(HTTPException) as modified:
        construct_stripe_event(payload + b" ", signature)
    assert modified.value.status_code == 400
    with pytest.raises(HTTPException) as malformed:
        construct_stripe_event(payload, "invalid")
    assert malformed.value.status_code == 400


def test_webhook_reads_exact_raw_body_and_rejects_oversized_stream():
    payload = b'{"raw":"bytes must stay exact"}'

    def request_for(chunks, content_length=None):
        remaining = list(chunks)

        async def receive():
            body = remaining.pop(0) if remaining else b""
            return {"type": "http.request", "body": body, "more_body": bool(remaining)}

        headers = [] if content_length is None else [(b"content-length", str(content_length).encode())]
        return Request({"type": "http", "method": "POST", "path": "/", "headers": headers}, receive)

    exact = run(stripe_webhooks._raw_webhook_body(request_for([payload[:7], payload[7:]])))
    assert exact == payload
    with pytest.raises(HTTPException) as declared:
        run(stripe_webhooks._raw_webhook_body(request_for([], stripe_webhooks.MAX_STRIPE_WEBHOOK_BYTES + 1)))
    assert declared.value.status_code == 413
    with pytest.raises(HTTPException) as streamed:
        run(stripe_webhooks._raw_webhook_body(request_for([b"x" * (stripe_webhooks.MAX_STRIPE_WEBHOOK_BYTES + 1)])))
    assert streamed.value.status_code == 413


def test_duplicate_event_is_processed_once_even_when_delivered_ten_times():
    database = AsyncDatabase("payment_duplicate_10x")
    prepare_ledger(database)
    access = scoped(database)
    collection = seed_payable(access)
    event = checkout_event()

    results = [run(handle_stripe_event(access, event)) for _ in range(10)]

    assert results[0]["status"] == "processed"
    assert [row["status"] for row in results[1:]] == ["duplicate"] * 9
    resource = run(collection.find_one({"id": "resource-1"}))
    assert resource["paymentStatus"] == "Bezahlt"
    assert len(resource["paymentRecords"]) == 1
    assert database.raw.payment_provider_events.count_documents({}) == 1


def test_duplicate_payment_event_sends_shop_confirmation_at_most_once(monkeypatch):
    database = AsyncDatabase("payment_email_once")
    access = scoped(database)
    collection = seed_payable(
        access,
        customer={"email": "buyer@example.test"},
        totalMinor=1234,
        total=12.34,
    )
    messages = []

    async def capture_email(**message):
        messages.append(message)
        return "test-message"

    monkeypatch.setattr("app.payment_integrity.send_email", capture_email)
    normalized = normalize_stripe_event(checkout_event())
    run(process_normalized_stripe_event(access, normalized))
    run(process_normalized_stripe_event(access, normalized))

    resource = run(collection.find_one({"id": "resource-1"}))
    assert len(messages) == 1
    assert resource["paymentConfirmationEmailState"] == "queued"
    assert len(resource["paymentRecords"]) == 1


def test_concurrent_duplicate_event_has_one_economic_effect():
    database = AsyncDatabase("payment_duplicate_concurrent")
    prepare_ledger(database)
    access = scoped(database)
    collection = seed_payable(access)
    event = checkout_event()

    async def deliver():
        try:
            return await handle_stripe_event(access, event)
        except HTTPException as exc:
            return exc

    async def deliver_both():
        return await asyncio.gather(deliver(), deliver())

    results = run(deliver_both())
    assert sum(isinstance(item, dict) and item["status"] == "processed" for item in results) == 1
    assert sum(
        (isinstance(item, dict) and item["status"] == "duplicate")
        or (isinstance(item, HTTPException) and item.status_code == 503)
        for item in results
    ) == 1
    resource = run(collection.find_one({"id": "resource-1"}))
    assert len(resource["paymentRecords"]) == 1


@pytest.mark.parametrize("resource_type", ["shop_order", "machine_request"])
def test_paid_webhook_settles_simple_resources_once(resource_type):
    database = AsyncDatabase(f"payment_{resource_type}")
    access = scoped(database)
    collection = seed_payable(access, resource_type)
    normalized = normalize_stripe_event(checkout_event(resource_type=resource_type))

    first = run(process_normalized_stripe_event(access, normalized))
    second = run(process_normalized_stripe_event(access, normalized))

    resource = run(collection.find_one({"id": "resource-1"}))
    assert first["changed"] is True and second["changed"] is False
    assert resource["paymentStatus"] == "Bezahlt"
    assert resource["status"] == ("Bezahlt" if resource_type == "shop_order" else "Gekauft")
    assert len(resource["paymentRecords"]) == 1


def test_paid_webhook_settles_remaining_invoice_amount_once():
    database = AsyncDatabase("payment_invoice")
    access = scoped(database)
    collection = seed_payable(
        access,
        "invoice",
        amount=20.0,
        amountMinor=2000,
        paidAmountMinor=766,
        status="Offen",
    )
    normalized = normalize_stripe_event(checkout_event(resource_type="invoice"))

    result = run(process_normalized_stripe_event(access, normalized))

    invoice = run(collection.find_one({"id": "resource-1"}))
    assert result["changed"] is True
    assert invoice["paidAmountMinor"] == 2000
    assert invoice["status"] == "Bezahlt"
    assert invoice["paymentRecords"][0]["amountMinor"] == 1234


def test_invoice_checkout_charges_only_server_calculated_remaining_amount(monkeypatch):
    database = AsyncDatabase("payment_invoice_checkout_remaining")
    access = scoped(database)
    run(access.companies.insert_one({"id": "company-1", "name": "Company", "active": True}))
    run(access.invoices.insert_one({
        "id": "invoice-1", "companyId": "company-1", "status": "Teilweise bezahlt",
        "amount": 20.0, "amountMinor": 2000, "paidAmountMinor": 766, "currency": "EUR",
    }))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"id": "cs_invoice_remaining", "url": "https://checkout.test/invoice"}

    async def visible(*_args, **_kwargs):
        return ["company-1"]

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.create", create)
    monkeypatch.setattr(payments, "visible_company_ids", visible)
    result = run(payments.create_checkout(
        "invoice-1", principal(access), access, operation_id="invoice-checkout-op",
    ))
    assert result["sessionId"] == "cs_invoice_remaining"
    assert calls[0]["line_items"][0]["price_data"]["unit_amount"] == 1234
    assert calls[0]["currency"] == "eur"


@pytest.mark.parametrize(
    ("event_changes", "error_code"),
    [
        ({"amount": 1235}, "provider_amount_mismatch"),
        ({"currency": "usd"}, "provider_currency_mismatch"),
        ({"tenant_id": "tenant-b"}, "tenant_mismatch"),
        ({"session_id": "cs_foreign"}, "provider_session_mismatch"),
        ({"operation_id": "operation-foreign"}, "checkout_operation_mismatch"),
        ({"client_reference_id": "resource-foreign"}, "client_reference_mismatch"),
        ({"object_type": "payment_intent"}, "provider_object_mismatch"),
        ({"live_mode": True}, "provider_mode_mismatch"),
    ],
)
def test_mismatched_provider_claims_never_mark_resource_paid(event_changes, error_code):
    database = AsyncDatabase(f"payment_mismatch_{error_code}")
    access = scoped(database)
    collection = seed_payable(access)
    normalized = normalize_stripe_event(checkout_event(**event_changes))

    with pytest.raises(PaymentIntegrityError) as rejected:
        run(process_normalized_stripe_event(access, normalized))

    assert rejected.value.code == error_code
    resource = run(collection.find_one({"id": "resource-1"}))
    assert resource["paymentStatus"] == "Offen"
    assert "paymentRecords" not in resource


def test_terminal_validation_failure_is_ledgered_without_repeated_processing():
    database = AsyncDatabase("payment_terminal")
    prepare_ledger(database)
    access = scoped(database)
    seed_payable(access)
    event = checkout_event(amount=9999)

    first = run(handle_stripe_event(access, event))
    second = run(handle_stripe_event(access, event))

    assert first["status"] == "rejected" and second["status"] == "duplicate"
    ledger = database.raw.payment_provider_events.find_one({})
    assert ledger["status"] == "failed_terminal"
    assert ledger["lastErrorCode"] == "provider_amount_mismatch"


@pytest.mark.parametrize(
    ("resource_overrides", "event_changes", "error_code"),
    [
        ({"id": "different"}, {}, "payment_resource_missing"),
        ({"paymentStatus": "Bezahlt"}, {}, "resource_already_paid_by_other_payment"),
        ({"stripeSessionId": None}, {}, "provider_session_mismatch"),
    ],
)
def test_unknown_already_paid_and_unlinked_resources_fail_closed(
    resource_overrides, event_changes, error_code,
):
    database = AsyncDatabase(f"payment_resource_{error_code}")
    access = scoped(database)
    seed_payable(access, **resource_overrides)
    with pytest.raises(PaymentIntegrityError) as rejected:
        run(process_normalized_stripe_event(access, normalize_stripe_event(checkout_event(**event_changes))))
    assert rejected.value.code == error_code


def test_async_payment_success_settles_and_async_failure_does_not():
    success_database = AsyncDatabase("payment_async_success")
    success_access = scoped(success_database)
    success_collection = seed_payable(success_access)
    success = checkout_event(
        event_id="evt_async_success",
        event_type="checkout.session.async_payment_succeeded",
    )
    assert run(process_normalized_stripe_event(success_access, normalize_stripe_event(success)))["outcome"] == "paid"
    assert run(success_collection.find_one({"id": "resource-1"}))["paymentStatus"] == "Bezahlt"

    failure_database = AsyncDatabase("payment_async_failure")
    failure_access = scoped(failure_database)
    failure_collection = seed_payable(failure_access)
    failure = checkout_event(
        event_id="evt_async_failure",
        event_type="checkout.session.async_payment_failed",
        payment_status="unpaid",
    )
    assert run(process_normalized_stripe_event(failure_access, normalize_stripe_event(failure)))["outcome"] == "failed"
    failed_resource = run(failure_collection.find_one({"id": "resource-1"}))
    assert failed_resource["paymentStatus"] == "Offen"


def test_out_of_order_expired_or_pending_event_cannot_revert_paid_resource():
    database = AsyncDatabase("payment_out_of_order")
    access = scoped(database)
    collection = seed_payable(access)
    run(process_normalized_stripe_event(access, normalize_stripe_event(checkout_event(created=200))))

    expired = checkout_event(
        event_id="evt_expired", event_type="checkout.session.expired",
        payment_status="unpaid", created=300,
    )
    pending = checkout_event(
        event_id="evt_pending", event_type="checkout.session.completed",
        payment_status="unpaid", created=400,
    )
    assert run(process_normalized_stripe_event(access, normalize_stripe_event(expired)))["changed"] is False
    assert run(process_normalized_stripe_event(access, normalize_stripe_event(pending)))["changed"] is False
    resource = run(collection.find_one({"id": "resource-1"}))
    assert resource["paymentStatus"] == "Bezahlt" and resource["stripeCheckoutState"] == "paid"


def test_stale_non_paid_event_cannot_overwrite_newer_provider_state():
    database = AsyncDatabase("payment_stale")
    access = scoped(database)
    collection = seed_payable(access)
    newer = checkout_event(
        event_id="evt_newer", event_type="checkout.session.async_payment_failed",
        payment_status="unpaid", created=200,
    )
    older = checkout_event(
        event_id="evt_older", event_type="checkout.session.completed",
        payment_status="unpaid", created=100,
    )
    run(process_normalized_stripe_event(access, normalize_stripe_event(newer)))
    result = run(process_normalized_stripe_event(access, normalize_stripe_event(older)))
    resource = run(collection.find_one({"id": "resource-1"}))
    assert result["changed"] is False and resource["stripeCheckoutState"] == "failed"


def test_unknown_and_dispute_events_create_no_financial_effect():
    database = AsyncDatabase("payment_review")
    access = scoped(database)
    collection = seed_payable(access)
    unknown = normalize_stripe_event(checkout_event(event_id="evt_unknown", event_type="customer.updated"))
    dispute = normalize_stripe_event(checkout_event(event_id="evt_dispute", event_type="charge.dispute.created"))

    assert run(process_normalized_stripe_event(access, unknown))["outcome"] == "ignored"
    assert run(process_normalized_stripe_event(access, dispute))["outcome"] == "review_required"
    assert run(collection.find_one({"id": "resource-1"}))["paymentStatus"] == "Offen"


def test_ledger_is_tenant_scoped_while_provider_event_identity_is_global():
    database = AsyncDatabase("payment_tenant_scope")
    prepare_ledger(database)
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    event = normalize_stripe_event(checkout_event())

    claim = run(StripeEventLedger(tenant_a).claim(event))
    assert claim.lease_token
    with pytest.raises(PaymentIntegrityError, match="provider_event_owned_by_other_tenant"):
        run(StripeEventLedger(tenant_b).claim(event))


def test_checkout_reuses_provider_idempotency_after_local_persistence_failure(monkeypatch):
    database = AsyncDatabase("payment_checkout_recovery")
    access = scoped(database)
    run(access.shop_orders.insert_one({"id": "order-1", "paymentStatus": "Offen"}))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"id": "cs_recovered", "url": "https://checkout.test/recovered"}

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.create", create)
    original_update = access.shop_orders.update_one
    failures = {"remaining": 1}

    async def fail_persistence_once(query, update, *args, **kwargs):
        if update.get("$set", {}).get("stripeSessionId") and failures["remaining"]:
            failures["remaining"] -= 1
            class Result:
                matched_count = 0
            return Result()
        return await original_update(query, update, *args, **kwargs)

    monkeypatch.setattr(access.shop_orders, "update_one", fail_persistence_once)
    kwargs = dict(
        resource_type="shop_order", resource_id="order-1", operation_id="operation-recovery",
        expected_amount_minor=1234, currency="EUR", product_name="Order",
        success_url="https://app.test/success", cancel_url="https://app.test/cancel",
    )
    with pytest.raises(HTTPException) as first:
        run(create_stripe_checkout(access, **kwargs))
    assert first.value.status_code == 409
    result = run(create_stripe_checkout(access, **kwargs))
    assert result["sessionId"] == "cs_recovered"
    assert len(calls) == 2
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]


def test_parallel_checkout_calls_converge_on_one_provider_session(monkeypatch):
    database = AsyncDatabase("payment_checkout_parallel")
    access = scoped(database)
    run(access.shop_orders.insert_one({"id": "order-1", "paymentStatus": "Offen"}))
    calls = []
    sessions = {}

    def create(**kwargs):
        calls.append(kwargs)
        return sessions.setdefault(
            kwargs["idempotency_key"],
            {"id": "cs_parallel", "url": "https://checkout.test/parallel"},
        )

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.create", create)

    async def checkout(operation_id):
        return await create_stripe_checkout(
            access,
            resource_type="shop_order",
            resource_id="order-1",
            operation_id=operation_id,
            expected_amount_minor=1234,
            currency="EUR",
            product_name="Order",
            success_url="https://app.test/success",
            cancel_url="https://app.test/cancel",
        )

    async def both():
        return await asyncio.gather(checkout("client-one"), checkout("client-two"))

    first, second = run(both())
    assert first == second
    assert {call["idempotency_key"] for call in calls} == {"tenant-a:client-one"}
    resource = run(access.shop_orders.find_one({"id": "order-1"}))
    assert resource["stripeCheckoutOperationId"] == "client-one"
    assert resource["stripeSessionId"] == "cs_parallel"


def test_resource_stable_checkout_operation_reuses_session_for_new_client_key(monkeypatch):
    database = AsyncDatabase("payment_checkout_stable")
    access = scoped(database)
    run(access.shop_orders.insert_one({"id": "order-1", "paymentStatus": "Offen"}))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"id": "cs_stable", "url": "https://checkout.test/stable"}

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.create", create)
    common = dict(
        resource_type="shop_order", resource_id="order-1", expected_amount_minor=1234,
        currency="EUR", product_name="Order", success_url="https://app.test/success",
        cancel_url="https://app.test/cancel",
    )
    first = run(create_stripe_checkout(access, operation_id="client-key-1", **common))
    second = run(create_stripe_checkout(access, operation_id="client-key-2", **common))
    assert first == second
    assert len(calls) == 1


def test_expired_checkout_starts_one_new_provider_operation(monkeypatch):
    database = AsyncDatabase("payment_checkout_expired")
    access = scoped(database)
    run(access.shop_orders.insert_one({
        "id": "order-1", "paymentStatus": "Offen",
        "stripeSessionId": "cs_expired", "stripeCheckoutUrl": "https://checkout.test/expired",
        "stripeCheckoutOperationId": "operation-expired", "stripeCheckoutState": "expired",
        "stripeExpectedAmountMinor": 1234, "stripeExpectedCurrency": "EUR",
    }))
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return {"id": "cs_new", "url": "https://checkout.test/new"}

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.create", create)
    result = run(create_stripe_checkout(
        access,
        resource_type="shop_order", resource_id="order-1", operation_id="operation-new",
        expected_amount_minor=1234, currency="EUR", product_name="Order",
        success_url="https://app.test/success", cancel_url="https://app.test/cancel",
    ))
    resource = run(access.shop_orders.find_one({"id": "order-1"}))
    assert result["sessionId"] == "cs_new"
    assert resource["stripeCheckoutOperationId"] == "operation-new"
    assert resource["stripeSessionId"] == "cs_new"
    assert calls[0]["idempotency_key"] == "tenant-a:operation-new"


def test_retry_after_payment_write_before_ledger_completion_has_one_payment(monkeypatch):
    database = AsyncDatabase("payment_ledger_recovery")
    prepare_ledger(database)
    access = scoped(database)
    collection = seed_payable(access)
    event = checkout_event()
    original_complete = StripeEventLedger.complete
    attempts = {"count": 0}

    async def fail_first_complete(self, claim, result):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise PaymentIntegrityError("simulated_ledger_write_failure", retryable=True)
        return await original_complete(self, claim, result)

    monkeypatch.setattr(StripeEventLedger, "complete", fail_first_complete)
    with pytest.raises(HTTPException) as first:
        run(handle_stripe_event(access, event))
    assert first.value.status_code == 503
    assert database.raw.payment_provider_events.find_one({})["status"] == "failed_retryable"

    second = run(handle_stripe_event(access, event))
    resource = run(collection.find_one({"id": "resource-1"}))
    assert second["status"] == "processed"
    assert len(resource["paymentRecords"]) == 1


def test_admin_event_visibility_is_tenant_safe_and_retry_is_audited(monkeypatch):
    database = AsyncDatabase("payment_admin_recovery")
    prepare_ledger(database)
    own = scoped(database)
    foreign = scoped(database, tenant="tenant-b")
    seed_payable(own)
    own_event = normalize_stripe_event(checkout_event())
    foreign_event = normalize_stripe_event(checkout_event(event_id="evt_foreign", tenant_id="tenant-b"))
    run(own.payment_provider_events.insert_one({
        "id": "ledger-own", "provider": "stripe", "providerAccount": "platform",
        "eventId": own_event["eventId"], "eventType": own_event["eventType"],
        "status": "failed_retryable", "attempts": 1, "eventSnapshot": own_event,
        "updatedAt": "2026-01-01T00:00:00+00:00", "lastErrorCode": "temporary",
    }))
    run(foreign.payment_provider_events.insert_one({
        "id": "ledger-foreign", "provider": "stripe", "providerAccount": "platform",
        "eventId": foreign_event["eventId"], "eventType": foreign_event["eventType"],
        "status": "failed_retryable", "attempts": 1, "eventSnapshot": foreign_event,
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }))
    audit_calls = []

    async def capture_audit(*args):
        audit_calls.append(args)

    monkeypatch.setattr("app.payment_integrity.tenant_audit", capture_audit)
    rows = run(stripe_webhooks.list_payment_events(principal(own), own, None))
    assert [row["id"] for row in rows] == ["ledger-own"]
    assert "eventSnapshot" not in rows[0] and "tenantId" not in rows[0]

    result = run(retry_stripe_event(own, "ledger-own", principal(own)))
    assert result["status"] == "processed"
    assert audit_calls[0][2] == "payment.event_retry"
    assert database.raw.payment_provider_events.find_one({"id": "ledger-foreign"})["status"] == "failed_retryable"


def test_admin_retry_recovers_expired_processing_lease_and_internal_failure(monkeypatch):
    database = AsyncDatabase("payment_admin_expired_lease")
    prepare_ledger(database)
    access = scoped(database)
    normalized = normalize_stripe_event(checkout_event())
    run(access.payment_provider_events.insert_one({
        "id": "ledger-expired", "provider": "stripe", "providerAccount": "platform",
        "eventId": normalized["eventId"], "eventType": normalized["eventType"],
        "status": "processing", "attempts": 1, "eventSnapshot": normalized,
        "leaseToken": "abandoned", "leaseUntil": datetime.now(timezone.utc) - timedelta(seconds=1),
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }))

    async def unexpected(*_args, **_kwargs):
        raise RuntimeError("simulated processor crash")

    monkeypatch.setattr("app.payment_integrity.process_normalized_stripe_event", unexpected)
    with pytest.raises(HTTPException) as failed:
        run(retry_stripe_event(access, "ledger-expired", principal(access)))
    assert failed.value.status_code == 503
    ledger = database.raw.payment_provider_events.find_one({"id": "ledger-expired"})
    assert ledger["status"] == "failed_retryable"
    assert ledger["attempts"] == 2
    assert ledger["lastErrorCode"] == "internal_payment_retry_error"


def test_status_endpoints_never_poll_stripe_or_mark_paid(monkeypatch):
    database = AsyncDatabase("payment_status_read_only")
    access = scoped(database)
    run(access.invoices.insert_one({
        "id": "invoice-1", "companyId": "company-1", "status": "Offen",
        "paymentStatus": "Offen", "stripeSessionId": "cs_status",
    }))
    run(access.companies.insert_one({"id": "company-1", "name": "Company", "active": True}))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Stripe must not be called from a browser status request")

    monkeypatch.setattr("app.payment_integrity.stripe.checkout.Session.retrieve", forbidden)
    user = principal(access, company_id=None)
    monkeypatch.setattr(payments, "visible_company_ids", lambda *_args: asyncio.sleep(0, result=["company-1"]))
    response = run(payments.payment_status("invoice-1", user, access))
    assert response == {"status": "Offen"}
    assert run(access.invoices.find_one({"id": "invoice-1"}))["status"] == "Offen"


def test_checkout_environment_guards_reject_wrong_stripe_key_and_insecure_app_url(monkeypatch):
    from app.payment_integrity import checkout_return_urls, stripe_api_key

    monkeypatch.setenv("APP_ENV", " StAgInG ")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_wrong_for_staging")
    with pytest.raises(PaymentIntegrityError, match="stripe_non_production_requires_test_key"):
        stripe_api_key()
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_correct")
    monkeypatch.setenv("APP_URL", "http://staging.example")
    with pytest.raises(PaymentIntegrityError, match="app_url_must_use_https"):
        checkout_return_urls()
    monkeypatch.setenv("APP_URL", "https://")
    with pytest.raises(PaymentIntegrityError, match="app_url_invalid"):
        checkout_return_urls()

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_URL", "https://app.example.test")
    success, cancel = checkout_return_urls("shop_order", "shop/id")
    assert success == "https://app.example.test/zahlung?status=success&type=shop_order&id=shop%2Fid"
    assert cancel == "https://app.example.test/zahlung?status=cancel&type=shop_order&id=shop%2Fid"


@pytest.mark.parametrize("environment", [None, "", "stagin", "qa", "local"])
def test_payment_environment_is_explicit_and_fail_closed(monkeypatch, environment):
    from app.payment_integrity import stripe_api_key

    if environment is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_safe")
    with pytest.raises(PaymentIntegrityError, match="payment_environment_invalid") as error:
        stripe_api_key()
    assert error.value.retryable is True


def test_production_and_development_stripe_modes_cannot_be_crossed(monkeypatch):
    from app.payment_integrity import stripe_api_key

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_wrong_for_production")
    with pytest.raises(PaymentIntegrityError, match="stripe_production_requires_live_key"):
        stripe_api_key()

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("STRIPE_API_KEY", "sk_live_wrong_for_development")
    with pytest.raises(PaymentIntegrityError, match="stripe_non_production_requires_test_key"):
        stripe_api_key()


def test_event_ledger_never_persists_raw_payload_or_signature():
    database = AsyncDatabase("payment_safe_snapshot")
    prepare_ledger(database)
    access = scoped(database)
    seed_payable(access)
    event = checkout_event()
    event["data"]["object"]["customer_details"] = {"email": "private@example.test"}
    event["data"]["object"]["payment_method_details"] = {"card": {"last4": "4242"}}

    run(handle_stripe_event(access, event))

    ledger = database.raw.payment_provider_events.find_one({})
    serialized = json.dumps(ledger, default=str)
    assert "private@example.test" not in serialized
    assert "4242" not in serialized
    assert "rawPayload" not in ledger
    assert "stripeSignature" not in ledger


def test_migration_10_is_additive_repeat_safe_and_changes_no_documents():
    database = AsyncDatabase("migration_10")
    plan = migration10.inspect(database.raw)
    assert plan.expected_changes["documentsChanged"] == 0

    class Context:
        def checkpoint(self):
            return None

    first = migration10.apply(database.raw, Context())
    second = migration10.apply(database.raw, Context())
    assert first == second == {"indexesEnsured": len(migration10.INDEXES), "documentsChanged": 0}
    assert sum(database.raw[name].count_documents({}) for name in database.raw.list_collection_names()) == 0
