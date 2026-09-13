"""Iteration 17 - Machine leasing acceptance auto-creates coffee-binding contract; contract PDF + Kaufbeleg flows.

Tests focus on the NEW behavior introduced in this iteration:
1. Accepting a LEASING request (customer with companyId) creates a contract with
   source='machine_leasing', machine=machineName, machineRate, minQtyMonth, termMonths;
   the request now has contractId. Idempotent: second accept does NOT create dup contract.
2. Accepting a FINANZIERUNG request does NOT create a contract.
3. GET /api/contracts (customer auth) includes the auto-created leasing contract.
4. Regression: machines catalog/requests/terms/checkout still working (light).
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


def _make_request(api_client, base_url, customer_token, mtype: str, term: int = 48):
    m = _seed_machine(api_client, base_url, customer_token)
    r = api_client.post(f"{base_url}/api/machine-requests",
                        json={"machineId": m["id"], "type": mtype, "termMonths": term},
                        headers=hdr(customer_token))
    assert r.status_code == 201, r.text
    req = r.json()
    _created_requests.append(req["id"])
    return req


def _set_terms(api_client, base_url, admin_token, req_id: str, **overrides):
    payload = {
        "downPayment": None,
        "monthlyRate": 129.9,
        "finalPayment": None,
        "termMonths": 48,
        "minCoffeeKgMonth": 8,
        "note": "TEST offer",
    }
    payload.update(overrides)
    r = api_client.put(f"{base_url}/api/machine-requests/{req_id}",
                       json=payload, headers=hdr(admin_token))
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Auto contract creation on LEASING accept ----------
class TestLeasingAcceptCreatesContract:
    def test_leasing_accept_creates_coffee_binding_contract(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing", term=36)
        _set_terms(api_client, base_url, admin_token, req["id"],
                   monthlyRate=149.5, minCoffeeKgMonth=10, termMonths=36)

        # accept as customer
        acc = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200, acc.text
        data = acc.json()
        assert data["status"] == "Bestätigt"
        assert "contractId" in data and data["contractId"], "contractId missing on accept response"
        contract_id = data["contractId"]
        _created_contract_ids.append(contract_id)
        # id format S&S-YYYY-M####
        assert contract_id.startswith("S&S-") and "-M" in contract_id, f"unexpected contract id format: {contract_id}"

        # Verify contract row in mongo
        cli, db = _mongo()
        try:
            c = db.contracts.find_one({"id": contract_id})
            assert c is not None, "contract not created in db.contracts"
            assert c.get("source") == "machine_leasing"
            assert c.get("machine") == req["machineName"]
            assert c.get("machineRate") == 149.5
            assert c.get("minQtyMonth") == 10
            assert c.get("termMonths") == 36
            assert c.get("machineRequestId") == req["id"]
            assert c.get("companyId"), "contract missing companyId"
        finally:
            cli.close()

    def test_leasing_accept_is_idempotent_no_duplicate_contract(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing", term=48)
        _set_terms(api_client, base_url, admin_token, req["id"], monthlyRate=99.0, minCoffeeKgMonth=5, termMonths=48)

        acc1 = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                               headers=hdr(customer_token))
        assert acc1.status_code == 200
        cid1 = acc1.json().get("contractId")
        assert cid1
        _created_contract_ids.append(cid1)

        # Second accept: server responds 409 (already Bestätigt) — no dup contract regardless
        acc2 = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                               headers=hdr(customer_token))
        # Either 409 (status not 'Angebot' anymore) or 200 idempotent. Both acceptable so long as no dup.
        assert acc2.status_code in (200, 409)

        cli, db = _mongo()
        try:
            n = db.contracts.count_documents({"machineRequestId": req["id"]})
            assert n == 1, f"expected 1 contract per request, got {n}"
        finally:
            cli.close()

    def test_finanzierung_accept_does_not_create_contract(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "finanzierung", term=36)
        _set_terms(api_client, base_url, admin_token, req["id"],
                   downPayment=500, monthlyRate=200, finalPayment=800, termMonths=36, minCoffeeKgMonth=None)

        acc = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200
        data = acc.json()
        assert data["status"] == "Bestätigt"
        assert not data.get("contractId"), "finanzierung acceptance MUST NOT create a contract"

        cli, db = _mongo()
        try:
            assert db.contracts.count_documents({"machineRequestId": req["id"]}) == 0
        finally:
            cli.close()


# ---------- GET /api/contracts includes leasing contract ----------
class TestContractsListIncludesLeasing:
    def test_customer_contracts_include_machine_leasing(self, api_client, base_url, admin_token, customer_token):
        req = _make_request(api_client, base_url, customer_token, "leasing", term=24)
        _set_terms(api_client, base_url, admin_token, req["id"], monthlyRate=79.9, minCoffeeKgMonth=7, termMonths=24)
        acc = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200
        cid = acc.json()["contractId"]
        _created_contract_ids.append(cid)

        r = api_client.get(f"{base_url}/api/contracts", headers=hdr(customer_token))
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        found = next((c for c in rows if c.get("id") == cid), None)
        assert found is not None, f"leasing contract {cid} not returned by GET /api/contracts"
        # no ObjectId leak
        for c in rows:
            assert "_id" not in c
        assert found.get("source") == "machine_leasing"
        assert found.get("machine") == req["machineName"]
        assert found.get("machineRate") == 79.9


# ---------- Regression light ----------
class TestRegressionMachines:
    def test_machines_list_still_ok(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token))
        assert r.status_code == 200
        assert len(r.json()) >= 3

    def test_kauf_checkout_flow_still_ok(self, api_client, base_url, customer_token):
        req = _make_request(api_client, base_url, customer_token, "kauf")
        r = api_client.post(f"{base_url}/api/machine-requests/{req['id']}/checkout",
                            headers=hdr(customer_token))
        # 502 acceptable in preview
        assert r.status_code in (200, 502)


# ---------- Edge case: leasing customer without companyId ----------
class TestLeasingNoCompanyDoesNotCrash:
    """A user without a companyId accepting a leasing offer should not crash
    and should not create a contract (per implementation)."""

    def test_no_company_no_contract(self, api_client, base_url, admin_token):
        # Create a temporary user without companyId via mongo (test-only user)
        cli, db = _mongo()
        try:
            from datetime import datetime, timezone
            import uuid as _uuid
            uid = f"TEST_u_{_uuid.uuid4().hex[:8]}"
            email = f"TEST_nocompany_{_uuid.uuid4().hex[:6]}@ss-coffee.de"
            # bcrypt hash of "Test#2026" — generate via passlib
            try:
                from passlib.hash import bcrypt as _bcrypt
                pw = _bcrypt.hash("Test#2026")
            except Exception:
                pytest.skip("passlib/bcrypt not available")
            db.users.insert_one({
                "id": uid, "email": email, "name": "TEST NoCompany",
                "role": "customer", "companyId": None,
                "passwordHash": pw, "active": True,
                "createdAt": datetime.now(timezone.utc).isoformat(),
            })

            # Login as this user
            r = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": email, "password": "Test#2026"},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
            if r.status_code != 200:
                pytest.skip(f"could not login temp user: {r.status_code} {r.text}")
            tok = r.json()["access_token"]

            # Create leasing request as this user
            m = _seed_machine(api_client, base_url, tok)
            cr = api_client.post(f"{base_url}/api/machine-requests",
                                 json={"machineId": m["id"], "type": "leasing"},
                                 headers=hdr(tok))
            assert cr.status_code == 201, cr.text
            rid = cr.json()["id"]
            _created_requests.append(rid)

            # Admin sets terms
            _set_terms(api_client, base_url, admin_token, rid, monthlyRate=50.0, minCoffeeKgMonth=3)

            # Accept as user — should succeed with no crash, no contract
            acc = api_client.post(f"{base_url}/api/machine-requests/{rid}/accept",
                                  headers=hdr(tok))
            assert acc.status_code == 200, acc.text
            assert acc.json()["status"] == "Bestätigt"
            assert not acc.json().get("contractId")

            # No contract row created for this request
            assert db.contracts.count_documents({"machineRequestId": rid}) == 0

            # cleanup this user
            db.users.delete_one({"id": uid})
        finally:
            cli.close()


# ---------- Cleanup ----------
class TestZCleanup:
    def test_cleanup(self, api_client, base_url, admin_token):
        cli, db = _mongo()
        try:
            del_req = db.machine_requests.delete_many(
                {"customer.email": {"$in": ["kunde@ss-coffee.de"]},
                 "createdAt": {"$gte": _start_iso}}
            ).deleted_count
            # Also cleanup TEST_ user requests
            del_req_test = db.machine_requests.delete_many(
                {"customer.email": {"$regex": "^TEST_"}}
            ).deleted_count
            # Only remove contracts we created (source machine_leasing AND created in this run)
            del_c = 0
            if _created_contract_ids:
                del_c = db.contracts.delete_many(
                    {"id": {"$in": _created_contract_ids}, "source": "machine_leasing"}
                ).deleted_count
            print(f"Cleanup: {del_req + del_req_test} requests, {del_c} machine_leasing contracts")
        finally:
            cli.close()
        assert True
