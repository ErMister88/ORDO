from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os

import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_package9")
os.environ.setdefault("JWT_SECRET", "test-only-package9-secret-with-32-bytes")
os.environ.setdefault("APP_ENV", "test")

from app.idempotency import IdempotencyService, request_hash
from app.migrations.versions import v0009_transaction_safety_config as migration9
from app.models import BusinessClassificationIn, CompanyCreateIn, CompanyUpdateIn, CustomerPriceIn, EquipmentFinancingRequestIn, OfferCreate, OrderCreate, PaymentRecordIn, PriceApprovalDecisionIn, ProductIn, SubscriptionIn
from app.routers import billing, business_config, companies, invoices, offers, operations, orders, pricing, products, subscriptions
from test_customer_commerce_platform import AsyncDatabase, no_audit, principal, run, scoped, seed_product


def prepare_idempotency(database: AsyncDatabase) -> None:
    database.raw.idempotency_records.create_index(
        [("tenantId", 1), ("operation", 1), ("actorId", 1), ("key", 1)],
        unique=True,
    )


def service(access, *, key="request-key-0001", actor="admin", payload=None):
    return IdempotencyService(
        access,
        actor_id=actor,
        operation="test.operation",
        key=key,
        payload=payload or {"value": 1},
    )


def test_request_hash_is_canonical_and_payload_changes_are_detected():
    assert request_hash({"b": 2, "a": 1}) == request_hash({"a": 1, "b": 2})
    assert request_hash({"a": 1}) != request_hash({"a": 2})


def test_idempotency_completed_retry_replays_and_payload_substitution_fails():
    database = AsyncDatabase("idempotency_replay")
    prepare_idempotency(database)
    access = scoped(database)
    first_service = service(access)
    first = run(first_service.claim())
    run(first_service.complete(first, {"id": "result-1"}, {"orderId": "result-1"}))

    replay = run(service(access).claim())
    assert replay.is_replay and replay.replay_response == {"id": "result-1"}
    with pytest.raises(HTTPException) as conflict:
        run(service(access, payload={"value": 2}).claim())
    assert conflict.value.status_code == 409


def test_idempotency_scope_is_independent_per_tenant_and_actor():
    database = AsyncDatabase("idempotency_scope")
    prepare_idempotency(database)
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    assert run(service(tenant_a, actor="actor-a").claim()).lease_token
    assert run(service(tenant_a, actor="actor-b").claim()).lease_token
    assert run(service(tenant_b, actor="actor-a").claim()).lease_token
    assert database.raw.idempotency_records.count_documents({}) == 3


