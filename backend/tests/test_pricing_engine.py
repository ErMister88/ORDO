from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os

import mongomock
import pytest

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_pricing_import")
os.environ.setdefault("JWT_SECRET", "test-only-pricing-secret-at-least-32-bytes")
os.environ.setdefault("APP_ENV", "test")

from app.pricing_engine import PricingEngine, PricingError
from app.models import OrderCreate, PricingQuoteIn, ShopOrderIn
from app.tenant_access import TenantBusinessAccess
from app.tenancy import TenantContext, TenantResolutionSource


class AsyncCursor:
    def __init__(self, cursor): self.cursor = cursor
    def sort(self, *args, **kwargs): self.cursor = self.cursor.sort(*args, **kwargs); return self
    async def to_list(self, length=None):
        rows = list(self.cursor)
        return rows if length is None else rows[:length]


class AsyncCollection:
    def __init__(self, collection): self.collection = collection
    def find(self, *args, **kwargs): return AsyncCursor(self.collection.find(*args, **kwargs))
    async def find_one(self, *args, **kwargs): return self.collection.find_one(*args, **kwargs)
    async def insert_one(self, document, *args, **kwargs): return self.collection.insert_one(deepcopy(document), *args, **kwargs)
    async def update_one(self, *args, **kwargs): return self.collection.update_one(*args, **kwargs)
    async def delete_one(self, *args, **kwargs): return self.collection.delete_one(*args, **kwargs)
    async def count_documents(self, *args, **kwargs): return self.collection.count_documents(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name): self.raw = mongomock.MongoClient(tz_aware=True)[name]
    def __getitem__(self, name): return AsyncCollection(self.raw[name])


def run(value): return asyncio.run(value)


def access(database, tenant="tenant-a"):
    return TenantBusinessAccess(database, TenantContext(
        tenant_id=tenant, actor_user_id="admin", membership_id="mbr", role="admin",
        resolution_source=TenantResolutionSource.MEMBERSHIP, default_currency="EUR",
    ))


def seed(target, *, product_id="p1", standard=1990, b2c=2999, tax=7, tiers=None):
    run(target.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(target.products.insert_one({
        "id": product_id, "brand": "Brand", "name": "Coffee", "unit": "kg", "active": True,
        "currency": "EUR", "standardPriceMinor": standard, "b2cPriceMinor": b2c,
        "taxRate": tax, "b2cTiers": tiers or [], "discountTiers": [{"minQty": 2, "priceMinor": 1}],
    }))


def test_b2b_customer_price_wins_and_legacy_tier_never_applies():
    db = AsyncDatabase("pricing_b2b_customer")
    a = access(db); seed(a)
    run(a.customer_prices.insert_one({"companyId": "c1", "productId": "p1", "priceMinor": 1890, "currency": "EUR"}))
    _product, quote = run(PricingEngine(a).quote_b2b("c1", "p1", 100))
    assert (quote.final_unit_price_minor, quote.price_source, quote.price_semantics) == (1890, "customer_price", "net")


def test_b2b_standard_fallback_and_never_b2c_fallback():
    db = AsyncDatabase("pricing_b2b_standard")
    a = access(db); seed(a)
    assert run(PricingEngine(a).quote_b2b("c1", "p1", 1))[1].final_unit_price_minor == 1990
    db.raw.products.update_one({"tenantId": "tenant-a", "id": "p1"}, {"$unset": {"standardPriceMinor": "", "standardPrice": ""}})
    with pytest.raises(PricingError, match="kein gültiger Preis"):
        run(PricingEngine(a).quote_b2b("c1", "p1", 1))


def test_duplicate_or_wrong_currency_customer_price_fails_closed():
    db = AsyncDatabase("pricing_customer_invalid")
    a = access(db); seed(a)
    run(a.customer_prices.insert_one({"companyId": "c1", "productId": "p1", "priceMinor": 1800, "currency": "USD"}))
    with pytest.raises(PricingError, match="Währung"):
        run(PricingEngine(a).quote_b2b("c1", "p1", 1))
    db.raw.customer_prices.delete_many({})
    for price in (1800, 1700):
        run(a.customer_prices.insert_one({"companyId": "c1", "productId": "p1", "priceMinor": price, "currency": "EUR"}))
    with pytest.raises(PricingError, match="Mehrere"):
        run(PricingEngine(a).quote_b2b("c1", "p1", 1))


def test_promotion_is_start_inclusive_end_exclusive_and_base_survives():
    db = AsyncDatabase("pricing_promotion_boundary")
    a = access(db); seed(a)
    run(a.customer_prices.insert_one({"companyId": "c1", "productId": "p1", "priceMinor": 1990, "currency": "EUR"}))
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 12, 1, tzinfo=timezone.utc)
    run(a.pricing_promotions.insert_one({"id": "promo", "productId": "p1", "companyId": "c1",
        "priceMinor": 1890, "currency": "EUR", "startsAt": start.isoformat(), "endsAt": end.isoformat()}))
    engine = PricingEngine(a)
    assert run(engine.quote_b2b("c1", "p1", 1, at=start))[1].final_unit_price_minor == 1890
    expired = run(engine.quote_b2b("c1", "p1", 1, at=end))[1]
    assert (expired.final_unit_price_minor, expired.price_source) == (1990, "customer_price")


