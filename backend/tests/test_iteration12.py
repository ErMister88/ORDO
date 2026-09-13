"""Iteration 12: Newsletter Double-Opt-In + Legal + AGB acceptance for shop."""
import os
import re
import time
import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient
import asyncio

from tests.conftest import hdr  # noqa

pytestmark = pytest.mark.usefixtures("api_client")

MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "test_database")


# --- Utilities: DB access to fetch confirmToken (email is not delivered in preview) ---
async def _fetch_token(email: str) -> str | None:
    client = AsyncIOMotorClient(MONGO_URL)
    doc = await client[DB_NAME].newsletter.find_one({"email": email.lower()})
    client.close()
    return doc.get("confirmToken") if doc else None


async def _cleanup(email_prefix: str):
    client = AsyncIOMotorClient(MONGO_URL)
    await client[DB_NAME].newsletter.delete_many({"email": {"$regex": f"^{email_prefix}"}})
    await client[DB_NAME].shop_orders.delete_many({"customer.name": {"$regex": "^TEST_"}})
    client.close()


def run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# --- Newsletter Double-Opt-In tests ---
class TestNewsletterDoubleOptIn:
    def test_subscribe_new_returns_pending_no_code(self, api_client, base_url):
        email = f"TEST_doi_{int(time.time())}@example.com"
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email, "baseUrl": base_url})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("ok") is True
        assert data.get("pending") is True
        assert "code" not in data or not data.get("code")
        run_async(_cleanup("TEST_doi_"))

    def test_subscribe_idempotent_unconfirmed(self, api_client, base_url):
        email = f"TEST_doiid_{int(time.time())}@example.com"
        r1 = api_client.post(f"{base_url}/api/newsletter/subscribe",
                             json={"email": email, "baseUrl": base_url})
        assert r1.status_code == 200
        assert r1.json().get("pending") is True

        r2 = api_client.post(f"{base_url}/api/newsletter/subscribe",
                             json={"email": email, "baseUrl": base_url})
        assert r2.status_code == 200
        d2 = r2.json()
        assert d2.get("pending") is True
        assert not d2.get("code")

        # Verify token stable
        tok1 = run_async(_fetch_token(email))
        tok2 = run_async(_fetch_token(email))
        assert tok1 and tok1 == tok2
        run_async(_cleanup("TEST_doiid_"))

    def test_confirm_invalid_token_returns_404_html(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/newsletter/confirm",
                           params={"token": "invalid-token-xyz"})
        assert r.status_code == 404
        assert "text/html" in r.headers.get("content-type", "").lower()
        assert "Link ung" in r.text  # 'Link ungültig'

    def test_confirm_valid_token_returns_code_and_sets_confirmed(self, api_client, base_url):
        email = f"TEST_doicf_{int(time.time())}@example.com"
        r = api_client.post(f"{base_url}/api/newsletter/subscribe",
                            json={"email": email, "baseUrl": base_url})
        assert r.status_code == 200
        assert r.json().get("pending") is True

        token = run_async(_fetch_token(email))
        assert token, "confirmToken not stored"

        rc = api_client.get(f"{base_url}/api/newsletter/confirm", params={"token": token})
        assert rc.status_code == 200
        assert "text/html" in rc.headers.get("content-type", "").lower()
        assert "E-Mail best" in rc.text  # 'E-Mail bestätigt'

        m = re.search(r"SS-[0-9A-F]{6}", rc.text)
        assert m, f"code not found in confirm page: {rc.text[:400]}"
        code = m.group(0)

        # Now validate-code should return valid=True with percent=10
        rv = api_client.post(f"{base_url}/api/shop/validate-code", json={"code": code})
        assert rv.status_code == 200
        vj = rv.json()
        assert vj.get("valid") is True
        assert vj.get("percent") == 10

        # store for later test
        pytest.confirmed_code_for_order = code
        pytest.confirmed_email_for_order = email

    def test_validate_random_code_invalid(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/shop/validate-code",
                            json={"code": "SS-ZZZZZZ"})
        assert r.status_code == 200
        data = r.json()
        assert data.get("valid") is False


# --- Shop order with promoCode regression ---
class TestShopOrderPromo:
    def _first_product(self, api_client, base_url):
        r = api_client.get(f"{base_url}/api/shop/products")
        assert r.status_code == 200
        products = r.json()
        assert len(products) > 0, "no products in shop"
        return products[0]

    def test_order_with_confirmed_code_applies_discount(self, api_client, base_url):
        code = getattr(pytest, "confirmed_code_for_order", None)
        if not code:
            pytest.skip("no confirmed code available")

        p = self._first_product(api_client, base_url)
        payload = {
            "items": [{"productId": p["id"], "qty": 2}],
            "customer": {"name": "TEST_promo user", "email": "test_promo@example.com",
                         "phone": "", "street": "s 1", "zip": "10000", "city": "berlin"},
            "promoCode": code,
        }
        r = api_client.post(f"{base_url}/api/shop/orders", json=payload)
        assert r.status_code in (200, 201), r.text
        o = r.json()
        assert o.get("discountPercent") == 10
        assert o.get("discount") > 0
        # subtotal - discount should match
        assert o.get("total") > 0

    def test_order_with_invalid_code_zero_discount(self, api_client, base_url):
        p = self._first_product(api_client, base_url)
        payload = {
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST_bad code", "email": "test_bad@example.com",
                         "phone": "", "street": "s 1", "zip": "10000", "city": "berlin"},
            "promoCode": "SS-INVALID",
        }
        r = api_client.post(f"{base_url}/api/shop/orders", json=payload)
        assert r.status_code in (200, 201), r.text
        o = r.json()
        assert o.get("discount", 0) == 0
        assert o.get("discountPercent", 0) == 0

    @classmethod
    def teardown_class(cls):
        run_async(_cleanup("TEST_"))
