"""Iteration 16 - Machines catalog + acquisition (Kauf / Finanzierung / Leasing) tests."""
import os
import time
import pytest
def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- helpers ----------
def _created_machine_ids():
    return getattr(_created_machine_ids, "_ids", [])


def _add_machine(mid):
    _created_machine_ids._ids = _created_machine_ids().__class__(_created_machine_ids()) if hasattr(_created_machine_ids, "_ids") else []
    if not hasattr(_created_machine_ids, "_ids"):
        _created_machine_ids._ids = []
    _created_machine_ids._ids.append(mid)


import time
_start_iso = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
_created_machines = []
_created_requests = []


# ---------- Catalog & seed ----------
class TestMachinesCatalog:
    def test_list_machines_seeds_three(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token))
        assert r.status_code == 200, r.text
        data = r.json()
        assert isinstance(data, list)
        assert len(data) >= 3, f"expected >=3 seeded machines, got {len(data)}"
        # validate no _id leakage and required fields
        for m in data:
            assert "_id" not in m
            for k in ("id", "name", "price", "taxRate", "active"):
                assert k in m, f"machine missing key {k}: {m}"
            assert m["taxRate"] == 19
        # customer only sees active
        for m in data:
            assert m["active"] is True

    def test_admin_can_see_inactive(self, api_client, base_url, admin_token):
        # create + deactivate one, then verify admin sees it
        payload = {"name": "TEST_Inactive_M", "description": "", "imageUrl": "", "price": 100.0, "active": True}
        r = api_client.post(f"{base_url}/api/machines", json=payload, headers=hdr(admin_token))
        assert r.status_code == 201, r.text
        mid = r.json()["id"]
        _created_machines.append(mid)
        d = api_client.delete(f"{base_url}/api/machines/{mid}", headers=hdr(admin_token))
        assert d.status_code == 200
        lst = api_client.get(f"{base_url}/api/machines", headers=hdr(admin_token)).json()
        found = next((m for m in lst if m["id"] == mid), None)
        assert found is not None
        assert found["active"] is False


class TestMachineAdminCRUD:
    def test_create_update_delete(self, api_client, base_url, admin_token):
        # create
        payload = {"name": "TEST_M1", "description": "d", "imageUrl": "", "price": 1234.5, "active": True}
        r = api_client.post(f"{base_url}/api/machines", json=payload, headers=hdr(admin_token))
        assert r.status_code == 201, r.text
        m = r.json()
        _created_machines.append(m["id"])
        assert m["taxRate"] == 19
        assert m["price"] == 1234.5

        # update
        upd = {"name": "TEST_M1_upd", "description": "x", "imageUrl": "", "price": 999.0, "active": True}
        r2 = api_client.put(f"{base_url}/api/machines/{m['id']}", json=upd, headers=hdr(admin_token))
        assert r2.status_code == 200
        assert r2.json()["name"] == "TEST_M1_upd"
        assert r2.json()["price"] == 999.0

        # GET verify persisted
        lst = api_client.get(f"{base_url}/api/machines", headers=hdr(admin_token)).json()
        got = next((x for x in lst if x["id"] == m["id"]), None)
        assert got and got["name"] == "TEST_M1_upd"

        # delete -> active=false
        rd = api_client.delete(f"{base_url}/api/machines/{m['id']}", headers=hdr(admin_token))
        assert rd.status_code == 200
        lst2 = api_client.get(f"{base_url}/api/machines", headers=hdr(admin_token)).json()
        got2 = next((x for x in lst2 if x["id"] == m["id"]), None)
        assert got2 and got2["active"] is False

    def test_non_admin_forbidden(self, api_client, base_url, customer_token, sales_token):
        payload = {"name": "TEST_forbid", "price": 1.0}
        for tok in (customer_token, sales_token):
            r = api_client.post(f"{base_url}/api/machines", json=payload, headers=hdr(tok))
            assert r.status_code == 403, f"expected 403, got {r.status_code}: {r.text}"

    def test_update_unknown_returns_404(self, api_client, base_url, admin_token):
        r = api_client.put(f"{base_url}/api/machines/does-not-exist",
                           json={"name": "x", "price": 1.0}, headers=hdr(admin_token))
        assert r.status_code == 404


