"""Iteration 20 — Security re-audit fix verification.

Coverage:
- SEC MACHINES SCOPING (MEDIUM): sales role is company-scoped in machines module.
  * GET /api/machine-requests: sales sees only in-scope; admin sees all
  * PUT /api/machine-requests/{id}: 403 out-of-scope, 200 in-scope
  * POST /api/machine-requests/{id}/accept: 403 out-of-scope for sales
  * GET /api/machines/leasing-contracts: sales sees only assigned companies
- SEC MACHINES FLOOR: coffeePricePerKg below product absoluteFloor -> 400
- SEC NEWSLETTER (LOW): malicious baseUrl must not influence emitted link — server APP_URL used only
- REGRESSION machines happy path: leasing request -> admin terms -> customer accept -> auto contract
- REGRESSION kauf checkout: Stripe url or 502 (preview)
- REGRESSION prior fixes: shopuser -> 403 on /products; customer /orders ignores client price;
  shop order checkout/payment-status require token or owner
- REGRESSION general: B2B admin/sales/customer login work
"""
import os
import uuid
import time
import secrets as pysecrets
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv(Path(__file__).parent.parent / ".env")

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]
_mongo = MongoClient(MONGO_URL)
_db = _mongo[DB_NAME]


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# Track resources we create so we can clean up after the run.
_CREATED = {
    "machine_requests": [],
    "contracts": [],
    "newsletter_emails": [],
    "shop_orders": [],
    "users": [],
    "orders": [],
}


def _cleanup():
    if _CREATED["machine_requests"]:
        _db.machine_requests.delete_many({"id": {"$in": _CREATED["machine_requests"]}})
    if _CREATED["contracts"]:
        _db.contracts.delete_many({"id": {"$in": _CREATED["contracts"]}})
    if _CREATED["newsletter_emails"]:
        _db.newsletter.delete_many({"email": {"$in": _CREATED["newsletter_emails"]}})
    if _CREATED["shop_orders"]:
        _db.shop_orders.delete_many({"id": {"$in": _CREATED["shop_orders"]}})
    if _CREATED["orders"]:
        _db.orders.delete_many({"id": {"$in": _CREATED["orders"]}})
    if _CREATED["users"]:
        _db.users.delete_many({"id": {"$in": _CREATED["users"]}})


@pytest.fixture(scope="module", autouse=True)
def _module_cleanup():
    yield
    _cleanup()


