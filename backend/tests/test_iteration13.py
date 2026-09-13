"""Iteration 13 tests: newsletter unsubscribe + B2C shop order details persistence."""
import time
import re
import os
import secrets
import pytest
import requests
from pymongo import MongoClient
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ.get("DB_NAME", "test_database")
_mongo = MongoClient(MONGO_URL)
_db = _mongo[DB_NAME]


# ---------- helpers ----------
def _test_email() -> str:
    # Emails are lowercased by the backend, so use lowercase prefix
    return f"test_{secrets.token_hex(4)}_{int(time.time())}@example.com"


# ---------- Newsletter unsubscribe (new in iter 13) ----------
class TestNewsletterUnsubscribe:
    def test_subscribe_stores_unsub_token(self, api_client, base_url):
        email = _test_email()
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email, "name": "TEST_User"}, timeout=30)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("ok") is True
        assert body.get("pending") is True
        assert "code" not in body  # pending users must not receive code in response

        # DB check: unsubToken must be present even before confirm
        doc = _db.newsletter.find_one({"email": email})
        assert doc, "Subscriber not stored"
        assert doc.get("unsubToken"), "unsubToken not created on subscribe"
        assert doc.get("confirmToken"), "confirmToken not created on subscribe"

    def test_unsubscribe_valid_token_deletes_subscriber(self, api_client, base_url):
        email = _test_email()
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email}, timeout=30)
        assert r.status_code == 200
        doc = _db.newsletter.find_one({"email": email})
        assert doc and doc.get("unsubToken")
        token = doc["unsubToken"]

        # Now unsubscribe
        r = api_client.get(f"{base_url}/api/newsletter/unsubscribe",
                           params={"token": token}, timeout=30, allow_redirects=False)
        assert r.status_code == 200, r.text
        assert "text/html" in r.headers.get("content-type", "").lower()
        assert "Abgemeldet" in r.text
        # Subscriber was removed from DB
        assert _db.newsletter.find_one({"email": email}) is None

    def test_unsubscribe_invalid_token_returns_404_html(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/newsletter/unsubscribe",
                           params={"token": "TEST_invalid_" + secrets.token_hex(6)}, timeout=30)
        assert r.status_code == 404
        assert "text/html" in r.headers.get("content-type", "").lower()
        assert "Link ung" in r.text  # "Link ungültig"

    def test_unsubscribe_same_token_twice_second_is_404(self, api_client, base_url):
        email = _test_email()
        api_client.post(f"{base_url}/api/newsletter/subscribe", json={"email": email}, timeout=30)
        doc = _db.newsletter.find_one({"email": email})
        token = doc["unsubToken"]
        r1 = api_client.get(f"{base_url}/api/newsletter/unsubscribe", params={"token": token}, timeout=30)
        assert r1.status_code == 200
        r2 = api_client.get(f"{base_url}/api/newsletter/unsubscribe", params={"token": token}, timeout=30)
        assert r2.status_code == 404


# ---------- Newsletter regression ----------
class TestNewsletterRegression:
    def test_subscribe_returns_pending_no_code(self, api_client, base_url):
        email = _test_email()
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email}, timeout=30)
        assert r.status_code == 200
        body = r.json()
        assert body.get("ok") is True
        assert body.get("pending") is True
        assert body.get("confirmed") is not True
        assert "code" not in body

    def test_confirm_issues_code_and_validate_works(self, api_client, base_url):
        email = _test_email()
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email}, timeout=30)
        assert r.status_code == 200
        doc = _db.newsletter.find_one({"email": email})
        assert doc and doc.get("confirmToken")
        # Confirm
        r2 = api_client.get(f"{base_url}/api/newsletter/confirm",
                            params={"token": doc["confirmToken"]}, timeout=30)
        assert r2.status_code == 200
        assert "best" in r2.text.lower()  # "bestätigt"
        m = re.search(r"SS-[A-F0-9]{6}", r2.text)
        assert m, "No SS-XXXXXX code in confirm response"
        code = m.group(0)
        # Validate the code
        r3 = api_client.post(f"{base_url}/api/shop/validate-code",
                             json={"code": code}, timeout=30)
        assert r3.status_code == 200
        vb = r3.json()
        assert vb.get("valid") is True
        assert isinstance(vb.get("percent"), int) and vb["percent"] > 0

    def test_validate_bad_code_returns_invalid(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/shop/validate-code",
                            json={"code": "SS-ZZZZZZ"}, timeout=30)
        assert r.status_code == 200
        assert r.json().get("valid") is False