def test_concurrent_duplicate_claim_has_exactly_one_owner():
    database = AsyncDatabase("idempotency_concurrent")
    prepare_idempotency(database)
    access = scoped(database)

    async def claim_once():
        try:
            return await service(access).claim()
        except HTTPException as exc:
            return exc

    async def concurrent_claims():
        return await asyncio.gather(claim_once(), claim_once())

    results = run(concurrent_claims())
    assert sum(not isinstance(result, HTTPException) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1


def test_failed_and_expired_leases_can_be_recovered_but_active_lease_cannot():
    database = AsyncDatabase("idempotency_recovery")
    prepare_idempotency(database)
    access = scoped(database)
    first_service = service(access)
    first = run(first_service.claim())
    with pytest.raises(HTTPException):
        run(service(access).claim())
    run(first_service.fail(first, error_code="expected_test_failure"))
    recovered = run(service(access).claim())
    assert recovered.recovered and recovered.record_id == first.record_id

    database.raw.idempotency_records.update_one(
        {"id": first.record_id},
        {"$set": {"status": "processing", "leaseUntil": datetime.now(timezone.utc) - timedelta(seconds=1)}},
    )
    reclaimed = run(service(access).claim())
    assert reclaimed.recovered and reclaimed.record_id == first.record_id


def test_business_validation_failure_is_terminal_for_the_same_key():
    database = AsyncDatabase("idempotency_terminal")
    prepare_idempotency(database)
    access = scoped(database)
    idem = service(access)
    claim = run(idem.claim())
    run(idem.fail(
        claim, error_code="invalid_request",
        exception=HTTPException(status_code=400, detail="invalid"),
    ))
    with pytest.raises(HTTPException, match="endgültig") as conflict:
        run(service(access).claim())
    assert conflict.value.status_code == 409


def test_naive_bson_lease_timestamp_is_compared_as_utc():
    database = AsyncDatabase("idempotency_naive_lease")
    prepare_idempotency(database)
    access = scoped(database)
    claim = run(service(access).claim())
    database.raw.idempotency_records.update_one(
        {"id": claim.record_id},
        {"$set": {"leaseUntil": datetime.utcnow() + timedelta(seconds=30)}},
    )
    with pytest.raises(HTTPException) as conflict:
        run(service(access).claim())
    assert conflict.value.status_code == 409


def test_cleaned_record_starts_a_new_bounded_idempotency_window():
    database = AsyncDatabase("idempotency_cleanup")
    prepare_idempotency(database)
    access = scoped(database)
    first = run(service(access).claim())
    database.raw.idempotency_records.delete_one({"id": first.record_id})
    second = run(service(access).claim())
    assert second.record_id != first.record_id


def test_order_invoice_partial_failure_recovers_one_order_and_one_invoice(monkeypatch):
    database = AsyncDatabase("order_invoice_recovery")
    prepare_idempotency(database)
    database.raw.orders.create_index([("tenantId", 1), ("operationId", 1)], unique=True, sparse=True)
    database.raw.invoices.create_index([("tenantId", 1), ("orderId", 1)], unique=True)
    access = scoped(database)
    user = principal(access)
    run(access.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    seed_product(access)
    counters = iter(range(1, 20))

    async def sequence(_name):
        return next(counters)

    async def quiet(*_args, **_kwargs):
        return None

    original_invoice = billing.create_invoice_record
    calls = {"count": 0}

    async def fail_invoice_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated dependent write failure")
        return await original_invoice(*args, **kwargs)

    monkeypatch.setattr(orders, "next_seq", sequence)
    monkeypatch.setattr(billing, "next_seq", sequence)
    monkeypatch.setattr(orders, "record_customer_activity", quiet)
    monkeypatch.setattr(billing, "record_customer_activity", quiet)
    monkeypatch.setattr(billing, "create_invoice_record", fail_invoice_once)
    body = OrderCreate(
        companyId="c1", items=[{"productId": "p1", "qty": 2}],
        paymentMethod="cash", createInvoice=True,
    )

    with pytest.raises(RuntimeError, match="dependent write"):
        run(orders.create_order_endpoint(body, user, access, "order-request-0001"))
    assert database.raw.orders.count_documents({"tenantId": "tenant-a"}) == 1
    assert database.raw.invoices.count_documents({"tenantId": "tenant-a"}) == 0

    result = run(orders.create_order_endpoint(body, user, access, "order-request-0001"))
    assert result["invoiceId"]
    assert database.raw.orders.count_documents({"tenantId": "tenant-a"}) == 1
    assert database.raw.invoices.count_documents({"tenantId": "tenant-a"}) == 1
    replay = run(orders.create_order_endpoint(body, user, access, "order-request-0001"))
    assert replay["id"] == result["id"] and replay["invoiceId"] == result["invoiceId"]


def test_business_config_crud_is_tenant_scoped_and_archived_history_is_visible(monkeypatch):
    database = AsyncDatabase("business_config")
    database.raw.customer_types.create_index(
        [("tenantId", 1), ("normalizedName", 1)], unique=True
    )
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    monkeypatch.setattr(business_config, "tenant_audit", no_audit)
    admin = principal(tenant_a)

    created = run(business_config.create_business_config(
        "customer-types", BusinessClassificationIn(name="Hotel", sortOrder=20), admin, tenant_a,
    ))
    updated = run(business_config.update_business_config(
        "customer-types", created["id"],
        BusinessClassificationIn(name="Hotellerie", description="Historischer Typ", sortOrder=10),
        admin, tenant_a,
    ))
    assert updated["name"] == "Hotellerie"
    run(business_config.archive_business_config("customer-types", created["id"], admin, tenant_a))
    rows = run(business_config.list_business_config("customer-types", admin, tenant_a))
    assert len(rows) == 1
    assert rows[0]["active"] is False
    assert run(business_config.list_business_config("customer-types", principal(tenant_b), tenant_b)) == []


def test_customer_classifications_allow_multiple_tags_reject_cross_tenant_and_preserve_archived(monkeypatch):
    database = AsyncDatabase("customer_classifications")
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    monkeypatch.setattr(companies, "tenant_audit", no_audit)
    monkeypatch.setattr(companies, "record_customer_activity", no_audit)
    run(tenant_a.customer_types.insert_one({"id": "type-a", "name": "A", "active": True}))
    run(tenant_a.customer_tags.insert_one({"id": "tag-a", "name": "A", "active": True}))
    run(tenant_a.customer_tags.insert_one({"id": "tag-b", "name": "B", "active": True}))
    run(tenant_b.customer_types.insert_one({"id": "type-foreign", "name": "Foreign", "active": True}))

    company = run(companies.create_company(
        CompanyCreateIn(name="Customer", customerTypeId="type-a", customerTagIds=["tag-a", "tag-b"]),
        principal(tenant_a), tenant_a,
    ))
    assert company["customerTagIds"] == ["tag-a", "tag-b"]
    with pytest.raises(HTTPException):
        run(companies.create_company(
            CompanyCreateIn(name="Foreign", customerTypeId="type-foreign"),
            principal(tenant_a), tenant_a,
        ))

    run(tenant_a.customer_types.update_one({"id": "type-a"}, {"$set": {"active": False}}))
    preserved = run(companies.update_company(
        company["id"],
        CompanyUpdateIn(name="Renamed", customerTypeId="type-a", customerTagIds=["tag-a", "tag-b"]),
        principal(tenant_a), tenant_a,
    ))
    assert preserved["customerTypeId"] == "type-a"


def test_product_brand_assignment_requires_active_same_tenant_reference():
    database = AsyncDatabase("product_brand")
    tenant_a = scoped(database)
    tenant_b = scoped(database, tenant="tenant-b")
    run(tenant_a.business_brands.insert_one({"id": "brand-a", "name": "A", "active": True}))
    run(tenant_b.business_brands.insert_one({"id": "brand-b", "name": "B", "active": True}))
    valid = ProductIn(
        name="Product", brandId="brand-a", standardPrice=10, salesFloor=9,
        absoluteFloor=8, cost=5, taxRate=19,
    )
    run(products._validate_product_references(tenant_a, valid))
    invalid = valid.model_copy(update={"brandId": "brand-b"})
    with pytest.raises(HTTPException):
        run(products._validate_product_references(tenant_a, invalid))


def test_customer_price_retry_after_price_write_recovers_missing_history(monkeypatch):
    database = AsyncDatabase("customer_price_recovery")
    prepare_idempotency(database)
    database.raw.price_history.create_index([("operationId", 1)], unique=True, sparse=True)
    access = scoped(database)
    user = principal(access)
    run(access.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    seed_product(access)
    monkeypatch.setattr(pricing, "tenant_audit", no_audit)
    monkeypatch.setattr(pricing, "record_customer_activity", no_audit)
    original_insert = access.price_history.insert_one
    calls = {"count": 0}

    async def fail_first_history_write(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("simulated history write failure")
        return await original_insert(*args, **kwargs)

    monkeypatch.setattr(access.price_history, "insert_one", fail_first_history_write)
    body = CustomerPriceIn(companyId="c1", productId="p1", price=9.5)
    with pytest.raises(RuntimeError, match="history write"):
        run(pricing.upsert_customer_price_endpoint(body, user, access, "price-request-0001"))
    assert database.raw.price_history.count_documents({"tenantId": "tenant-a"}) == 0
    assert database.raw.customer_prices.count_documents({"tenantId": "tenant-a"}) == 1

    result = run(pricing.upsert_customer_price_endpoint(body, user, access, "price-request-0001"))
    assert result["priceMinor"] == 950
    assert database.raw.price_history.count_documents({"tenantId": "tenant-a"}) == 1
    assert database.raw.customer_prices.count_documents({"tenantId": "tenant-a"}) == 1


def test_public_financing_request_retry_does_not_duplicate():
    database = AsyncDatabase("equipment_request_idempotency")
    prepare_idempotency(database)
    database.raw.equipment_requests.create_index([("operationId", 1)], unique=True, sparse=True)
    access = scoped(database)
    seed_product(access)
    body = EquipmentFinancingRequestIn(
        productId="p1", name="Buyer", email="buyer@example.test", message="Please contact me",
    )
    first = run(products.create_equipment_financing_request_endpoint(
        body, access, "equipment-request-0001"
    ))
    replay = run(products.create_equipment_financing_request_endpoint(
        body, access, "equipment-request-0001"
    ))
    assert replay == first
    assert database.raw.equipment_requests.count_documents({"tenantId": "tenant-a"}) == 1


def test_persistent_price_approval_can_only_be_applied_once_concurrently(monkeypatch):
    database = AsyncDatabase("persistent_approval_concurrency")
    database.raw.price_history.create_index([("operationId", 1)], unique=True, sparse=True)
    access = scoped(database)
    user = principal(access)
    run(access.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    seed_product(access)
    run(access.price_approvals.insert_one({
        "id": "approval-1", "companyId": "c1", "productId": "p1",
        "requestedPriceMinor": 900, "currency": "EUR", "conditions": {},
        "status": "pending", "requestedBy": "sales", "createdAt": "2026-01-01",
    }))
    monkeypatch.setattr(pricing, "tenant_audit", no_audit)
    monkeypatch.setattr(pricing, "record_customer_activity", no_audit)

    async def decide_once():
        try:
            return await pricing.decide_price_approval(
                "approval-1", PriceApprovalDecisionIn(persistence="customer_price"),
                user, access,
            )
        except HTTPException as exc:
            return exc

    async def decide_both():
        return await asyncio.gather(decide_once(), decide_once())

    results = run(decide_both())
    assert sum(not isinstance(result, HTTPException) for result in results) == 1
    assert database.raw.customer_prices.count_documents({"tenantId": "tenant-a"}) == 1
    assert database.raw.price_history.count_documents({"tenantId": "tenant-a"}) == 1


def test_one_time_price_approval_cannot_create_two_offers(monkeypatch):
    database = AsyncDatabase("one_time_approval_concurrency")
    prepare_idempotency(database)
    database.raw.offers.create_index([("operationId", 1)], unique=True, sparse=True)
    access = scoped(database, role="sales", actor="sales")
    user = principal(access)
    run(access.companies.insert_one({
        "id": "c1", "name": "Customer", "assignedSalesRepId": "sales", "active": True,
    }))
    seed_product(access)
    run(access.price_approvals.insert_one({
        "id": "approval-once", "companyId": "c1", "productId": "p1",
        "requestedPriceMinor": 850, "currency": "EUR", "quantity": 1,
        "status": "approved", "persistence": "one_time", "requestedBy": "sales",
    }))
    sequence = iter(range(1, 20))

    async def next_number(_name):
        return next(sequence)

    monkeypatch.setattr(offers, "next_seq", next_number)
    body = OfferCreate(
        companyId="c1", items=[{"productId": "p1", "qty": 1, "price": 8.5, "approvalId": "approval-once"}],
    )

    async def create_once(key):
        try:
            return await offers.create_offer_endpoint(body, user, access, key)
        except HTTPException as exc:
            return exc

    async def create_both():
        return await asyncio.gather(
            create_once("offer-request-0001"), create_once("offer-request-0002")
        )

    results = run(create_both())
    assert sum(isinstance(result, dict) for result in results) == 1
    assert database.raw.offers.count_documents({"tenantId": "tenant-a"}) == 1
    approval = database.raw.price_approvals.find_one({"tenantId": "tenant-a", "id": "approval-once"})
    assert approval.get("consumedByOfferId")


def test_same_payment_retry_has_one_economic_effect(monkeypatch):
    database = AsyncDatabase("payment_retry")
    prepare_idempotency(database)
    access = scoped(database)
    user = principal(access)
    run(access.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(access.orders.insert_one({"id": "B-1", "companyId": "c1", "currency": "EUR", "items": []}))
    run(access.invoices.insert_one({
        "id": "RE-1", "companyId": "c1", "orderId": "B-1", "currency": "EUR",
        "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen",
    }))
    monkeypatch.setattr(invoices, "tenant_audit", no_audit)
    body = PaymentRecordIn(amount=4, method="bank_transfer", reference="transfer-1")
    first = run(invoices.record_invoice_payment_endpoint(
        "RE-1", body, user, access, "payment-request-0001"
    ))
    replay = run(invoices.record_invoice_payment_endpoint(
        "RE-1", body, user, access, "payment-request-0001"
    ))
    assert replay == first
    stored = database.raw.invoices.find_one({"tenantId": "tenant-a", "id": "RE-1"})
    assert stored["paidAmountMinor"] == 400
    assert len(stored["paymentRecords"]) == 1


def test_one_subscription_run_can_safely_create_multiple_orders_and_replay(monkeypatch):
    database = AsyncDatabase("subscription_batch")
    prepare_idempotency(database)
    database.raw.orders.create_index([("operationId", 1)], unique=True, sparse=True)
    database.raw.orders.create_index([("subscriptionRunKey", 1)], unique=True, sparse=True)
    access = scoped(database)
    user = principal(access)
    run(access.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    seed_product(access)
    first = run(subscriptions.create_subscription(
        SubscriptionIn(companyId="c1", items=[{"productId": "p1", "qty": 1}], intervalDays=28),
        user, access,
    ))
    second = run(subscriptions.create_subscription(
        SubscriptionIn(companyId="c1", items=[{"productId": "p1", "qty": 2}], intervalDays=28),
        user, access,
    ))
    run(access.subscriptions.update_one({"id": first["id"]}, {"$set": {"nextRun": "2020-01-01"}}))
    run(access.subscriptions.update_one({"id": second["id"]}, {"$set": {"nextRun": "2020-01-01"}}))
    sequence = iter(range(1, 20))

    async def next_number(_name):
        return next(sequence)

    monkeypatch.setattr(subscriptions, "next_seq", next_number)
    result = run(subscriptions.run_due_subscriptions_endpoint(
        user, access, "subscription-run-0001"
    ))
    replay = run(subscriptions.run_due_subscriptions_endpoint(
        user, access, "subscription-run-0001"
    ))
    assert result["count"] == 2 and replay == result
    assert database.raw.orders.count_documents({"tenantId": "tenant-a"}) == 2
    assert len({row["operationId"] for row in database.raw.orders.find({})}) == 2


def test_operations_visibility_is_tenant_scoped_and_does_not_expose_keys_or_hashes():
    database = AsyncDatabase("operation_visibility")
    access = scoped(database)
    foreign = scoped(database, tenant="tenant-b")
    run(access.idempotency_records.insert_one({
        "id": "op-own", "operation": "order.create", "actorId": "admin",
        "key": "secret-client-key", "requestHash": "sensitive-hash", "status": "failed_retryable",
        "workflowState": "failed", "attempts": 1, "events": [], "updatedAt": "2026-01-01",
    }))
    run(foreign.idempotency_records.insert_one({
        "id": "op-foreign", "operation": "order.create", "actorId": "other",
        "key": "foreign-key", "requestHash": "foreign-hash", "status": "failed_retryable",
        "workflowState": "failed", "attempts": 1, "events": [], "updatedAt": "2026-01-01",
    }))
    rows = run(operations.list_commercial_operations(principal(access), access))
    assert [row["id"] for row in rows] == ["op-own"]
    assert "key" not in rows[0] and "requestHash" not in rows[0]


def test_migration_9_is_additive_repeat_safe_and_changes_no_documents():
    database = AsyncDatabase("migration_9")
    plan = migration9.inspect(database.raw)
    assert plan.expected_changes["documentsChanged"] == 0

    class Context:
        def checkpoint(self):
            return None

    first = migration9.apply(database.raw, Context())
    second = migration9.apply(database.raw, Context())
    assert first == second == {"indexesEnsured": len(migration9.INDEXES), "documentsChanged": 0}
    assert sum(database.raw[name].count_documents({}) for name in database.raw.list_collection_names()) == 0