def _seed_machine_request(company_id: str, req_type: str = "leasing",
                          machine_id: str = None, machine_name: str = "Test-Maschine",
                          machine_price: float = 3200.0, status: str = "Angefragt",
                          terms: dict = None, user_id: str = "u-customer",
                          user_email: str = "kunde@ss-coffee.de"):
    """Insert a machine_request directly for scoping tests."""
    rid = f"M-TEST-{uuid.uuid4().hex[:8]}"
    if not machine_id:
        m = _db.machines.find_one({})
        machine_id = m["id"] if m else "m-unknown"
        machine_name = m["name"] if m else machine_name
        machine_price = float(m.get("price", machine_price)) if m else machine_price
    doc = {
        "id": rid,
        "machineId": machine_id,
        "machineName": machine_name,
        "machinePrice": machine_price,
        "type": req_type,
        "termMonths": 48,
        "message": "TEST",
        "customer": {
            "userId": user_id,
            "companyId": company_id,
            "userName": "Test Kunde",
            "email": user_email,
            "companyName": f"Company {company_id}",
        },
        "status": status,
        "paymentStatus": "Offen",
        "terms": terms,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    _db.machine_requests.insert_one(doc)
    _CREATED["machine_requests"].append(rid)
    return rid


# ==============================================================================
# REGRESSION: B2B login smoke
# ==============================================================================
class TestLoginSmoke:
    def test_admin_login(self, admin_token):
        assert admin_token and isinstance(admin_token, str)

    def test_sales_login(self, sales_token):
        assert sales_token and isinstance(sales_token, str)

    def test_customer_login(self, customer_token):
        assert customer_token and isinstance(customer_token, str)


# ==============================================================================
# SEC: Machines scoping — sales role is company-scoped
# ==============================================================================
class TestMachinesScoping:
    @pytest.fixture(scope="class")
    def seeded(self, base_url):
        # In-scope: c1 (assigned to u-sales). Out-of-scope: c2 (assigned to u-admin).
        in_scope_id = _seed_machine_request(company_id="c1", req_type="leasing")
        out_scope_id = _seed_machine_request(
            company_id="c2", req_type="leasing",
            user_id="u-external", user_email="external@example.com",
        )
        return {"in_scope": in_scope_id, "out_scope": out_scope_id}

    def test_get_machine_requests_sales_scoped(self, base_url, sales_token, seeded):
        r = requests.get(f"{base_url}/api/machine-requests",
                         headers=hdr(sales_token), timeout=30)
        assert r.status_code == 200, r.text
        ids = {row["id"] for row in r.json()}
        assert seeded["in_scope"] in ids, "sales must see in-scope (c1) request"
        assert seeded["out_scope"] not in ids, \
            f"sales must NOT see out-of-scope (c2) request: {seeded['out_scope']}"
        # Also assert no c2 companyIds are leaked in customer snapshots
        for row in r.json():
            assert row.get("customer", {}).get("companyId") != "c2", \
                "PII leak: c2 customer visible to sales"

    def test_get_machine_requests_admin_sees_all(self, base_url, admin_token, seeded):
        r = requests.get(f"{base_url}/api/machine-requests",
                         headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200
        ids = {row["id"] for row in r.json()}
        assert seeded["in_scope"] in ids
        assert seeded["out_scope"] in ids, "admin must see all machine-requests"

    def test_put_terms_sales_out_of_scope_403(self, base_url, sales_token, seeded):
        payload = {"status": "Angebot", "monthlyRate": 99.0, "termMonths": 48,
                   "minCoffeeKgMonth": 10, "productId": "p1", "coffeePricePerKg": 20.0}
        r = requests.put(f"{base_url}/api/machine-requests/{seeded['out_scope']}",
                         json=payload, headers=hdr(sales_token), timeout=30)
        assert r.status_code == 403, f"expected 403 out-of-scope, got {r.status_code}: {r.text}"

    def test_put_terms_sales_in_scope_200(self, base_url, sales_token, seeded):
        payload = {"status": "Angebot", "monthlyRate": 99.0, "termMonths": 48,
                   "minCoffeeKgMonth": 10, "productId": "p1", "coffeePricePerKg": 20.0}
        r = requests.put(f"{base_url}/api/machine-requests/{seeded['in_scope']}",
                         json=payload, headers=hdr(sales_token), timeout=30)
        assert r.status_code == 200, f"expected 200 in-scope, got {r.status_code}: {r.text}"
        # verify persisted
        doc = _db.machine_requests.find_one({"id": seeded["in_scope"]})
        assert doc["status"] == "Angebot"
        assert doc["terms"]["monthlyRate"] == 99.0

    def test_accept_sales_out_of_scope_403(self, base_url, sales_token, seeded):
        # Prepare an out-of-scope request that is in "Angebot" state (admin-set terms)
        _db.machine_requests.update_one(
            {"id": seeded["out_scope"]},
            {"$set": {"status": "Angebot",
                      "terms": {"monthlyRate": 100.0, "termMonths": 48,
                                "minCoffeeKgMonth": 10, "productId": "p1",
                                "coffeePricePerKg": 20.0, "coffeeName": "Gambilongo Espresso Bar"}}},
        )
        r = requests.post(f"{base_url}/api/machine-requests/{seeded['out_scope']}/accept",
                          headers=hdr(sales_token), timeout=30)
        assert r.status_code == 403, f"expected 403 accept out-of-scope, got {r.status_code}"

    def test_leasing_contracts_sales_scoped(self, base_url, sales_token, admin_token):
        # Insert two leasing contracts: one in-scope (c1), one out-of-scope (c2).
        in_id = f"S&S-TEST-M{pysecrets.token_hex(3)}"
        out_id = f"S&S-TEST-M{pysecrets.token_hex(3)}"
        for cid, coid in [(in_id, "c1"), (out_id, "c2")]:
            _db.contracts.insert_one({
                "id": cid, "companyId": coid, "productId": "p1",
                "start": datetime.now(timezone.utc).date().isoformat(),
                "termMonths": 48, "minQtyMonth": 10, "price": 20.0,
                "machine": "Test", "machineRate": 100.0,
                "source": "machine_leasing", "machineRequestId": "TEST",
            })
            _CREATED["contracts"].append(cid)

        r = requests.get(f"{base_url}/api/machines/leasing-contracts",
                         headers=hdr(sales_token), timeout=30)
        assert r.status_code == 200
        ids = {c["id"] for c in r.json()}
        assert in_id in ids
        assert out_id not in ids, "sales leaked contract for company c2"
        for c in r.json():
            assert c.get("companyId") != "c2", "PII leak: c2 contract visible to sales"

        r2 = requests.get(f"{base_url}/api/machines/leasing-contracts",
                          headers=hdr(admin_token), timeout=30)
        assert r2.status_code == 200
        ids2 = {c["id"] for c in r2.json()}
        assert in_id in ids2 and out_id in ids2


# ==============================================================================
# SEC: Machines coffee-price floor enforcement
# ==============================================================================
class TestMachinesFloor:
    def test_below_floor_400(self, base_url, admin_token):
        rid = _seed_machine_request(company_id="c1", req_type="leasing")
        # p1 absoluteFloor = 14.9 — try 10.0
        payload = {"status": "Angebot", "monthlyRate": 100.0, "termMonths": 48,
                   "minCoffeeKgMonth": 10, "productId": "p1", "coffeePricePerKg": 10.0}
        r = requests.put(f"{base_url}/api/machine-requests/{rid}",
                         json=payload, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400, f"expected 400 below floor, got {r.status_code}: {r.text}"
        assert "14" in r.text or "Floor" in r.text or "unter" in r.text.lower() or "nicht unter" in r.text.lower()

    def test_at_or_above_floor_ok(self, base_url, admin_token):
        rid = _seed_machine_request(company_id="c1", req_type="leasing")
        # exactly at floor
        payload = {"status": "Angebot", "monthlyRate": 100.0, "termMonths": 48,
                   "minCoffeeKgMonth": 10, "productId": "p1", "coffeePricePerKg": 14.9}
        r = requests.put(f"{base_url}/api/machine-requests/{rid}",
                         json=payload, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200, f"expected 200 at floor, got {r.status_code}: {r.text}"
        assert r.json()["terms"]["coffeePricePerKg"] == 14.9


# ==============================================================================
# SEC: Newsletter open-redirect fix
# ==============================================================================
class TestNewsletterBaseUrl:
    def test_safe_base_ignores_malicious(self):
        # Direct unit test on the helper — it must NEVER return the client base
        from app.routers import newsletter as nl_mod
        result = nl_mod._safe_base("https://evil.example.com")
        assert "evil.example.com" not in result, \
            f"_safe_base leaked malicious host: {result}"
        assert result == nl_mod.APP_URL.rstrip("/"), \
            f"_safe_base must return server APP_URL only, got {result}"

    def test_subscribe_ignores_malicious_baseurl(self, base_url):
        # Use unique email so we go through the pending-subscribe branch
        email = f"nl_sec_{pysecrets.token_hex(4)}@example.com"
        _CREATED["newsletter_emails"].append(email)

        payload = {"email": email, "name": "SEC Test",
                   "baseUrl": "https://evil.example.com"}
        r = requests.post(f"{base_url}/api/newsletter/subscribe",
                          json=payload, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("pending") is True

        # Inspect DB: no malicious baseUrl persisted on the record
        rec = _db.newsletter.find_one({"email": email})
        assert rec is not None
        assert rec.get("confirmed") is False
        # Ensure no stored trace of the malicious URL
        for v in rec.values():
            if isinstance(v, str):
                assert "evil.example.com" not in v, \
                    f"newsletter record persisted malicious URL: {v}"

    def test_confirm_flow_still_works(self, base_url):
        email = f"nl_ok_{pysecrets.token_hex(4)}@example.com"
        _CREATED["newsletter_emails"].append(email)
        r = requests.post(f"{base_url}/api/newsletter/subscribe",
                          json={"email": email, "baseUrl": "https://evil.example.com"},
                          timeout=30)
        assert r.status_code == 200

        rec = _db.newsletter.find_one({"email": email})
        token = rec["confirmToken"]
        # GET confirm
        r2 = requests.get(f"{base_url}/api/newsletter/confirm",
                          params={"token": token}, timeout=30)
        assert r2.status_code == 200, r2.text
        assert "bestätigt" in r2.text.lower() or "best&auml;tigt" in r2.text.lower()
        # Discount code issued and confirmed
        rec_after = _db.newsletter.find_one({"email": email})
        assert rec_after["confirmed"] is True
        assert rec_after.get("code", "").startswith("SS-")


# ==============================================================================
# REGRESSION: Machines happy path (leasing) end-to-end
# ==============================================================================
class TestMachinesLeasingHappyPath:
    def test_full_flow(self, base_url, admin_token, customer_token):
        # 1) Customer creates leasing request
        machine = _db.machines.find_one({})
        assert machine is not None
        payload = {"machineId": machine["id"], "type": "leasing",
                   "termMonths": 48, "message": "Bitte um Angebot"}
        r = requests.post(f"{base_url}/api/machine-requests", json=payload,
                          headers=hdr(customer_token), timeout=30)
        assert r.status_code == 201, r.text
        req = r.json()
        rid = req["id"]
        _CREATED["machine_requests"].append(rid)
        assert req["status"] == "Angefragt"
        assert req["customer"]["companyId"] == "c1"

        # 2) Admin sets valid terms (product + price >= floor + minCoffeeKg)
        terms = {"status": "Angebot", "downPayment": 500.0, "monthlyRate": 120.0,
                 "termMonths": 48, "minCoffeeKgMonth": 15,
                 "productId": "p1", "coffeePricePerKg": 15.5,
                 "note": "Test-Angebot"}
        r = requests.put(f"{base_url}/api/machine-requests/{rid}",
                         json=terms, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Angebot"

        # 3) Customer accepts
        r = requests.post(f"{base_url}/api/machine-requests/{rid}/accept",
                          headers=hdr(customer_token), timeout=30)
        assert r.status_code == 200, r.text
        accepted = r.json()
        assert accepted["status"] == "Bestätigt"
        contract_id = accepted.get("contractId")
        assert contract_id, "auto coffee-binding contract must be created"
        _CREATED["contracts"].append(contract_id)

        # 4) Contract appears in admin leasing-contracts
        r = requests.get(f"{base_url}/api/machines/leasing-contracts",
                         headers=hdr(admin_token), timeout=30)
        assert r.status_code == 200
        ids = {c["id"] for c in r.json()}
        assert contract_id in ids, f"new contract {contract_id} missing in leasing-contracts"
        contract = next(c for c in r.json() if c["id"] == contract_id)
        assert contract["companyId"] == "c1"
        assert contract["price"] == 15.5
        assert contract["minQtyMonth"] == 15
        assert contract["source"] == "machine_leasing"


class TestMachinesLeasingValidations:
    def test_missing_product_400(self, base_url, admin_token):
        rid = _seed_machine_request(company_id="c1", req_type="leasing")
        payload = {"status": "Angebot", "monthlyRate": 100.0, "termMonths": 48,
                   "minCoffeeKgMonth": 10, "coffeePricePerKg": 20.0}
        r = requests.put(f"{base_url}/api/machine-requests/{rid}",
                         json=payload, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400
        assert "Kaffeesorte" in r.text

    def test_zero_min_coffee_400(self, base_url, admin_token):
        rid = _seed_machine_request(company_id="c1", req_type="leasing")
        payload = {"status": "Angebot", "monthlyRate": 100.0, "termMonths": 48,
                   "minCoffeeKgMonth": 0, "productId": "p1", "coffeePricePerKg": 20.0}
        r = requests.put(f"{base_url}/api/machine-requests/{rid}",
                         json=payload, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400


# ==============================================================================
# REGRESSION: Kauf checkout (Stripe url or 502)
# ==============================================================================
class TestKaufCheckout:
    def test_kauf_checkout_status(self, base_url, customer_token):
        machine = _db.machines.find_one({})
        payload = {"machineId": machine["id"], "type": "kauf",
                   "termMonths": 48, "message": "Kauf"}
        r = requests.post(f"{base_url}/api/machine-requests", json=payload,
                          headers=hdr(customer_token), timeout=30)
        assert r.status_code == 201, r.text
        rid = r.json()["id"]
        _CREATED["machine_requests"].append(rid)
        r = requests.post(f"{base_url}/api/machine-requests/{rid}/checkout",
                          headers=hdr(customer_token), timeout=30)
        # Stripe placeholder key -> 502 acceptable; real key -> 200 with url
        assert r.status_code in (200, 502), f"unexpected {r.status_code}: {r.text}"
        if r.status_code == 200:
            assert r.json().get("url", "").startswith("http")

    def test_checkout_not_owner_403(self, base_url, customer_token, admin_token):
        # Seed a kauf request under a different customer
        rid = _seed_machine_request(company_id="c2", req_type="kauf",
                                    user_id="u-external", user_email="external@example.com")
        # Note: admin bypasses via _owns (returns True for admin)
        # But the customer with company c1 should get 403 for a c2 request
        r = requests.post(f"{base_url}/api/machine-requests/{rid}/checkout",
                          headers=hdr(customer_token), timeout=30)
        assert r.status_code == 403


# ==============================================================================
# REGRESSION: prior security fixes still hold
# ==============================================================================
class TestPriorRegressions:
    def test_shopuser_products_403(self, base_url):
        email = f"reg_shop_{pysecrets.token_hex(3)}@example.com"
        r = requests.post(f"{base_url}/api/shop/register", json={
            "name": "Reg Shop", "email": email, "password": "Secret#2026",
        }, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        _CREATED["users"].append(j["user"]["id"])
        token = j["access_token"]
        r = requests.get(f"{base_url}/api/products", headers=hdr(token), timeout=30)
        assert r.status_code == 403

    def test_customer_orders_ignores_client_price(self, base_url, customer_token):
        payload = {"companyId": "c1",
                   "items": [{"productId": "p1", "qty": 3, "price": 0.01}]}
        r = requests.post(f"{base_url}/api/orders", json=payload,
                          headers=hdr(customer_token), timeout=30)
        assert r.status_code == 200, r.text
        order = r.json()
        _CREATED["orders"].append(order["id"])
        assert order["items"][0]["price"] != 0.01, \
            f"client price leaked: {order['items'][0]['price']}"
        assert order["items"][0]["price"] > 1.0

    def test_shop_order_checkout_requires_token_or_owner(self, base_url):
        payload = {
            "items": [{"productId": "p1", "qty": 1}],
            "customer": {"name": "Reg Guest", "email": "reg_guest@example.com",
                         "street": "S1", "zip": "10115", "city": "Berlin"},
        }
        r = requests.post(f"{base_url}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 200
        data = r.json()
        _CREATED["shop_orders"].append(data["id"])
        # no token -> 403
        r2 = requests.post(f"{base_url}/api/shop/orders/{data['id']}/checkout", timeout=30)
        assert r2.status_code == 403
        # correct token -> 200 or 502 (Stripe preview)
        r3 = requests.post(f"{base_url}/api/shop/orders/{data['id']}/checkout",
                           params={"token": data["token"]}, timeout=30)
        assert r3.status_code in (200, 502)
