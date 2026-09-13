"""Iteration 18 - Leasing offer requires coffee product/price/min-kg; decline/question; leasing-contracts overview.

Tests:
1. PUT /api/machine-requests/{id} for LEASING with status 'Angebot':
   - 400 without minCoffeeKgMonth (or <=0)
   - 400 without productId
   - 400 without coffeePricePerKg (or <=0)
   - 200 with all three → terms include productId, coffeePricePerKg, coffeeName
   - Finanzierung offer does NOT require coffee fields
2. Accept leasing offer → contract has productId & price = coffeePricePerKg & minQtyMonth
3. POST /respond decline/question sets status; invalid action 400; non-owner customer 403
4. GET /api/machines/leasing-contracts - admin/sales only, customer 403, enriched rows
"""
import os
import time
import pytest
import pymongo


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


_start_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
_created_requests: list[str] = []
_created_contract_ids: list[str] = []


def _mongo():
    url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
    dbn = os.environ.get("DB_NAME") or "ss_b2b_database"
    cli = pymongo.MongoClient(url)
    return cli, cli[dbn]


def _seed_machine(api_client, base_url, tok):
    lst = api_client.get(f"{base_url}/api/machines", headers=hdr(tok)).json()
    for m in lst:
        if m.get("active") and not m["name"].startswith("TEST_"):
            return m
    return lst[0]


def _first_product(api_client, base_url, tok):
    r = api_client.get(f"{base_url}/api/products", headers=hdr(tok))
    assert r.status_code == 200, r.text
    prods = r.json()
    assert len(prods) >= 1
    return prods[0]


def _make_request(api_client, base_url, customer_token, mtype: str, term: int = 48):
    m = _seed_machine(api_client, base_url, customer_token)
    r = api_client.post(f"{base_url}/api/machine-requests",
                        json={"machineId": m["id"], "type": mtype, "termMonths": term},
                        headers=hdr(customer_token))
    assert r.status_code == 201, r.text
    req = r.json()
    _created_requests.append(req["id"])
    return req


# ---------- Leasing offer validation ----------
class TestLeasingOfferRequiresCoffee:
    def test_leasing_missing_minkg_returns_400(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 48,
                                 "productId": prod["id"], "coffeePricePerKg": 22.5},
                           headers=hdr(admin_token))
        assert r.status_code == 400, r.text
        assert "Mindestabnahme" in r.text or "Kaffee" in r.text

    def test_leasing_minkg_zero_returns_400(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 48, "minCoffeeKgMonth": 0,
                                 "productId": prod["id"], "coffeePricePerKg": 22.5},
                           headers=hdr(admin_token))
        assert r.status_code == 400, r.text

    def test_leasing_missing_product_returns_400(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 48,
                                 "minCoffeeKgMonth": 8, "coffeePricePerKg": 22.5},
                           headers=hdr(admin_token))
        assert r.status_code == 400, r.text
        assert "Kaffeesorte" in r.text or "product" in r.text.lower()

    def test_leasing_missing_price_returns_400(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 48,
                                 "minCoffeeKgMonth": 8, "productId": prod["id"]},
                           headers=hdr(admin_token))
        assert r.status_code == 400, r.text
        assert "Kaffeepreis" in r.text or "price" in r.text.lower()

    def test_leasing_price_zero_returns_400(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 48,
                                 "minCoffeeKgMonth": 8, "productId": prod["id"],
                                 "coffeePricePerKg": 0},
                           headers=hdr(admin_token))
        assert r.status_code == 400, r.text

    def test_leasing_full_valid_terms_returns_200(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing", term=36)
        prod = _first_product(api_client, base_url, admin_token)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"monthlyRate": 149.0, "termMonths": 36,
                                 "minCoffeeKgMonth": 8, "productId": prod["id"],
                                 "coffeePricePerKg": 22.5, "note": "TEST offer"},
                           headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "Angebot"
        t = data.get("terms") or {}
        assert t.get("productId") == prod["id"]
        assert t.get("coffeePricePerKg") == 22.5
        assert t.get("minCoffeeKgMonth") == 8
        expected_name = f"{prod.get('brand', '')} {prod.get('name', '')}".strip()
        assert t.get("coffeeName") == expected_name, f"coffeeName mismatch: {t.get('coffeeName')} vs {expected_name}"

    def test_finanzierung_offer_does_not_require_coffee(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "finanzierung", term=36)
        r = api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                           json={"downPayment": 500, "monthlyRate": 200, "finalPayment": 800,
                                 "termMonths": 36},
                           headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Angebot"


# ---------- Accept creates full contract ----------
class TestLeasingAcceptContractFields:
    def test_accept_contract_uses_product_and_price(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing", term=36)
        prod = _first_product(api_client, base_url, admin_token)
        api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                       json={"monthlyRate": 149.0, "termMonths": 36,
                             "minCoffeeKgMonth": 10, "productId": prod["id"],
                             "coffeePricePerKg": 22.5},
                       headers=hdr(admin_token))
        acc = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200, acc.text
        cid = acc.json().get("contractId")
        assert cid
        _created_contract_ids.append(cid)

        cli, db = _mongo()
        try:
            c = db.contracts.find_one({"id": cid})
            assert c is not None
            assert c.get("productId") == prod["id"]
            assert c.get("price") == 22.5
            assert c.get("minQtyMonth") == 10
            assert c.get("source") == "machine_leasing"
        finally:
            cli.close()


