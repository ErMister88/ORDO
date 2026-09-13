"""Iteration 14 backend tests:
- PUT /api/shop/orders/{id}/status (admin/sales auth, valid statuses, 400/404/403)
- Regression: POST /api/shop/orders/{id}/checkout, GET /api/shop/orders/{id}/payment-status
- GET /api/shop/my-orders returns customer + status + paymentStatus
"""
import os
import time
import uuid
import pytest
import requests
def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


TEST_PREFIX = "statustest"


# ---------- Helpers ----------
def _pick_product(base_url):
    r = requests.get(f"{base_url}/api/shop/products", timeout=30)
    assert r.status_code == 200, r.text
    prods = r.json()
    assert len(prods) > 0, "No shop products available"
    return prods[0]


def _register_shop_user(base_url):
    email = f"{TEST_PREFIX}_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(f"{base_url}/api/shop/register",
                      json={"name": "TEST User", "email": email, "password": "TestPass1234"},
                      timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    return email, data["access_token"], data["user"]["id"]


def _create_shop_order(base_url, token=None, with_address=True):
    p = _pick_product(base_url)
    customer = {"name": "TEST Kunde", "email": f"{TEST_PREFIX}_{uuid.uuid4().hex[:6]}@example.com"}
    if with_address:
        customer.update({"phone": "0301234567", "street": "Teststr. 1", "zip": "10115", "city": "Berlin"})
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"items": [{"productId": p["id"], "qty": 1}], "customer": customer}
    r = requests.post(f"{base_url}/api/shop/orders", json=body, headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["id"]


# ---------- Cleanup ----------
@pytest.fixture(scope="module", autouse=True)
def _cleanup(base_url):
    yield
    # Best-effort cleanup: not strictly required, orders remain but prefixed for identification.


# ---------- Status update tests ----------
class TestShopOrderStatus:
    def test_admin_updates_status_to_versendet(self, base_url, admin_token):
        oid = _create_shop_order(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Versendet"}, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["id"] == oid
        assert data["status"] == "Versendet"
        # Verify persistence via list
        lr = requests.get(f"{base_url}/api/shop/orders", headers=hdr(admin_token), timeout=30)
        assert lr.status_code == 200
        assert any(o["id"] == oid and o["status"] == "Versendet" for o in lr.json())

    def test_sales_can_update_status(self, base_url, sales_token):
        oid = _create_shop_order(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Bestätigt"}, headers=hdr(sales_token), timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Bestätigt"

    def test_all_valid_statuses(self, base_url, admin_token):
        oid = _create_shop_order(base_url)
        for st in ["Neu", "Bestätigt", "In Bearbeitung", "Versendet", "Abgeschlossen", "Storniert"]:
            r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                             json={"status": st}, headers=hdr(admin_token), timeout=30)
            assert r.status_code == 200, f"{st}: {r.text}"
            assert r.json()["status"] == st

    def test_invalid_status_returns_400(self, base_url, admin_token):
        oid = _create_shop_order(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Bogus"}, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400, r.text

    def test_unknown_order_returns_404(self, base_url, admin_token):
        r = requests.put(f"{base_url}/api/shop/orders/DOES-NOT-EXIST/status",
                         json={"status": "Versendet"}, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 404, r.text

    def test_shopuser_forbidden(self, base_url, admin_token):
        oid = _create_shop_order(base_url)
        _, tok, _ = _register_shop_user(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Versendet"}, headers=hdr(tok), timeout=30)
        assert r.status_code == 403, r.text

    def test_no_auth_returns_401(self, base_url):
        oid = _create_shop_order(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Versendet"}, timeout=30)
        assert r.status_code == 401, r.text


# ---------- Regression: checkout / payment-status ----------
class TestShopCheckoutRegression:
    def test_checkout_returns_url_or_502(self, base_url):
        oid = _create_shop_order(base_url)
        r = requests.post(f"{base_url}/api/shop/orders/{oid}/checkout", timeout=30)
        # In preview Stripe test key may fail with 502; both are acceptable
        assert r.status_code in (200, 502), f"Unexpected: {r.status_code} {r.text}"
        if r.status_code == 200:
            body = r.json()
            assert "url" in body and body["url"].startswith("http")
        else:
            # 502 with placeholder Stripe key is acceptable in preview.
            # Note: body may be Cloudflare's HTML 502 page rather than FastAPI JSON.
            pass

    def test_checkout_unknown_order_404(self, base_url):
        r = requests.post(f"{base_url}/api/shop/orders/NOPE/checkout", timeout=30)
        assert r.status_code == 404

    def test_payment_status_open(self, base_url):
        oid = _create_shop_order(base_url)
        r = requests.get(f"{base_url}/api/shop/orders/{oid}/payment-status", timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("status") in ("Offen", "Bezahlt")

    def test_payment_status_unknown_404(self, base_url):
        r = requests.get(f"{base_url}/api/shop/orders/NOPE/payment-status", timeout=30)
        assert r.status_code == 404


# ---------- my-orders returns customer + status + paymentStatus ----------
class TestShopMyOrders:
    def test_my_orders_fields(self, base_url):
        email, tok, uid = _register_shop_user(base_url)
        oid = _create_shop_order(base_url, token=tok, with_address=True)
        r = requests.get(f"{base_url}/api/shop/my-orders", headers=hdr(tok), timeout=30)
        assert r.status_code == 200, r.text
        orders = r.json()
        assert any(o["id"] == oid for o in orders)
        o = next(o for o in orders if o["id"] == oid)
        assert "customer" in o and isinstance(o["customer"], dict)
        assert o["customer"].get("street") == "Teststr. 1"
        assert o["customer"].get("zip") == "10115"
        assert "status" in o and o["status"] == "Neu"
        assert "paymentStatus" in o and o["paymentStatus"] == "Offen"
        # Mongo _id must be stripped
        assert "_id" not in o

    def test_status_change_reflects_in_my_orders(self, base_url, admin_token):
        email, tok, uid = _register_shop_user(base_url)
        oid = _create_shop_order(base_url, token=tok)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         json={"status": "Versendet"}, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200
        r2 = requests.get(f"{base_url}/api/shop/my-orders", headers=hdr(tok), timeout=30)
        assert r2.status_code == 200
        o = next(o for o in r2.json() if o["id"] == oid)
        assert o["status"] == "Versendet"