# ---------- Shop order details (B2C) ----------
class TestShopOrderDetails:
    @pytest.fixture(scope="class")
    def shop_user(self, api_client, base_url):
        email = _test_email()
        password = "TESTpass1234"
        r = api_client.post(f"{base_url}/api/shop/register",
                            json={"name": "TEST_Shopper", "email": email, "password": password},
                            timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("access_token")
        assert data.get("user", {}).get("email") == email
        return {"email": email, "password": password, "token": data["access_token"], "id": data["user"]["id"]}

    def test_shop_me_returns_user(self, api_client, base_url, shop_user):
        r = api_client.get(f"{base_url}/api/shop/me",
                           headers={"Authorization": f"Bearer {shop_user['token']}"}, timeout=30)
        assert r.status_code == 200
        assert r.json()["email"] == shop_user["email"]

    def test_create_order_as_logged_in_and_verify_details(self, api_client, base_url, shop_user):
        # Fetch a valid B2C product
        rp = api_client.get(f"{base_url}/api/shop/products", timeout=30)
        assert rp.status_code == 200
        products = rp.json()
        assert products, "No shop products available"
        p = products[0]

        payload = {
            "items": [{"productId": p["id"], "qty": 2}],
            "customer": {"name": "TEST_Shopper", "email": shop_user["email"],
                         "street": "Teststr 1", "zip": "12345", "city": "Berlin"},
        }
        r = api_client.post(f"{base_url}/api/shop/orders",
                            json=payload,
                            headers={"Authorization": f"Bearer {shop_user['token']}"},
                            timeout=30)
        assert r.status_code == 200, r.text
        created = r.json()
        oid = created["id"]

        # Verify it appears in /shop/my-orders with FULL details
        r2 = api_client.get(f"{base_url}/api/shop/my-orders",
                            headers={"Authorization": f"Bearer {shop_user['token']}"}, timeout=30)
        assert r2.status_code == 200
        orders = r2.json()
        assert isinstance(orders, list) and len(orders) >= 1
        mine = next((o for o in orders if o["id"] == oid), None)
        assert mine, f"Order {oid} not returned"

        # Check required fields for the "Meine Bestellungen" details card
        assert isinstance(mine.get("items"), list) and len(mine["items"]) >= 1
        first_item = mine["items"][0]
        for key in ("name", "qty", "price"):
            assert key in first_item, f"item missing {key}"
        assert "shipping" in mine
        assert "total" in mine and mine["total"] > 0
        assert "taxTotal" in mine
        assert "status" in mine  # Lieferstatus row
        assert "paymentStatus" in mine  # StatusBadge
        # discount should be a number (>=0)
        assert isinstance(mine.get("discount", 0), (int, float))

    def test_my_orders_unauthorized(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/shop/my-orders", timeout=30)
        assert r.status_code in (401, 403)

    def test_no_mongo_objectid_leak(self, api_client, base_url, shop_user):
        r = api_client.get(f"{base_url}/api/shop/my-orders",
                           headers={"Authorization": f"Bearer {shop_user['token']}"}, timeout=30)
        assert r.status_code == 200
        assert "_id" not in r.text  # ObjectId scrubbed


# ---------- Cleanup ----------
@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    try:
        _db.newsletter.delete_many({"email": {"$regex": "^test_", "$options": "i"}})
        # remove test users + their orders
        test_users = list(_db.users.find({"email": {"$regex": "^test_", "$options": "i"}}))
        uids = [u["id"] for u in test_users]
        if uids:
            _db.shop_orders.delete_many({"userId": {"$in": uids}})
            _db.users.delete_many({"id": {"$in": uids}})
    except Exception as e:
        print(f"cleanup: {e}")