# ---------- Respond: decline / question ----------
class TestRespondDeclineQuestion:
    def test_decline_sets_status_abgelehnt(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                       json={"monthlyRate": 149.0, "termMonths": 48,
                             "minCoffeeKgMonth": 8, "productId": prod["id"],
                             "coffeePricePerKg": 22.5},
                       headers=hdr(admin_token))
        r = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/respond",
                            json={"action": "decline"}, headers=hdr(customer_token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Abgelehnt"

    def test_question_sets_status_and_appends(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        prod = _first_product(api_client, base_url, admin_token)
        api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                       json={"monthlyRate": 149.0, "termMonths": 48,
                             "minCoffeeKgMonth": 8, "productId": prod["id"],
                             "coffeePricePerKg": 22.5},
                       headers=hdr(admin_token))
        r = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/respond",
                            json={"action": "question", "message": "Ist die Maschine gebraucht?"},
                            headers=hdr(customer_token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["status"] == "Rückfrage"
        qs = data.get("questions") or []
        assert len(qs) >= 1
        assert qs[-1]["message"] == "Ist die Maschine gebraucht?"

        # Second question appends
        r2 = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/respond",
                             json={"action": "question", "message": "Und die Garantie?"},
                             headers=hdr(customer_token))
        assert r2.status_code == 200
        assert len(r2.json().get("questions") or []) >= 2

    def test_invalid_action_returns_400(self, api_client, base_url, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing")
        r = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/respond",
                            json={"action": "explode"}, headers=hdr(customer_token))
        assert r.status_code == 400, r.text

    def test_non_owner_customer_gets_403(self, api_client, base_url, admin_token, customer_token):
        # Create req as customer (company c1), then try to respond as a different customer
        req = _make_request(api_client, base_url, customer_token, "leasing")

        # Try as vertrieb (staff role sales) - should be allowed since staff sees all
        # So instead create a TEST_ customer in a different company
        cli, db = _mongo()
        try:
            from datetime import datetime, timezone
            import uuid as _uuid
            try:
                from passlib.hash import bcrypt as _bcrypt
                pw = _bcrypt.hash("Test#2026")
            except Exception:
                pytest.skip("passlib/bcrypt not available")
            # find a different existing company id (not c1)
            other_co = db.companies.find_one({"id": {"$ne": "c1"}})
            if not other_co:
                pytest.skip("no alternate company available")
            uid = f"TEST_u_{_uuid.uuid4().hex[:8]}"
            email = f"TEST_other_{_uuid.uuid4().hex[:6]}@ss-coffee.de"
            db.users.insert_one({
                "id": uid, "email": email, "name": "TEST Other",
                "role": "customer", "companyId": other_co["id"],
                "passwordHash": pw, "active": True,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            })
            r = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": email, "password": "Test#2026"},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code != 200:
                db.users.delete_one({"id": uid})
                pytest.skip(f"could not login TEST_ user: {r.status_code}")
            tok = r.json()["access_token"]

            resp = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/respond",
                                   json={"action": "decline"}, headers=hdr(tok))
            db.users.delete_one({"id": uid})
            assert resp.status_code == 403, f"expected 403, got {resp.status_code}: {resp.text}"
        finally:
            cli.close()


# ---------- Leasing contracts overview ----------
class TestLeasingContractsOverview:
    def test_admin_gets_enriched_rows(self, api_client, base_url, admin_token, customer_token):
        # Ensure at least one leasing contract exists
        req = _make_request(api_client, base_url, customer_token, "leasing", term=24)
        prod = _first_product(api_client, base_url, admin_token)
        api_client.put(f"{base_url}/api/machine-requests/{req['id']}",
                       json={"monthlyRate": 89.0, "termMonths": 24,
                             "minCoffeeKgMonth": 7, "productId": prod["id"],
                             "coffeePricePerKg": 21.0},
                       headers=hdr(customer_token if False else admin_token))
        acc = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200
        cid = acc.json()["contractId"]
        _created_contract_ids.append(cid)

        r = api_client.get(f"{base_url}/api/machines/leasing-contracts", headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        found = next((c for c in rows if c.get("id") == cid), None)
        assert found is not None, f"contract {cid} missing in leasing-contracts overview"
        assert found.get("companyName"), "companyName missing"
        assert found.get("productName"), "productName missing"
        assert found.get("source") == "machine_leasing"
        # No _id leak
        for c in rows:
            assert "_id" not in c

    def test_sales_can_access(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/machines/leasing-contracts", headers=hdr(sales_token))
        assert r.status_code == 200

    def test_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/machines/leasing-contracts", headers=hdr(customer_token))
        assert r.status_code == 403


# ---------- Cleanup ----------
class TestZCleanup:
    def test_cleanup(self, api_client, base_url, admin_token):
        cli, db = _mongo()
        try:
            # Delete requests created during this run
            n_reqs = 0
            if _created_requests:
                n_reqs = db.machine_requests.delete_many({"id": {"$in": _created_requests}}).deleted_count
            # Also cleanup any created since start by customer / TEST_ users
            n_reqs += db.machine_requests.delete_many(
                {"customer.email": {"$regex": "^TEST_"}}
            ).deleted_count
            n_c = 0
            if _created_contract_ids:
                n_c = db.contracts.delete_many(
                    {"id": {"$in": _created_contract_ids}, "source": "machine_leasing"}
                ).deleted_count
            # Also remove any TEST_ users left behind
            db.users.delete_many({"email": {"$regex": "^TEST_"}})
            print(f"Cleanup: {n_reqs} requests, {n_c} machine_leasing contracts")
        finally:
            cli.close()
        assert True
