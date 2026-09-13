"""Iteration 15 backend tests:
- B2C-only newsletter discount (shopuser gets discount; admin/customer/sales don't).
- Shop user address: PUT /api/shop/me/address, GET /api/shop/me, register response includes address.
- Order statusHistory + optional GLS tracking on 'Versendet'.
"""
import os
import uuid
import secrets
import pytest
import requests

from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URL = os.environ.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME")

TEST_PREFIX = "iter15"


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


@pytest.fixture(scope="module")
def mdb():
    assert MONGO_URL and DB_NAME, "Missing MONGO_URL/DB_NAME"
    cli = MongoClient(MONGO_URL)
    yield cli[DB_NAME]
    cli.close()


@pytest.fixture(scope="module")
def confirmed_code(mdb):
    """Insert a confirmed newsletter subscription with a promo code."""
    code = f"SS-{secrets.token_hex(3).upper()}"
    email = f"{TEST_PREFIX}_nl_{uuid.uuid4().hex[:6]}@example.com"
    doc = {
        "email": email,
        "name": "Iter15 Tester",
        "confirmed": True,
        "code": code,
        "confirmToken": secrets.token_urlsafe(16),
        "unsubToken": secrets.token_urlsafe(16),
    }
    mdb.newsletter.insert_one(doc)
    yield code
    mdb.newsletter.delete_one({"email": email})


def _pick_product(base_url):
    r = requests.get(f"{base_url}/api/shop/products", timeout=30)
    assert r.status_code == 200, r.text
    prods = r.json()
    assert prods
    return prods[0]