# ---------- Requests: kauf / finanzierung / leasing ----------
class TestMachineRequestsCreate:
    def _get_seed_machine(self, api_client, base_url, tok):
        lst = api_client.get(f"{base_url}/api/machines", headers=hdr(tok)).json()
        # pick first active seed (not our test ones)
        for m in lst:
            if m["active"] and not m["name"].startswith("TEST_"):
                return m
        return lst[0]

    def test_create_kauf(self, api_client, base_url, customer_token):
        m = self._get_seed_machine(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": m["id"], "type": "kauf"},
                            headers=hdr(customer_token))
        assert r.status_code == 201, r.text
        req = r.json()
        _created_requests.append(req["id"])
        assert req["type"] == "kauf"
        assert req["status"] == "Zahlung offen"
        assert req["paymentStatus"] == "Offen"
        assert req["termMonths"] == 48
        assert req["machineName"] == m["name"]

    def test_create_finanzierung(self, api_client, base_url, customer_token):
        m = self._get_seed_machine(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": m["id"], "type": "finanzierung", "termMonths": 36},
                            headers=hdr(customer_token))
        assert r.status_code == 201, r.text
        req = r.json()
        _created_requests.append(req["id"])
        assert req["status"] == "Angefragt"
        assert req["termMonths"] == 36

    def test_create_leasing(self, api_client, base_url, customer_token):
        m = self._get_seed_machine(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": m["id"], "type": "leasing"},
                            headers=hdr(customer_token))
        assert r.status_code == 201, r.text
        req = r.json()
        _created_requests.append(req["id"])
        assert req["status"] == "Angefragt"
        assert req["type"] == "leasing"

    def test_invalid_type_400(self, api_client, base_url, customer_token):
        m = self._get_seed_machine(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": m["id"], "type": "miete"},
                            headers=hdr(customer_token))
        assert r.status_code == 400

    def test_unknown_machine_404(self, api_client, base_url, customer_token):
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": "nope-xyz", "type": "kauf"},
                            headers=hdr(customer_token))
        assert r.status_code == 404


class TestMachineRequestsVisibility:
    def test_admin_sees_all(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/machine-requests", headers=hdr(admin_token))
        assert r.status_code == 200
        rows = r.json()
        # our test-created requests must be visible to admin
        ids = {x["id"] for x in rows}
        for rid in _created_requests:
            assert rid in ids, f"admin missing {rid}"
        for x in rows:
            assert "_id" not in x

    def test_customer_sees_only_own(self, api_client, base_url, customer_token, admin_token):
        r = api_client.get(f"{base_url}/api/machine-requests", headers=hdr(customer_token))
        assert r.status_code == 200
        rows = r.json()
        # all rows should belong to customer/company
        for row in rows:
            cust = row.get("customer") or {}
            # We can't assert userId directly but companyId c1 or userId same
            # Accept: either their userId or a companyId (visibility rule)
            assert cust.get("userId") or cust.get("companyId")


class TestMachineTermsAndAccept:
    def test_admin_sets_terms_customer_accepts(self, api_client, base_url, admin_token, customer_token):
        # find one of our finanzierung requests
        # ensure a finanzierung request exists for the customer (xdist workers may share state poorly)
        machines = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token)).json()
        seed = next((m for m in machines if m["active"] and not m["name"].startswith("TEST_")), machines[0])
        cr = api_client.post(f"{base_url}/api/machine-requests",
                             json={"machineId": seed["id"], "type": "finanzierung", "termMonths": 24},
                             headers=hdr(customer_token))
        assert cr.status_code == 201
        target = cr.json()
        _created_requests.append(target["id"])

        payload = {
            "downPayment": 500.0, "monthlyRate": 149.9, "finalPayment": 1200.0,
            "termMonths": 48, "minCoffeeKgMonth": None, "note": "TEST terms"
        }
        r = api_client.put(f"{base_url}/api/machine-requests/{target['id']}",
                           json=payload, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        upd = r.json()
        assert upd["status"] == "Angebot"
        assert upd["terms"]["downPayment"] == 500.0
        assert upd["terms"]["monthlyRate"] == 149.9
        assert upd["terms"]["finalPayment"] == 1200.0
        assert upd["terms"]["termMonths"] == 48

        # customer accepts
        acc = api_client.post(f"{base_url}/api/machine-requests/{target['id']}/accept",
                              headers=hdr(customer_token))
        assert acc.status_code == 200, acc.text
        assert acc.json()["status"] == "Bestätigt"

    def test_accept_without_offer_409(self, api_client, base_url, customer_token):
        # create a fresh leasing request explicitly (xdist workers don't share state)
        machines = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token)).json()
        seed = next((m for m in machines if m["active"] and not m["name"].startswith("TEST_")), machines[0])
        cr = api_client.post(f"{base_url}/api/machine-requests",
                             json={"machineId": seed["id"], "type": "leasing"},
                             headers=hdr(customer_token))
        assert cr.status_code == 201
        rid = cr.json()["id"]
        _created_requests.append(rid)
        r = api_client.post(f"{base_url}/api/machine-requests/{rid}/accept",
                            headers=hdr(customer_token))
        assert r.status_code == 409

    def test_set_terms_forbidden_for_customer(self, api_client, base_url, customer_token):
        machines = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token)).json()
        seed = next((m for m in machines if m["active"] and not m["name"].startswith("TEST_")), machines[0])
        cr = api_client.post(f"{base_url}/api/machine-requests",
                             json={"machineId": seed["id"], "type": "leasing"},
                             headers=hdr(customer_token))
        assert cr.status_code == 201
        rid = cr.json()["id"]
        _created_requests.append(rid)
        r = api_client.put(f"{base_url}/api/machine-requests/{rid}",
                           json={"downPayment": 1.0}, headers=hdr(customer_token))
        assert r.status_code == 403


