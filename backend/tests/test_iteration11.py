"""Iteration 11 backend tests: Newsletter + configurable discount + Push broadcast.

Covers:
- Newsletter subscribe (idempotent) + validate-code
- Shop settings: newsletterDiscountPercent + newsletterDiscountEnabled
- Shop orders: promoCode applies discount + tax reduced proportionally
- Push register/broadcast/stats (expected 502 in preview with placeholder key)
"""
import os
import pytest
import requests

from conftest import hdr


# ---------- helpers ----------
def _admin_products(base_url, admin_token):
    r = requests.get(f"{base_url}/api/products", headers=hdr(admin_token), timeout=30)
    assert r.status_code == 200
    return r.json()


def _first_shop_product(base_url):
    r = requests.get(f"{base_url}/api/shop/products", timeout=30)
    assert r.status_code == 200
    prods = [p for p in r.json() if p.get("b2cPrice")]
    assert prods, "no shop products with b2cPrice"
    return prods[0]


# ---------- Newsletter subscribe ----------
class TestNewsletter:
    def test_subscribe_success(self, base_url):
        email = "TEST_nl_iter11_a@example.com"
        r = requests.post(f"{base_url}/api/newsletter/subscribe",
                          json={"email": email, "name": "TEST"}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["ok"] is True
        assert d["code"].startswith("SS-")
        assert len(d["code"]) == 3 + 6  # SS- + 6 hex
        assert d["percent"] == 10
        assert d["enabled"] is True

    def test_subscribe_idempotent(self, base_url):
        email = "TEST_nl_iter11_idem@example.com"
        r1 = requests.post(f"{base_url}/api/newsletter/subscribe",
                           json={"email": email}, timeout=30)
        r2 = requests.post(f"{base_url}/api/newsletter/subscribe",
                           json={"email": email.upper(), "name": "again"}, timeout=30)
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["code"] == r2.json()["code"], "same email must return same code"

    def test_subscribe_invalid_email(self, base_url):
        r = requests.post(f"{base_url}/api/newsletter/subscribe",
                          json={"email": "not-an-email"}, timeout=30)
        assert r.status_code == 400


# ---------- validate-code ----------
class TestValidateCode:
    def test_valid_code(self, base_url):
        email = "TEST_nl_iter11_valid@example.com"
        sub = requests.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email}, timeout=30).json()
        code = sub["code"]
        r = requests.post(f"{base_url}/api/shop/validate-code",
                          json={"code": code}, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["valid"] is True
        assert d["percent"] == 10

    def test_invalid_code(self, base_url):
        r = requests.post(f"{base_url}/api/shop/validate-code",
                          json={"code": "SS-DEADBE"}, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["valid"] is False
        assert d["percent"] == 0

    def test_empty_code(self, base_url):
        r = requests.post(f"{base_url}/api/shop/validate-code",
                          json={"code": ""}, timeout=30)
        assert r.status_code == 200
        assert r.json()["valid"] is False


# ---------- shop settings ----------
class TestShopSettings:
    def test_get_settings_includes_newsletter_fields(self, base_url):
        r = requests.get(f"{base_url}/api/shop/settings", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "freeShippingThreshold" in d
        assert "shippingFee" in d
        assert "newsletterDiscountPercent" in d
        assert "newsletterDiscountEnabled" in d
        assert isinstance(d["newsletterDiscountPercent"], int)
        assert isinstance(d["newsletterDiscountEnabled"], bool)

    def test_admin_can_update_discount(self, base_url, admin_token):
        # bump to 15% + disabled=False
        r = requests.put(f"{base_url}/api/shop/settings",
                        json={"freeShippingThreshold": 50.0, "shippingFee": 4.90,
                              "newsletterDiscountPercent": 15, "newsletterDiscountEnabled": True},
                        headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        g = requests.get(f"{base_url}/api/shop/settings", timeout=30).json()
        assert g["newsletterDiscountPercent"] == 15
        assert g["newsletterDiscountEnabled"] is True
        # restore
        rr = requests.put(f"{base_url}/api/shop/settings",
                         json={"freeShippingThreshold": 50.0, "shippingFee": 4.90,
                               "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True},
                         headers=hdr(admin_token), timeout=30)
        assert rr.status_code == 200

    def test_toggle_disabled_makes_validate_return_false(self, base_url, admin_token):
        email = "TEST_nl_iter11_toggle@example.com"
        code = requests.post(f"{base_url}/api/newsletter/subscribe",
                             json={"email": email}, timeout=30).json()["code"]
        # disable
        requests.put(f"{base_url}/api/shop/settings",
                     json={"freeShippingThreshold": 50.0, "shippingFee": 4.90,
                           "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": False},
                     headers=hdr(admin_token), timeout=30)
        try:
            v = requests.post(f"{base_url}/api/shop/validate-code",
                              json={"code": code}, timeout=30).json()
            assert v["valid"] is False
            assert v["percent"] == 0
        finally:
            # re-enable
            requests.put(f"{base_url}/api/shop/settings",
                         json={"freeShippingThreshold": 50.0, "shippingFee": 4.90,
                               "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True},
                         headers=hdr(admin_token), timeout=30)

    def test_sales_cannot_update_settings(self, base_url, sales_token):
        r = requests.put(f"{base_url}/api/shop/settings",
                        json={"freeShippingThreshold": 50.0, "shippingFee": 4.90,
                              "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True},
                        headers=hdr(sales_token), timeout=30)
        assert r.status_code == 403


# ---------- shop orders with promo ----------
class TestShopOrdersPromo:
    def test_order_no_promo_no_discount(self, base_url):
        p = _first_shop_product(base_url)
        r = requests.post(f"{base_url}/api/shop/orders", json={
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST", "email": "TEST_iter11_np@example.com",
                          "street": "S", "zip": "10115", "city": "Berlin"},
        }, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["discount"] == 0
        assert d["discountPercent"] == 0

    def test_order_with_invalid_promo_no_discount(self, base_url):
        p = _first_shop_product(base_url)
        r = requests.post(f"{base_url}/api/shop/orders", json={
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST", "email": "TEST_iter11_ip@example.com"},
            "promoCode": "SS-INVALID",
        }, timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert d["discount"] == 0
        assert d["discountPercent"] == 0

    def test_order_with_valid_promo_applies_discount(self, base_url):
        p = _first_shop_product(base_url)
        # subscribe to get code
        sub = requests.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": "TEST_iter11_promo@example.com"}, timeout=30).json()
        code = sub["code"]

        qty = 3  # ensures subtotal > freeShippingThreshold=50 (e.g., 3*19.90=59.70) or below
        price = float(p["b2cPrice"])
        gross_subtotal = round(price * qty, 2)
        r = requests.post(f"{base_url}/api/shop/orders", json={
            "items": [{"productId": p["id"], "qty": qty}],
            "customer": {"name": "TEST", "email": "TEST_iter11_promo@example.com",
                          "street": "S", "zip": "10115", "city": "Berlin"},
            "promoCode": code,
        }, timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        expected_discount = round(gross_subtotal * 10 / 100, 2)
        expected_sub_after = round(gross_subtotal - expected_discount, 2)
        assert d["discountPercent"] == 10
        assert d["discount"] == expected_discount, f"expected {expected_discount}, got {d['discount']}"
        assert d["subtotal"] == expected_sub_after
        # tax reduced proportionally: sum of taxBreakdown values should equal taxTotal
        assert round(sum(d["taxBreakdown"].values()), 2) == round(d["taxTotal"], 2)
        # tax on discounted subtotal (approx): gross_sub * rate/(100+rate) * factor
        rate = int(p.get("taxRate", 7))
        factor = 1 - 10 / 100
        expected_tax = round(gross_subtotal * rate / (100 + rate) * factor, 2)
        # allow 0.02 tolerance
        assert abs(d["taxTotal"] - expected_tax) <= 0.03, f"tax {d['taxTotal']} vs {expected_tax}"

    def test_order_promo_case_insensitive(self, base_url):
        p = _first_shop_product(base_url)
        sub = requests.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": "TEST_iter11_ci@example.com"}, timeout=30).json()
        code = sub["code"].lower()
        r = requests.post(f"{base_url}/api/shop/orders", json={
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST", "email": "TEST_iter11_ci@example.com"},
            "promoCode": code,
        }, timeout=30)
        assert r.status_code == 200
        assert r.json()["discountPercent"] == 10


# ---------- push ----------
class TestPush:
    def test_register_push_upstream_expected_failure(self, base_url):
        # Placeholder key -> expected 500 or 502 gracefully; no crash
        r = requests.post(f"{base_url}/api/register-push", json={
            "user_id": "TEST_iter11_u1", "platform": "android", "device_token": "tok-abc"
        }, timeout=30)
        assert r.status_code in (201, 500, 502), f"unexpected {r.status_code} {r.text}"

    def test_broadcast_requires_admin(self, base_url, customer_token):
        r = requests.post(f"{base_url}/api/push/broadcast",
                         json={"title": "Hi", "message": "Hello"},
                         headers=hdr(customer_token), timeout=30)
        assert r.status_code == 403

    def test_broadcast_no_auth(self, base_url):
        r = requests.post(f"{base_url}/api/push/broadcast",
                         json={"title": "Hi", "message": "Hello"}, timeout=30)
        assert r.status_code in (401, 403)

    def test_broadcast_validation_empty_title(self, base_url, admin_token):
        r = requests.post(f"{base_url}/api/push/broadcast",
                         json={"title": "  ", "message": "Hello"},
                         headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400

    def test_broadcast_validation_empty_message(self, base_url, admin_token):
        r = requests.post(f"{base_url}/api/push/broadcast",
                         json={"title": "Hello", "message": ""},
                         headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400

    def test_broadcast_upstream_expected_502(self, base_url, admin_token):
        # placeholder EMERGENT_PUSH_KEY -> expected 502
        r = requests.post(f"{base_url}/api/push/broadcast",
                         json={"title": "Nur heute", "message": "20% auf Espresso"},
                         headers=hdr(admin_token), timeout=30)
        # If there are no registered devices, send_push returns early and returns 200.
        # Otherwise upstream should fail -> 502.
        assert r.status_code in (200, 502), r.text
        if r.status_code == 502:
            assert "Push" in r.json().get("detail", "") or "erst nach" in r.json().get("detail", "")

    def test_stats_admin_only(self, base_url, admin_token, customer_token):
        ra = requests.get(f"{base_url}/api/push/stats", headers=hdr(admin_token), timeout=30)
        assert ra.status_code == 200
        assert isinstance(ra.json().get("registered"), int)
        rc = requests.get(f"{base_url}/api/push/stats", headers=hdr(customer_token), timeout=30)
        assert rc.status_code == 403


# ---------- cleanup ----------
@pytest.fixture(scope="module", autouse=True)
def _cleanup_after(base_url, admin_token):
    yield
    # best-effort: nothing critical to clean; test emails scoped by TEST_ prefix
    # (no admin endpoints to delete newsletter subscribers; that's fine)