def _register_shop_user(base_url):
    email = f"{TEST_PREFIX}_{uuid.uuid4().hex[:8]}@example.com"
    r = requests.post(
        f"{base_url}/api/shop/register",
        json={"name": "TEST Iter15", "email": email, "password": "TestPass1234"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    return email, data["access_token"], data["user"]


# -------- (1) B2C-only newsletter discount --------
class TestB2COnlyDiscount:
    def test_shopuser_gets_discount(self, base_url, confirmed_code):
        _, tok, _ = _register_shop_user(base_url)
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            headers=hdr(tok),
            json={
                "items": [{"productId": p["id"], "qty": 2}],
                "customer": {"name": "Iter15", "email": "iter15@example.com",
                             "phone": "030", "street": "Str 1", "zip": "10115", "city": "Berlin"},
                "promoCode": confirmed_code,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["discountPercent"] >= 1, f"expected discount>0 got {body}"
        assert body["discount"] > 0

    def test_guest_gets_discount(self, base_url, confirmed_code):
        # Guest (no auth) is B2C too — should still get discount
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            json={
                "items": [{"productId": p["id"], "qty": 1}],
                "customer": {"name": "Guest", "email": "guest@example.com"},
                "promoCode": confirmed_code,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["discountPercent"] > 0
        assert body["discount"] > 0

    def test_b2b_admin_no_discount(self, base_url, admin_token, confirmed_code):
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            headers=hdr(admin_token),
            json={
                "items": [{"productId": p["id"], "qty": 2}],
                "customer": {"name": "B2B Admin", "email": "admin-b2c@example.com"},
                "promoCode": confirmed_code,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["discountPercent"] == 0, f"admin should NOT get discount, got {body}"
        assert body["discount"] == 0

    def test_b2b_customer_no_discount(self, base_url, customer_token, confirmed_code):
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            headers=hdr(customer_token),
            json={
                "items": [{"productId": p["id"], "qty": 1}],
                "customer": {"name": "B2B Kunde", "email": "kunde-b2c@example.com"},
                "promoCode": confirmed_code,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["discountPercent"] == 0
        assert body["discount"] == 0

    def test_b2b_sales_no_discount(self, base_url, sales_token, confirmed_code):
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            headers=hdr(sales_token),
            json={
                "items": [{"productId": p["id"], "qty": 1}],
                "customer": {"name": "Sales", "email": "sales-b2c@example.com"},
                "promoCode": confirmed_code,
            },
            timeout=30,
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["discountPercent"] == 0
        assert body["discount"] == 0


# -------- (2) Saved delivery address --------
class TestShopUserAddress:
    def test_register_response_includes_address_field(self, base_url):
        _, _, user = _register_shop_user(base_url)
        assert "address" in user, f"register response missing address: {user}"
        assert user["address"] == {}

    def test_login_response_includes_address(self, base_url):
        email = f"{TEST_PREFIX}_login_{uuid.uuid4().hex[:6]}@example.com"
        pw = "TestPass1234"
        r = requests.post(f"{base_url}/api/shop/register",
                          json={"name": "T", "email": email, "password": pw}, timeout=30)
        assert r.status_code == 200
        r2 = requests.post(f"{base_url}/api/shop/login",
                           json={"email": email, "password": pw}, timeout=30)
        assert r2.status_code == 200, r2.text
        assert "address" in r2.json()["user"]

    def test_put_address_saves_and_me_returns_it(self, base_url):
        _, tok, _ = _register_shop_user(base_url)
        addr = {"name": "Anna Adresse", "phone": "0301112222",
                "street": "Musterstr. 12", "zip": "10115", "city": "Berlin"}
        r = requests.put(f"{base_url}/api/shop/me/address",
                         headers=hdr(tok), json=addr, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["address"] == addr

        me = requests.get(f"{base_url}/api/shop/me", headers=hdr(tok), timeout=30)
        assert me.status_code == 200
        assert me.json()["address"] == addr

    def test_put_address_no_auth_401(self, base_url):
        r = requests.put(f"{base_url}/api/shop/me/address",
                         json={"name": "x", "phone": "", "street": "", "zip": "", "city": ""},
                         timeout=30)
        assert r.status_code == 401


# -------- (3) statusHistory + GLS tracking --------
class TestStatusHistoryTracking:
    def _new_order(self, base_url):
        p = _pick_product(base_url)
        r = requests.post(
            f"{base_url}/api/shop/orders",
            json={"items": [{"productId": p["id"], "qty": 1}],
                  "customer": {"name": "Track Test", "email": "track@example.com",
                               "zip": "10115"}},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def test_new_order_has_status_history_neu(self, base_url, admin_token):
        oid = self._new_order(base_url)
        lst = requests.get(f"{base_url}/api/shop/orders",
                           headers=hdr(admin_token), timeout=30).json()
        o = next(x for x in lst if x["id"] == oid)
        assert "statusHistory" in o and len(o["statusHistory"]) >= 1
        assert o["statusHistory"][0]["status"] == "Neu"
        assert o["statusHistory"][0].get("at")

    def test_versendet_with_tracking_appends_and_stores(self, base_url, admin_token):
        oid = self._new_order(base_url)
        r = requests.put(
            f"{base_url}/api/shop/orders/{oid}/status",
            headers=hdr(admin_token),
            json={"status": "Versendet", "trackingNumber": "01234567890"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "Versendet"
        assert data["trackingNumber"] == "01234567890"
        assert data["carrier"] == "GLS"
        # statusHistory now contains both Neu and Versendet
        statuses = [h["status"] for h in data["statusHistory"]]
        assert statuses[0] == "Neu"
        assert "Versendet" in statuses
        assert data["statusHistory"][-1]["status"] == "Versendet"
        # Mongo _id must be excluded
        assert "_id" not in data

    def test_versendet_without_tracking_ok(self, base_url, admin_token):
        oid = self._new_order(base_url)
        r = requests.put(
            f"{base_url}/api/shop/orders/{oid}/status",
            headers=hdr(admin_token),
            json={"status": "Versendet"},
            timeout=30,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "Versendet"
        assert not data.get("trackingNumber")

    def test_multiple_status_transitions_append(self, base_url, admin_token):
        oid = self._new_order(base_url)
        for st in ["Bestätigt", "In Bearbeitung", "Versendet"]:
            requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         headers=hdr(admin_token), json={"status": st}, timeout=30)
        lst = requests.get(f"{base_url}/api/shop/orders",
                           headers=hdr(admin_token), timeout=30).json()
        o = next(x for x in lst if x["id"] == oid)
        statuses = [h["status"] for h in o["statusHistory"]]
        # Should include all appended statuses in order
        assert statuses == ["Neu", "Bestätigt", "In Bearbeitung", "Versendet"]

    def test_invalid_status_400(self, base_url, admin_token):
        oid = self._new_order(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         headers=hdr(admin_token), json={"status": "XYZ"}, timeout=30)
        assert r.status_code == 400

    def test_shopuser_forbidden_403(self, base_url):
        oid = self._new_order(base_url)
        _, tok, _ = _register_shop_user(base_url)
        r = requests.put(f"{base_url}/api/shop/orders/{oid}/status",
                         headers=hdr(tok), json={"status": "Versendet"}, timeout=30)
        assert r.status_code == 403


# -------- Cleanup: remove all test users + orders created in this iteration --------
@pytest.fixture(scope="module", autouse=True)
def _cleanup(mdb):
    yield
    try:
        mdb.users.delete_many({"email": {"$regex": f"^{TEST_PREFIX}_"}})
        mdb.shop_orders.delete_many({"customer.email": {"$regex": f"@example\\.com$"},
                                     "customer.name": {"$in": ["Iter15", "Guest",
                                                               "B2B Admin", "B2B Kunde",
                                                               "Sales", "Track Test"]}})
        mdb.newsletter.delete_many({"email": {"$regex": f"^{TEST_PREFIX}_"}})
    except Exception as e:
        print(f"Cleanup warning: {e}")