def test_overlapping_general_and_customer_promotions_fail_closed():
    db = AsyncDatabase("pricing_promotion_overlap")
    a = access(db); seed(a)
    now = datetime.now(timezone.utc)
    for identifier, company in (("global", None), ("target", "c1")):
        run(a.pricing_promotions.insert_one({"id": identifier, "productId": "p1", "companyId": company,
            "priceMinor": 1800, "currency": "EUR", "startsAt": (now-timedelta(days=1)).isoformat(),
            "endsAt": (now+timedelta(days=1)).isoformat()}))
    with pytest.raises(PricingError, match="Überlappende"):
        run(PricingEngine(a).quote_b2b("c1", "p1", 1, at=now))


def test_cross_tenant_prices_and_promotions_are_invisible():
    db = AsyncDatabase("pricing_tenant")
    a, b = access(db, "tenant-a"), access(db, "tenant-b")
    seed(a); seed(b)
    run(b.customer_prices.insert_one({"companyId": "c1", "productId": "p1", "priceMinor": 1, "currency": "EUR"}))
    now = datetime.now(timezone.utc)
    run(b.pricing_promotions.insert_one({"id": "foreign", "productId": "p1", "companyId": None,
        "priceMinor": 1, "currency": "EUR", "startsAt": (now-timedelta(days=1)).isoformat(),
        "endsAt": (now+timedelta(days=1)).isoformat()}))
    assert run(PricingEngine(a).quote_b2b("c1", "p1", 1))[1].final_unit_price_minor == 1990


@pytest.mark.parametrize("qty,expected,tier", [(1, 2999, None), (2, 2899, 2), (5, 2899, 2), (6, 2599, 6), (24, 2199, 24)])
def test_arbitrary_b2c_tier_boundaries(qty, expected, tier):
    db = AsyncDatabase(f"pricing_tier_{qty}")
    a = access(db); seed(a, tiers=[
        {"minQty": 2, "priceMinor": 2899, "currency": "EUR"},
        {"minQty": 6, "priceMinor": 2599, "currency": "EUR"},
        {"minQty": 24, "priceMinor": 2199, "currency": "EUR"},
    ])
    quote = run(PricingEngine(a).quote_b2c("p1", qty))[1]
    assert quote.final_unit_price_minor == expected
    assert (quote.quantity_tier or {}).get("minQty") == tier
    assert quote.price_semantics == "gross"


def test_b2c_tier_then_subscription_uses_half_up_minor_units():
    db = AsyncDatabase("pricing_subscription_rounding")
    a = access(db); seed(a, b2c=101, tiers=[{"minQty": 2, "priceMinor": 101, "currency": "EUR"}])
    quote = run(PricingEngine(a).quote_b2c("p1", 2, subscription_discount_percent=50))[1]
    assert quote.subscription_discount_minor == 51
    assert quote.final_unit_price_minor == 50
    assert quote.line_total_minor == 100


def test_missing_subscription_discount_and_invalid_tax_fail_closed():
    db = AsyncDatabase("pricing_missing_configuration")
    a = access(db); seed(a)
    settings = {"currency": "EUR", "freeShippingThresholdMinor": 5900, "shippingFeeMinor": 490}
    with pytest.raises(PricingError, match="Rabatt"):
        run(PricingEngine(a).quote_b2c_basket([{"productId": "p1", "qty": 1}], settings, subscription=True))
    db.raw.products.update_one({"tenantId": "tenant-a", "id": "p1"}, {"$unset": {"taxRate": ""}})
    with pytest.raises(PricingError, match="Steuer"):
        run(PricingEngine(a).quote_b2c("p1", 1))


@pytest.mark.parametrize("price,shipping", [(5899, 490), (5900, 0), (5901, 0)])
def test_shipping_threshold_uses_payable_merchandise(price, shipping):
    db = AsyncDatabase(f"pricing_shipping_{price}")
    a = access(db); seed(a, b2c=price)
    settings = {"currency": "EUR", "freeShippingThresholdMinor": 5900, "shippingFeeMinor": 490}
    basket = run(PricingEngine(a).quote_b2c_basket([{"productId": "p1", "qty": 1}], settings, subscription=False))
    assert basket.shipping_minor == shipping


