from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os

import mongomock
import pytest
from fastapi import HTTPException

os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_money_import"
os.environ["JWT_SECRET"] = "test-only-money-secret-at-least-32-bytes"
os.environ["APP_ENV"] = "test"

from app.money import (  # noqa: E402
    MoneyError,
    amount_minor,
    currency_code,
    from_minor,
    included_tax_minor,
    line_total_minor,
    percentage_minor,
    to_minor,
)
from app.migrations.versions import v0004_money_expansion as money_migration  # noqa: E402
from app.models import (  # noqa: E402
    AcceptOfferIn,
    MachineRequestIn,
    OfferCreate,
    OfferItemIn,
    OrderCreate,
    OrderItemIn,
    ShopCustomerIn,
    ShopItemIn,
    ShopOrderIn,
    SubscriptionIn,
)
from app.routers import billing, machines, offers, orders, shop, subscriptions  # noqa: E402
from app.snapshots import clone_snapshot_items  # noqa: E402
from app.tenant_access import TenantBusinessAccess  # noqa: E402
from app.tenancy import TenantContext, TenantResolutionSource  # noqa: E402


class AsyncCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def sort(self, *args, **kwargs):
        self.cursor = self.cursor.sort(*args, **kwargs)
        return self

    async def to_list(self, length=None):
        rows = list(self.cursor)
        return rows if length is None else rows[:length]