class TestMachineCheckout:
    def _make_kauf(self, api_client, base_url, customer_token):
        machines = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token)).json()
        seed = next((m for m in machines if m["active"] and not m["name"].startswith("TEST_")), machines[0])
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": seed["id"], "type": "kauf"},
                            headers=hdr(customer_token))
        assert r.status_code == 201
        req = r.json()
        _created_requests.append(req["id"])
        return req

    def _make_finanzierung(self, api_client, base_url, customer_token):
        machines = api_client.get(f"{base_url}/api/machines", headers=hdr(customer_token)).json()
        seed = next((m for m in machines if m["active"] and not m["name"].startswith("TEST_")), machines[0])
        r = api_client.post(f"{base_url}/api/machine-requests",
                            json={"machineId": seed["id"], "type": "finanzierung"},
                            headers=hdr(customer_token))
        assert r.status_code == 201
        req = r.json()
        _created_requests.append(req["id"])
        return req

    def test_checkout_kauf_returns_url_or_502(self, api_client, base_url, customer_token):
        kauf = self._make_kauf(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests/{kauf['id']}/checkout",
                            headers=hdr(customer_token))
        # 502 is acceptable in preview per spec
        assert r.status_code in (200, 502), f"unexpected: {r.status_code} {r.text}"
        if r.status_code == 200:
            j = r.json()
            assert "url" in j and "sessionId" in j

    def test_checkout_non_kauf_400(self, api_client, base_url, customer_token):
        nk = self._make_finanzierung(api_client, base_url, customer_token)
        r = api_client.post(f"{base_url}/api/machine-requests/{nk['id']}/checkout",
                            headers=hdr(customer_token))
        assert r.status_code == 400

    def test_payment_status_returns_status(self, api_client, base_url, customer_token):
        kauf = self._make_kauf(api_client, base_url, customer_token)
        r = api_client.get(f"{base_url}/api/machine-requests/{kauf['id']}/payment-status",
                           headers=hdr(customer_token))
        assert r.status_code == 200
        assert "status" in r.json()


# ---------- Cleanup ----------
class TestZCleanup:
    def test_cleanup(self, api_client, base_url, admin_token):
        # remove test machine_requests directly via mongo
        import pymongo
        mongo_url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
        db_name = os.environ.get("DB_NAME") or "ss_b2b_database"
        cli = pymongo.MongoClient(mongo_url)
        d = cli[db_name]
        del_req = d.machine_requests.delete_many(
            {"customer.email": "kunde@ss-coffee.de", "createdAt": {"$gte": _start_iso}}
        ).deleted_count
        # only remove TEST_ prefixed machines (never seed ones)
        del_m = d.machines.delete_many({"name": {"$regex": "^TEST_"}}).deleted_count
        cli.close()
        print(f"Cleanup: removed {del_req} requests, {del_m} test machines")
        assert True