def test_discount_can_move_basket_below_shipping_threshold():
    db = AsyncDatabase("pricing_shipping_discount")
    a = access(db); seed(a, b2c=6200)
    settings = {"currency": "EUR", "freeShippingThresholdMinor": 5900, "shippingFeeMinor": 490}
    basket = run(PricingEngine(a).quote_b2c_basket([{"productId": "p1", "qty": 1}], settings,
                                                   subscription=False, basket_discount_percent=10))
    assert basket.payable_merchandise_minor == 5580
    assert basket.shipping_minor == 490


def test_duplicate_cart_lines_are_aggregated_before_tier_selection():
    db = AsyncDatabase("pricing_aggregate")
    a = access(db); seed(a, tiers=[{"minQty": 6, "priceMinor": 2500, "currency": "EUR"}])
    settings = {"currency": "EUR", "freeShippingThresholdMinor": 999999, "shippingFeeMinor": 0}
    basket = run(PricingEngine(a).quote_b2c_basket([
        {"productId": "p1", "qty": 3}, {"productId": "p1", "qty": 3}], settings, subscription=False))
    assert len(basket.lines) == 1
    assert basket.lines[0][1].quantity == Decimal("6")
    assert basket.lines[0][1].final_unit_price_minor == 2500


def test_snapshot_is_immutable_after_configuration_changes():
    db = AsyncDatabase("pricing_snapshot")
    a = access(db); seed(a, b2c=2999, tiers=[{"minQty": 2, "priceMinor": 2899, "currency": "EUR"}])
    product, quote = run(PricingEngine(a).quote_b2c("p1", 2, subscription_discount_percent=10))
    snapshot = quote.snapshot(product)
    db.raw.products.update_one({"tenantId": "tenant-a", "id": "p1"}, {"$set": {
        "b2cPriceMinor": 9999, "taxRate": 19, "b2cTiers": [{"minQty": 2, "priceMinor": 8888, "currency": "EUR"}]}})
    assert snapshot["unitPriceMinor"] == 2609
    assert snapshot["taxRate"] == 7
    assert snapshot["quantityTier"]["priceMinor"] == 2899
    assert snapshot["subscriptionDiscountPercent"] == 10


def test_b2b_snapshot_keeps_expired_promotion_price():
    db = AsyncDatabase("pricing_promotion_snapshot")
    a = access(db); seed(a)
    start = datetime(2026, 11, 1, tzinfo=timezone.utc)
    end = datetime(2026, 12, 1, tzinfo=timezone.utc)
    run(a.pricing_promotions.insert_one({"id": "promo", "name": "Thanks", "productId": "p1", "companyId": None,
        "priceMinor": 1800, "currency": "EUR", "startsAt": start.isoformat(), "endsAt": end.isoformat()}))
    product, quote = run(PricingEngine(a).quote_b2b("c1", "p1", 2, at=start))
    snapshot = quote.snapshot(product)
    db.raw.pricing_promotions.delete_many({})
    assert snapshot["unitPriceMinor"] == 1800
    assert snapshot["promotion"]["id"] == "promo"
    assert snapshot["priceSemantics"] == "net"


def test_corrupt_tiers_and_shipping_currency_fail_closed():
    db = AsyncDatabase("pricing_corrupt")
    a = access(db); seed(a, tiers=[
        {"minQty": 2, "priceMinor": 2000, "currency": "EUR"},
        {"minQty": 2, "priceMinor": 1900, "currency": "EUR"},
    ])
    with pytest.raises(PricingError, match="doppelte"):
        run(PricingEngine(a).quote_b2c("p1", 2))
    db.raw.products.update_one({"tenantId": "tenant-a", "id": "p1"}, {"$set": {"b2cTiers": []}})
    with pytest.raises(PricingError, match="Versandkosten"):
        run(PricingEngine(a).quote_b2c_basket([{"productId": "p1", "qty": 1}], {
            "currency": "USD", "freeShippingThresholdMinor": 5900, "shippingFeeMinor": 490,
        }, subscription=False))


def test_naive_pricing_time_and_oversized_quantity_fail_as_domain_errors():
    db = AsyncDatabase("pricing_boundaries")
    a = access(db); seed(a)
    with pytest.raises(PricingError, match="Zeitzone"):
        run(PricingEngine(a).quote_b2b("c1", "p1", 1, at=datetime(2026, 1, 1)))
    with pytest.raises(PricingError, match="Wertebereich"):
        run(PricingEngine(a).quote_b2c("p1", "999999999999999999999"))