class AsyncCollection:
    def __init__(self, collection):
        self.collection = collection

    def find(self, *args, **kwargs):
        return AsyncCursor(self.collection.find(*args, **kwargs))

    async def find_one(self, *args, **kwargs):
        return self.collection.find_one(*args, **kwargs)

    async def insert_one(self, document, *args, **kwargs):
        return self.collection.insert_one(deepcopy(document), *args, **kwargs)

    async def update_one(self, *args, **kwargs):
        return self.collection.update_one(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self.collection.delete_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self.collection.count_documents(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name):
        assert "staging" not in name and "production" not in name
        self.raw = mongomock.MongoClient(tz_aware=True)[name]

    def __getitem__(self, name):
        return AsyncCollection(self.raw[name])

    def __getattr__(self, name):
        return self[name]


def run(coroutine):
    return asyncio.run(coroutine)


def scoped(database: AsyncDatabase, tenant_id="tenant-a") -> TenantBusinessAccess:
    return TenantBusinessAccess(database, TenantContext(
        tenant_id=tenant_id,
        actor_user_id="admin",
        membership_id=f"mbr-{tenant_id}",
        role="admin",
        resolution_source=TenantResolutionSource.MEMBERSHIP,
        default_currency="EUR",
    ))


def actor(access: TenantBusinessAccess) -> dict:
    return {
        "id": "admin", "role": "admin", "companyId": None,
        "_tenant_context": access.context,
    }


def seed_catalog(access: TenantBusinessAccess, *, name="Original", price=10.01, cost=4.25):
    run(access.companies.insert_one({"id": "company", "name": "Customer", "active": True}))
    run(access.products.insert_one({
        "id": "product", "brand": "Brand", "name": name, "description": "Historic",
        "unit": "kg", "standardPrice": price, "salesFloor": 8.0,
        "absoluteFloor": 7.0, "cost": cost, "b2cPrice": 12.49,
        "taxRate": 7, "active": True, "discountTiers": [],
    }))


@pytest.mark.parametrize(
    ("value", "minor"),
    [("0.01", 1), ("1.005", 101), ("999999999999.99", 99999999999999)],
)
def test_money_conversion_is_decimal_and_half_up(value, minor):
    assert to_minor(value) == minor
    assert to_minor(from_minor(minor)) == minor


@pytest.mark.parametrize("value", [-0.01, float("nan"), float("inf"), True, None])
def test_invalid_money_is_rejected(value):
    with pytest.raises(MoneyError):
        to_minor(value)


def test_currency_and_exact_percentage_arithmetic():
    assert currency_code("EUR") == "EUR"
    for invalid in ("eur", " EUR", "EURO", ""):
        with pytest.raises(MoneyError):
            currency_code(invalid)
    assert line_total_minor(1, 3) == 3
    assert line_total_minor(1005, "1.5") == 1508
    assert percentage_minor(1005, 10) == 101
    assert included_tax_minor(107, 7) == 7
    with pytest.raises(MoneyError):
        amount_minor({"priceMinor": 100, "currency": "USD"}, "price", expected_currency="EUR")


def test_snapshot_creation_rejects_product_currency_mismatch():
    from app.snapshots import product_item_snapshot

    with pytest.raises(ValueError, match="currency mismatch"):
        product_item_snapshot(
            {"id": "p", "name": "Product", "currency": "USD", "costMinor": 10},
            quantity=1, unit_price_minor=100, currency="EUR", price_source="test",
        )


@pytest.mark.parametrize(
    "change",
    [
        {"productName": ""},
        {"unitPriceMinor": 999},
        {"lineTotalMinor": 999},
        {"taxRate": 101},
        {"qty": float("nan")},
    ],
)
def test_snapshot_clone_rejects_incomplete_or_inconsistent_source(change):
    item = {
        "snapshotVersion": 1,
        "productId": "p",
        "productName": "Product",
        "unit": "kg",
        "qty": 1,
        "price": 10.0,
        "unitPriceMinor": 1000,
        "lineTotalMinor": 1000,
        "currency": "EUR",
        "taxRate": 7,
        "priceSource": "offer_manual",
        "costMinor": 500,
    }
    item.update(change)
    with pytest.raises((MoneyError, ValueError)):
        clone_snapshot_items([item], currency="EUR")


def test_order_and_invoice_keep_product_price_cost_and_name_snapshot(monkeypatch):
    database = AsyncDatabase("money_order_invoice")
    access = scoped(database)
    seed_catalog(access)
    run(access.customer_prices.insert_one({"companyId": "company", "productId": "product", "price": 9.99}))

    async def sequence(_name):
        return 1

    monkeypatch.setattr(orders, "next_seq", sequence)
    created = run(orders.create_order(
        OrderCreate(companyId="company", items=[OrderItemIn(productId="product", qty=3)]),
        actor(access), access,
    ))
    assert created["items"][0]["unitPriceMinor"] == 999
    assert created["items"][0]["lineTotalMinor"] == 2997
    assert "costMinor" not in created["items"][0]
    assert database.raw.orders.find_one({"id": created["id"]})["items"][0]["costMinor"] == 425
    assert created["items"][0]["productName"] == "Brand Original"
    assert created["items"][0]["priceSource"] == "customer_price"

    database.raw.products.update_one(
        {"tenantId": "tenant-a", "id": "product"},
        {"$set": {"name": "Changed", "standardPrice": 99.0, "cost": 98.0, "taxRate": 19}},
    )
    database.raw.customer_prices.update_one(
        {"tenantId": "tenant-a"}, {"$set": {"price": 88.0}}
    )
    monkeypatch.setattr(billing, "next_seq", sequence)
    invoice = run(billing.create_invoice_for_order(created["id"], actor(access), access))
    assert invoice["lineItems"][0]["name"] == "Brand Original"
    assert invoice["lineItems"][0]["unitPriceMinor"] == 999
    assert invoice["lineItems"][0]["taxRate"] == 7
    assert "costMinor" not in invoice["lineItems"][0]
    assert invoice["netMinor"] == 2997
    assert invoice["amountMinor"] == 3207


def test_offer_acceptance_clones_immutable_snapshot(monkeypatch):
    database = AsyncDatabase("money_offer")
    access = scoped(database)
    seed_catalog(access)

    async def sequence(name):
        return 4 if name == "offer" else 5

    monkeypatch.setattr(offers, "next_seq", sequence)
    offer = run(offers.create_offer(
        OfferCreate(companyId="company", items=[OfferItemIn(productId="product", qty=2, price=9.5)]),
        actor(access), access,
    ))
    database.raw.products.update_one({"tenantId": "tenant-a"}, {"$set": {"name": "Changed", "cost": 99}})
    order = run(offers.accept_offer(offer["id"], AcceptOfferIn(), actor(access), access))
    stored_order = database.raw.orders.find_one({"id": order["id"]})
    stored_offer = database.raw.offers.find_one({"id": offer["id"]})
    assert stored_order["items"] == stored_offer["items"]
    assert stored_order["items"] is not stored_offer["items"]
    assert "costMinor" not in order["items"][0]
    assert order["items"][0]["productName"] == "Brand Original"
    assert order["items"][0]["unitPriceMinor"] == 950


def test_cost_snapshot_is_visible_only_in_admin_offer_response():
    document = {"id": "offer", "items": [{"productId": "p", "costMinor": 500}]}
    assert offers._offer_response(document, {"role": "admin"})["items"][0]["costMinor"] == 500
    assert "costMinor" not in offers._offer_response(document, {"role": "sales"})["items"][0]
    assert "costMinor" not in offers._offer_response(document, {"role": "customer"})["items"][0]


def test_subscription_order_gets_independent_snapshot(monkeypatch):
    database = AsyncDatabase("money_subscription")
    access = scoped(database)
    seed_catalog(access)
    subscription = run(subscriptions.create_subscription(
        SubscriptionIn(
            companyId="company",
            items=[OrderItemIn(productId="product", qty=2)],
            intervalDays=28,
        ),
        actor(access), access,
    ))
    database.raw.subscriptions.update_one({"id": subscription["id"]}, {"$set": {"nextRun": "2020-01-01"}})
    database.raw.products.update_one({"tenantId": "tenant-a"}, {"$set": {"name": "Changed", "standardPrice": 100}})

    async def sequence(_name):
        return 7

    monkeypatch.setattr(subscriptions, "next_seq", sequence)
    result = run(subscriptions.run_due_subscriptions(actor(access), access))
    order = database.raw.orders.find_one({"id": result["created"][0]})
    assert order["items"][0]["productName"] == "Brand Original"
    assert order["items"][0]["unitPriceMinor"] == 1001
    assert order["items"] is not database.raw.subscriptions.find_one({"id": subscription["id"]})["items"]


def test_shop_order_is_stable_and_guest_token_is_hashed_expiring_and_non_enumerable(monkeypatch):
    database = AsyncDatabase("money_shop")
    access = scoped(database)
    seed_catalog(access)
    run(access.settings.insert_one({"key": "shop", "currency": "EUR",
                                    "freeShippingThresholdMinor": 5900, "shippingFeeMinor": 490,
                                    "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True}))

    async def sequence(_name):
        return 9

    async def no_email(**_kwargs):
        return None

    monkeypatch.setattr(shop, "next_seq", sequence)
    monkeypatch.setattr(shop, "send_email", no_email)
    response = run(shop.create_shop_order(
        ShopOrderIn(
            items=[ShopItemIn(productId="product", qty=2)],
            customer=ShopCustomerIn(name="Guest", email="guest@example.test"),
        ),
        access,
        None,
    ))
    stored = database.raw.shop_orders.find_one({"id": response["id"]})
    assert response["token"] not in repr(stored)
    assert stored["items"][0]["productName"] == "Brand Original"
    assert stored["items"][0]["unitPriceMinor"] == 1249
    assert stored["currency"] == "EUR"
    shop._authorize_shop_order(stored, response["token"], None)
    assert run(shop.shop_payment_status(response["id"], access, response["token"], None)) == {"status": "Offen"}
    for token in (None, "wrong"):
        with pytest.raises(HTTPException) as exc:
            shop._authorize_shop_order(stored, token, None)
        assert exc.value.status_code == 404
    stored["accessTokenExpiresAt"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    with pytest.raises(HTTPException) as expired:
        shop._authorize_shop_order(stored, response["token"], None)
    assert expired.value.status_code == 404
    assert "accessTokenHash" not in shop._public_shop_order(stored)


def test_shop_checkout_uses_snapshot_total_and_never_calls_stripe_for_foreign_proof(monkeypatch):
    database = AsyncDatabase("money_shop_checkout")
    access = scoped(database)
    token = "guest-order-secret"
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    run(access.shop_orders.insert_one({
        "id": "shop-1", "paymentStatus": "Offen", "total": 12.34,
        "totalMinor": 1234, "currency": "EUR", "customer": {},
        "accessTokenHash": shop._order_token_hash(token), "accessTokenExpiresAt": expiry,
    }))
    calls = []

    class Session:
        id = "cs_test"
        url = "https://example.test/checkout"

    def create(**kwargs):
        calls.append(kwargs)
        return Session()

    monkeypatch.setattr(shop.stripe.checkout.Session, "create", create)
    with pytest.raises(HTTPException) as invalid:
        run(shop.shop_checkout("shop-1", access, "wrong", None))
    assert invalid.value.status_code == 404
    assert calls == []

    result = run(shop.shop_checkout("shop-1", access, token, None))
    assert result["url"] == Session.url
    assert calls[0]["line_items"][0]["price_data"]["unit_amount"] == 1234
    assert calls[0]["line_items"][0]["price_data"]["currency"] == "eur"


def test_shop_history_is_bounded_by_owner_and_tenant():
    database = AsyncDatabase("money_shop_history")
    tenant_a = scoped(database, "tenant-a")
    tenant_b = scoped(database, "tenant-b")
    run(tenant_a.shop_orders.insert_one({"id": "a-own", "userId": "shop-user", "accessTokenHash": "hidden"}))
    run(tenant_a.shop_orders.insert_one({"id": "a-foreign", "userId": "other"}))
    run(tenant_b.shop_orders.insert_one({"id": "b-own", "userId": "shop-user"}))

    rows = run(shop.shop_my_orders({"id": "shop-user"}, tenant_a))
    assert [row["id"] for row in rows] == ["a-own"]
    assert "accessTokenHash" not in rows[0]


def test_guest_order_token_cannot_cross_tenants_or_authorize_legacy_plaintext(monkeypatch):
    database = AsyncDatabase("money_shop_token_tenants")
    tenant_a = scoped(database, "tenant-a")
    tenant_b = scoped(database, "tenant-b")
    token_a = "tenant-a-secret"
    expiry = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    run(tenant_a.shop_orders.insert_one({
        "id": "same-order", "paymentStatus": "Offen", "totalMinor": 100,
        "currency": "EUR", "customer": {},
        "accessTokenHash": shop._order_token_hash(token_a),
        "accessTokenExpiresAt": expiry,
    }))
    run(tenant_b.shop_orders.insert_one({
        "id": "same-order", "paymentStatus": "Offen", "totalMinor": 200,
        "currency": "EUR", "customer": {},
        "accessTokenHash": shop._order_token_hash("tenant-b-secret"),
        "accessTokenExpiresAt": expiry,
    }))

    monkeypatch.setattr(
        shop.stripe.checkout.Session,
        "create",
        lambda **_kwargs: pytest.fail("Stripe must not be called for foreign proof"),
    )
    with pytest.raises(HTTPException) as foreign:
        run(shop.shop_checkout("same-order", tenant_b, token_a, None))
    assert foreign.value.status_code == 404

    with pytest.raises(HTTPException) as legacy:
        shop._authorize_shop_order({"id": "legacy", "token": "plaintext"}, "plaintext", None)
    assert legacy.value.status_code == 404


def test_same_product_id_in_two_tenants_never_cross_resolves_snapshot(monkeypatch):
    database = AsyncDatabase("money_cross_tenant")
    tenant_a = scoped(database, "tenant-a")
    tenant_b = scoped(database, "tenant-b")
    seed_catalog(tenant_a, name="Tenant A", price=10)
    seed_catalog(tenant_b, name="Tenant B", price=20)

    async def sequence(_name):
        return 1

    monkeypatch.setattr(orders, "next_seq", sequence)
    order_a = run(orders.create_order(
        OrderCreate(companyId="company", items=[OrderItemIn(productId="product", qty=1)]),
        actor(tenant_a), tenant_a,
    ))
    order_b = run(orders.create_order(
        OrderCreate(companyId="company", items=[OrderItemIn(productId="product", qty=1)]),
        actor(tenant_b), tenant_b,
    ))
    assert order_a["items"][0]["productName"] == "Brand Tenant A"
    assert order_b["items"][0]["productName"] == "Brand Tenant B"
    assert order_a["items"][0]["unitPriceMinor"] == 1000
    assert order_b["items"][0]["unitPriceMinor"] == 2000


def test_machine_purchase_keeps_catalog_snapshot_and_checkout_uses_minor_amount(monkeypatch):
    database = AsyncDatabase("money_machine")
    access = scoped(database)
    run(access.machines.insert_one({
        "id": "machine", "name": "Original Machine", "description": "Original",
        "price": 5900.005, "taxRate": 19, "active": True,
    }))

    async def sequence(_name):
        return 2

    monkeypatch.setattr(machines, "next_seq", sequence)
    request = run(machines.create_machine_request(
        MachineRequestIn(machineId="machine", type="kauf"), actor(access), access,
    ))
    assert request["machineName"] == "Original Machine"
    assert request["machinePriceMinor"] == 590001
    database.raw.machines.update_one(
        {"tenantId": "tenant-a", "id": "machine"},
        {"$set": {"name": "Changed", "price": 1.0}},
    )

    calls = []

    class Session:
        id = "cs_machine"
        url = "https://example.test/machine"

    def create(**kwargs):
        calls.append(kwargs)
        return Session()

    monkeypatch.setattr(machines.stripe.checkout.Session, "create", create)
    checkout = run(machines.machine_checkout(request["id"], actor(access), access))
    assert checkout["sessionId"] == Session.id
    assert calls[0]["line_items"][0]["price_data"]["unit_amount"] == 590001
    assert calls[0]["line_items"][0]["price_data"]["product_data"]["name"] == "Original Machine"


class MigrationContext:
    def checkpoint(self):
        return None


def test_money_migration_adds_exact_fields_but_does_not_invent_snapshot_facts():
    database = mongomock.MongoClient(tz_aware=True).ordo_test_money_migration
    database.tenants.insert_one({"id": "tenant-a", "defaultCurrency": "EUR"})
    database.orders.insert_one({
        "tenantId": "tenant-a", "id": "old-order",
        "items": [{"productId": "p1", "qty": 3, "price": 1.005}],
    })
    database.orders.insert_one({
        "id": "tenantless-order",
        "items": [{"productId": "p1", "qty": 1, "price": 9.99}],
    })
    plan = money_migration.inspect(database)
    assert plan.expected_changes["totalDocumentsToExpand"] == 1
    assert plan.expected_changes["documentsWithoutTenantId"]["orders"] == 1
    assert plan.expected_changes["totalDocumentsWithoutTenantId"] == 1
    money_migration.apply(database, MigrationContext())
    order = database.orders.find_one({"id": "old-order"})
    assert order["currency"] == "EUR"
    assert order["items"][0]["priceMinor"] == 101
    assert order["items"][0]["lineTotalMinor"] == 303
    assert order["netTotalMinor"] == 303
    assert "snapshotVersion" not in order
    assert "productName" not in order["items"][0]
    tenantless = database.orders.find_one({"id": "tenantless-order"})
    assert "currency" not in tenantless
    assert "priceMinor" not in tenantless["items"][0]


def test_money_migration_rejects_conflicting_existing_minor_value():
    database = mongomock.MongoClient().ordo_test_money_migration_conflict
    database.tenants.insert_one({"id": "tenant-a", "defaultCurrency": "EUR"})
    database.products.insert_one({
        "tenantId": "tenant-a", "id": "p1", "standardPrice": 10.0,
        "standardPriceMinor": 999,
    })
    with pytest.raises(Exception, match="conflicts"):
        money_migration.inspect(database)
