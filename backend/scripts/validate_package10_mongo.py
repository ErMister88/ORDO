#!/usr/bin/env python3
"""Validate Package 10 concurrency semantics against disposable real MongoDB.

The runner is deliberately unavailable for staging and production databases.
It expects migrations to have been applied to a fresh ``ordo_test_*`` target.
It never drops the target automatically so a failed safety check cannot destroy
an existing database and the validation evidence remains inspectable.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import sys
from typing import Any

from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


EXPECTED_MIGRATION_SETTINGS = {
    "tenantId": "tnt_ss_0001",
    "key": "shop",
    "currency": "EUR",
    "freeShippingThreshold": 59.0,
    "freeShippingThresholdMinor": 5900,
    "shippingFee": 0.0,
    "shippingFeeMinor": 0,
    "newsletterDiscountPercent": 10,
    "newsletterDiscountEnabled": True,
}


@dataclass(frozen=True)
class ValidationTarget:
    database_name: str
    confirmation: str


def validated_target(environment: dict[str, str] | None = None) -> ValidationTarget:
    values = environment if environment is not None else os.environ
    app_env = values.get("APP_ENV", "").strip().lower()
    database_name = values.get("DB_NAME", "").strip()
    confirmation = values.get("ORDO_TEST_TARGET_CONFIRMATION", "").strip()
    if app_env not in {"test", "testing"}:
        raise RuntimeError("Package 10 Mongo validation requires APP_ENV=test")
    if not database_name.startswith("ordo_test_") or database_name == "ordo_staging":
        raise RuntimeError("Package 10 Mongo validation requires a disposable ordo_test_* database")
    expected = f"{app_env}:{database_name}"
    if confirmation != expected:
        raise RuntimeError("ORDO_TEST_TARGET_CONFIRMATION does not match the disposable target")
    if not values.get("MONGO_URL", "").strip():
        raise RuntimeError("MONGO_URL is required")
    return ValidationTarget(database_name=database_name, confirmation=confirmation)


def checkout_event(
    event_id: str,
    resource_id: str,
    *,
    session_id: str,
    amount: int = 1234,
    currency: str = "eur",
) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "created": 100,
        "livemode": False,
        "data": {"object": {
            "id": session_id,
            "object": "checkout.session",
            "amount_total": amount,
            "currency": currency,
            "payment_status": "paid",
            "status": "complete",
            "payment_intent": f"pi_{event_id}",
            "client_reference_id": resource_id,
            "metadata": {
                "tenantId": "tnt_ss_0001",
                "resourceType": "shop_order",
                "resourceId": resource_id,
                "checkoutOperationId": f"op_{resource_id}",
            },
        }},
    }


async def ensure_no_business_documents(database) -> None:
    from app.tenant_access import TENANT_SCOPED_BUSINESS_COLLECTIONS

    collection_names = set(await database.list_collection_names())
    business_collections = TENANT_SCOPED_BUSINESS_COLLECTIONS - {"settings"}
    populated = {
        name: await database[name].count_documents({})
        for name in business_collections
        if name in collection_names
    }
    if any(populated.values()):
        raise RuntimeError("Disposable validation database already contains business documents")

    if "settings" not in collection_names:
        return
    settings = await database["settings"].find({}, {"_id": 0}).to_list(length=2)
    if settings and settings != [EXPECTED_MIGRATION_SETTINGS]:
        raise RuntimeError("Disposable validation database contains unexpected settings")


async def validate(database) -> dict[str, Any]:
    from app.idempotency import IdempotencyService
    from app.payment_integrity import (
        PaymentEventBusy,
        PaymentIntegrityError,
        StripeEventLedger,
        handle_stripe_event,
        normalize_stripe_event,
    )
    from app.routers import billing
    from app.tenant_access import TenantBusinessAccess
    from app.tenancy import TenantContext, TenantResolutionSource

    await ensure_no_business_documents(database)

    required_indexes = {
        "idempotency_records": "uniq_tenant_idempotency_scope",
        "invoices": "uniq_invoice_order",
        "payment_provider_events": "uniq_provider_account_event",
        "shop_orders": "uniq_shop_orders_stripe_session",
    }
    for collection, index_name in required_indexes.items():
        if index_name not in await database[collection].index_information():
            raise RuntimeError(f"Required migration index missing: {collection}.{index_name}")

    context = TenantContext(
        tenant_id="tnt_ss_0001",
        resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
    )
    access = TenantBusinessAccess(database, context)

    # Concurrent duplicate operation claims must have exactly one lease owner.
    def operation_service() -> IdempotencyService:
        return IdempotencyService(
            access,
            actor_id="validation-admin",
            operation="order.create",
            key="package10-real-mongo-order",
            payload={"companyId": "validation-company", "items": [{"productId": "p1", "qty": 1}]},
        )

    async def claim_operation():
        try:
            return await operation_service().claim()
        except HTTPException as exc:
            return exc

    operation_claims = await asyncio.gather(claim_operation(), claim_operation())
    owners = [value for value in operation_claims if not isinstance(value, HTTPException)]
    conflicts = [value for value in operation_claims if isinstance(value, HTTPException)]
    if len(owners) != 1 or len(conflicts) != 1 or conflicts[0].status_code != 409:
        raise RuntimeError("Concurrent idempotency claim did not produce one owner")
    await operation_service().complete(owners[0], {"id": "validation-order"})
    replay = await operation_service().claim()
    if not replay.is_replay or replay.replay_response != {"id": "validation-order"}:
        raise RuntimeError("Completed operation was not replayed")

    # Concurrent invoice recovery must produce one immutable invoice for one order.
    await access.companies.insert_one({"id": "validation-company", "name": "Validation", "active": True})
    order = {
        "id": "validation-order",
        "companyId": "validation-company",
        "items": [{
            "snapshotVersion": 1,
            "productId": "validation-product",
            "sku": "VALIDATION-1",
            "productName": "Validation product",
            "unit": "piece",
            "qty": 1,
            "price": 10.0,
            "unitPriceMinor": 1000,
            "lineTotalMinor": 1000,
            "taxRate": 19,
            "currency": "EUR",
            "priceSource": "validation",
            "costMinor": 500,
        }],
        "currency": "EUR",
        "paymentMethod": "bank_transfer",
        "companySnapshot": {"id": "validation-company", "name": "Validation"},
        "createdAt": "2026-01-01T00:00:00+00:00",
    }
    await access.orders.insert_one(order)
    sequence = iter(range(1, 20))

    async def next_sequence(_name: str) -> int:
        return next(sequence)

    async def no_activity(*_args, **_kwargs) -> None:
        return None

    billing.next_seq = next_sequence
    billing.record_customer_activity = no_activity
    actor = {"id": "validation-admin", "role": "admin", "name": "Validation"}
    invoices = await asyncio.gather(
        billing.create_invoice_record(access, order, actor),
        billing.create_invoice_record(access, order, actor),
    )
    if invoices[0]["id"] != invoices[1]["id"]:
        raise RuntimeError("Concurrent invoice recovery returned different invoices")
    if await access.invoices.count_documents({"orderId": order["id"]}) != 1:
        raise RuntimeError("Concurrent invoice recovery created more than one invoice")

    # A duplicate Stripe delivery must produce one provider record and one payment effect.
    resource_id = "validation-shop-order"
    session_id = "cs_validation_package10"
    await access.shop_orders.insert_one({
        "id": resource_id,
        "status": "Neu",
        "paymentStatus": "Offen",
        "currency": "EUR",
        "stripeSessionId": session_id,
        "stripeCheckoutOperationId": f"op_{resource_id}",
        "stripeExpectedAmountMinor": 1234,
        "stripeExpectedCurrency": "EUR",
    })
    event = checkout_event("evt_validation_package10", resource_id, session_id=session_id)

    async def deliver():
        try:
            return await handle_stripe_event(access, event)
        except HTTPException as exc:
            return exc

    deliveries = await asyncio.gather(deliver(), deliver())
    processed = sum(isinstance(value, dict) and value.get("status") == "processed" for value in deliveries)
    duplicate_or_busy = sum(
        isinstance(value, dict) and value.get("status") == "duplicate"
        or isinstance(value, HTTPException) and value.status_code == 503
        for value in deliveries
    )
    if processed != 1 or duplicate_or_busy != 1:
        raise RuntimeError("Concurrent Stripe delivery did not produce one processor")
    duplicate = await handle_stripe_event(access, event)
    if duplicate.get("status") != "duplicate":
        raise RuntimeError("Repeated Stripe delivery was not recognized as duplicate")
    paid = await access.shop_orders.find_one({"id": resource_id})
    if paid.get("paymentStatus") != "Bezahlt" or len(paid.get("paymentRecords") or []) != 1:
        raise RuntimeError("Duplicate Stripe delivery changed the economic effect count")

    second_event = checkout_event("evt_validation_package10_second", resource_id, session_id=session_id)
    second_result = await handle_stripe_event(access, second_event)
    paid_again = await access.shop_orders.find_one({"id": resource_id})
    if second_result.get("status") != "processed" or len(paid_again.get("paymentRecords") or []) != 1:
        raise RuntimeError("Different provider event duplicated an existing payment")

    # Event lease acquisition itself must also have one owner under a real race.
    lease_event = normalize_stripe_event(checkout_event(
        "evt_validation_lease", resource_id, session_id="cs_validation_lease",
    ))
    ledgers = [StripeEventLedger(access), StripeEventLedger(access)]

    async def claim_event(ledger: StripeEventLedger):
        try:
            return await ledger.claim(lease_event)
        except PaymentEventBusy as exc:
            return exc

    lease_claims = await asyncio.gather(*(claim_event(ledger) for ledger in ledgers))
    lease_owners = [value for value in lease_claims if not isinstance(value, PaymentEventBusy)]
    lease_busy = [value for value in lease_claims if isinstance(value, PaymentEventBusy)]
    if len(lease_owners) != 1 or len(lease_busy) != 1:
        raise RuntimeError("Concurrent provider-event lease did not produce one owner")
    await ledgers[0 if lease_claims[0] is lease_owners[0] else 1].fail(
        lease_owners[0], PaymentIntegrityError("validation_cleanup", retryable=False),
    )

    # Mismatched amount and currency remain terminal and cannot mark resources paid.
    for suffix, amount, currency in (("amount", 999, "eur"), ("currency", 1234, "usd")):
        mismatch_id = f"validation-mismatch-{suffix}"
        mismatch_session = f"cs_validation_{suffix}"
        await access.shop_orders.insert_one({
            "id": mismatch_id,
            "status": "Neu",
            "paymentStatus": "Offen",
            "currency": "EUR",
            "stripeSessionId": mismatch_session,
            "stripeCheckoutOperationId": f"op_{mismatch_id}",
            "stripeExpectedAmountMinor": 1234,
            "stripeExpectedCurrency": "EUR",
        })
        result = await handle_stripe_event(
            access,
            checkout_event(f"evt_validation_{suffix}", mismatch_id, session_id=mismatch_session, amount=amount, currency=currency),
        )
        unchanged = await access.shop_orders.find_one({"id": mismatch_id})
        if result.get("status") != "rejected" or unchanged.get("paymentStatus") != "Offen":
            raise RuntimeError(f"{suffix} mismatch changed payment state")

    return {
        "duplicateOrderRequest": "pass",
        "orderInvoiceRecovery": "pass",
        "duplicatePayment": "pass",
        "duplicateStripeEvent": "pass",
        "concurrentStripeEvent": "pass",
        "idempotencyLeaseRace": "pass",
        "amountCurrencyMismatch": "pass",
    }


async def main() -> int:
    target = validated_target()
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(
        os.environ["MONGO_URL"],
        tz_aware=True,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
    )
    try:
        await client.admin.command("ping")
        database = client[target.database_name]
        await ensure_no_business_documents(database)
        result = await validate(database)
        print("PACKAGE10_REAL_MONGO_VALIDATION=PASS")
        for name, status in sorted(result.items()):
            print(f"{name}={status}")
        return 0
    finally:
        client.close()
        print("DISPOSABLE_DATABASE_DROPPED=NO")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