def test_b2b_quote_rejects_aggregate_money_overflow_as_safe_api_error():
    from fastapi import HTTPException
    from app.routers import pricing

    db = AsyncDatabase("pricing_quote_overflow")
    a = access(db); seed(a, standard=5_000_000_000_000_000)
    body = PricingQuoteIn.model_validate({
        "companyId": "c1",
        "items": [{"productId": "p1", "qty": 1}, {"productId": "p1", "qty": 1}],
    })
    user = {"id": "admin", "role": "admin", "_tenant_context": a.context}
    with pytest.raises(HTTPException) as exc:
        run(pricing.quote_b2b(body, user, a))
    assert (exc.value.status_code, exc.value.detail) == (
        409, "Preisvorschau überschreitet den unterstützten Wertebereich")


def test_transaction_inputs_ignore_client_supplied_prices():
    order = OrderCreate.model_validate({
        "companyId": "c1", "items": [{"productId": "p1", "qty": 2, "price": 0.01}],
    })
    shop_order = ShopOrderIn.model_validate({
        "items": [{"productId": "p1", "qty": 2, "price": 0.01}],
        "customer": {"name": "Customer", "email": "customer@example.test"},
        "total": 0.02,
    })
    assert order.model_dump() == {"companyId": "c1", "items": [{"productId": "p1", "qty": 2.0}]}
    assert shop_order.model_dump()["items"] == [{"productId": "p1", "qty": 2.0}]
    assert "total" not in shop_order.model_dump()


def test_sales_customer_price_permissions_are_company_scoped(monkeypatch):
    from fastapi import HTTPException
    from app.models import CustomerPriceIn
    from app.routers import pricing

    db = AsyncDatabase("pricing_sales_permissions")
    context = TenantContext(tenant_id="tenant-a", actor_user_id="sales", membership_id="mbr-sales",
        role="sales", resolution_source=TenantResolutionSource.MEMBERSHIP, default_currency="EUR")
    scoped = TenantBusinessAccess(db, context)
    run(scoped.companies.insert_one({"id": "own", "assignedSalesRepId": "sales", "active": True}))
    run(scoped.companies.insert_one({"id": "other", "assignedSalesRepId": "someone-else", "active": True}))
    run(scoped.products.insert_one({"id": "p1", "active": True, "taxRate": 7, "standardPriceMinor": 1000, "currency": "EUR"}))
    user = {"id": "sales", "role": "sales", "companyId": None, "_tenant_context": context}
    async def no_audit(*_args, **_kwargs): return None
    monkeypatch.setattr(pricing, "tenant_audit", no_audit)
    result = run(pricing.upsert_customer_price(CustomerPriceIn(companyId="own", productId="p1", price=9), user, scoped))
    assert result["priceMinor"] == 900
    with pytest.raises(HTTPException) as denied:
        run(pricing.upsert_customer_price(CustomerPriceIn(companyId="other", productId="p1", price=8), user, scoped))
    assert denied.value.status_code == 403


def test_sales_cannot_create_tenant_wide_promotion(monkeypatch):
    from fastapi import HTTPException
    from app.models import B2BPromotionIn
    from app.routers import pricing

    db = AsyncDatabase("pricing_sales_promotion")
    context = TenantContext(tenant_id="tenant-a", actor_user_id="sales", membership_id="mbr-sales",
        role="sales", resolution_source=TenantResolutionSource.MEMBERSHIP, default_currency="EUR")
    scoped = TenantBusinessAccess(db, context)
    run(scoped.products.insert_one({"id": "p1", "active": True, "taxRate": 7, "standardPriceMinor": 1000, "currency": "EUR"}))
    user = {"id": "sales", "role": "sales", "companyId": None, "_tenant_context": context}
    now = datetime.now(timezone.utc)
    body = B2BPromotionIn(name="General", productId="p1", price=9,
                          startsAt=now, endsAt=now + timedelta(days=1))
    with pytest.raises(HTTPException) as denied:
        run(pricing.create_promotion(body, user, scoped))
    assert denied.value.status_code == 403
    assert db.raw.pricing_promotions.count_documents({}) == 0


def test_direct_machine_sale_is_gross_and_financing_formula_is_not_part_of_engine():
    db = AsyncDatabase("pricing_machine")
    a = access(db)
    run(a.machines.insert_one({"id": "m1", "name": "Machine", "priceMinor": 123456,
                               "currency": "EUR", "taxRate": 19, "active": True}))
    _machine, quote = run(PricingEngine(a).quote_b2c_machine("m1"))
    assert (quote.final_unit_price_minor, quote.price_semantics, quote.price_source) == (
        123456, "gross", "machine_b2c_standard")
    assert not hasattr(PricingEngine, "quote_financing")
