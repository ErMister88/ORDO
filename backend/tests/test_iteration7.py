"""Iteration 7 backend tests — 4 new features:
- Feature B: PUT /api/products/{id}/active + GET /api/products returns all (with 'active')
- Feature D: user mgmt (list/create/reset) + password forgot/reset/change
- Regression: sales/customer never see product 'cost'
"""
import hashlib
import pytest
import pymongo
import os
from datetime import datetime, timedelta, timezone


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _db():
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    db_name = os.environ.get("DB_NAME", "ss_b2b_database")
    return pymongo.MongoClient(mongo_url)[db_name]


# ---------- Feature B: product active toggle -------------------------------
class TestProductActive:
    def test_get_products_returns_active_field(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        prods = r.json()
        assert len(prods) >= 4
        for p in prods:
            assert "active" in p, f"product {p.get('id')} missing 'active'"

    def test_admin_can_toggle_active(self, api_client, base_url, admin_token):
        # deactivate p3
        r = api_client.put(f"{base_url}/api/products/p3/active",
                           json={"active": False}, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["active"] is False
        # verify persistence via GET
        g = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        found = [p for p in g.json() if p["id"] == "p3"][0]
        assert found["active"] is False
        # reactivate
        r = api_client.put(f"{base_url}/api/products/p3/active",
                           json={"active": True}, headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["active"] is True

    def test_sales_forbidden(self, api_client, base_url, sales_token):
        r = api_client.put(f"{base_url}/api/products/p1/active",
                           json={"active": False}, headers=hdr(sales_token))
        assert r.status_code == 403

    def test_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.put(f"{base_url}/api/products/p1/active",
                           json={"active": False}, headers=hdr(customer_token))
        assert r.status_code == 403

    def test_404_unknown_product(self, api_client, base_url, admin_token):
        r = api_client.put(f"{base_url}/api/products/pDOESNOTEXIST/active",
                           json={"active": False}, headers=hdr(admin_token))
        assert r.status_code == 404

    def test_inactive_still_in_get_products(self, api_client, base_url, admin_token):
        # deactivate then confirm still returned
        api_client.put(f"{base_url}/api/products/p4/active",
                       json={"active": False}, headers=hdr(admin_token))
        g = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        ids = [p["id"] for p in g.json()]
        assert "p4" in ids  # inactive products still returned
        # cleanup
        api_client.put(f"{base_url}/api/products/p4/active",
                       json={"active": True}, headers=hdr(admin_token))


# ---------- Feature D: user management -------------------------------------
class TestUserManagement:
    def test_list_users_admin(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/users", headers=hdr(admin_token))
        assert r.status_code == 200
        users = r.json()
        emails = [u["email"] for u in users]
        assert "admin@ss-coffee.de" in emails
        assert "vertrieb@ss-coffee.de" in emails
        assert "kunde@ss-coffee.de" in emails
        # No hashed_password leaks
        for u in users:
            assert "hashed_password" not in u

    def test_list_users_sales_forbidden(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/users", headers=hdr(sales_token))
        assert r.status_code == 403

    def test_list_users_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/users", headers=hdr(customer_token))
        assert r.status_code == 403

    def test_create_sales_returns_initial_password(self, api_client, base_url, admin_token):
        body = {"name": "TEST Sales", "email": "test_sales_i7@ss-coffee.de", "role": "sales"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["role"] == "sales"
        assert j["email"] == "test_sales_i7@ss-coffee.de"
        assert "initialPassword" in j and len(j["initialPassword"]) >= 8
        pytest._i7_sales_user = j

        # newly created sales user can log in
        login = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": j["email"], "password": j["initialPassword"]},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert login.status_code == 200, login.text
        assert login.json()["user"]["role"] == "sales"

    def test_create_customer_with_new_company(self, api_client, base_url, admin_token):
        body = {
            "name": "TEST Kunde",
            "email": "test_kunde_i7@ss-coffee.de",
            "role": "customer",
            "newCompany": {"name": "TEST_i7_Company", "city": "Nürnberg", "email": "test@i7co.de"},
        }
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["role"] == "customer"
        assert j["companyId"] is not None
        assert "initialPassword" in j
        pytest._i7_cust_user = j

        # login
        login = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": j["email"], "password": j["initialPassword"]},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert login.status_code == 200, login.text

    def test_create_duplicate_email_returns_409(self, api_client, base_url, admin_token):
        body = {"name": "Dup", "email": "admin@ss-coffee.de", "role": "sales"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 409

    def test_create_customer_without_company_returns_400(self, api_client, base_url, admin_token):
        body = {"name": "NoCo", "email": "test_noco_i7@ss-coffee.de", "role": "customer"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 400

    def test_create_customer_with_existing_company(self, api_client, base_url, admin_token):
        body = {"name": "TEST Cust2", "email": "test_kunde2_i7@ss-coffee.de",
                "role": "customer", "companyId": "c2"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 200
        j = r.json()
        assert j["companyId"] == "c2"
        pytest._i7_cust2_user = j

    def test_create_sales_non_admin_forbidden(self, api_client, base_url, sales_token):
        body = {"name": "X", "email": "should_not_create@ss.de", "role": "sales"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(sales_token))
        assert r.status_code == 403

    def test_admin_reset_password(self, api_client, base_url, admin_token):
        u = getattr(pytest, "_i7_sales_user", None)
        if not u:
            pytest.skip("previous test did not create user")
        r = api_client.post(f"{base_url}/api/users/{u['id']}/reset", headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        j = r.json()
        assert "initialPassword" in j
        # login with new password
        login = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": u["email"], "password": j["initialPassword"]},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert login.status_code == 200

    def test_admin_reset_unknown_user_404(self, api_client, base_url, admin_token):
        r = api_client.post(f"{base_url}/api/users/u-nonexistent/reset", headers=hdr(admin_token))
        assert r.status_code == 404

    def test_admin_reset_forbidden_for_sales(self, api_client, base_url, sales_token):
        r = api_client.post(f"{base_url}/api/users/u-admin/reset", headers=hdr(sales_token))
        assert r.status_code == 403


# ---------- Feature D: password forgot / reset / change --------------------
class TestPasswordFlows:
    def test_forgot_unknown_email_returns_200(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/auth/password/forgot",
                            json={"email": "nobody@nowhere.tld"})
        assert r.status_code == 200
        assert "message" in r.json()

    def test_forgot_known_email_returns_200(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/auth/password/forgot",
                            json={"email": "kunde@ss-coffee.de"})
        assert r.status_code == 200

    def test_reset_with_seeded_code_succeeds(self, api_client, base_url):
        """Since we cannot read the emailed code, insert a known code directly."""
        db = _db()
        user = db.users.find_one({"email": "test_kunde_i7@ss-coffee.de"})
        if not user:
            pytest.skip("cust test user not created")
        code = "123456"
        digest = hashlib.sha256(code.encode()).hexdigest()
        db.password_resets.delete_many({"userId": user["id"]})
        db.password_resets.insert_one({
            "userId": user["id"],
            "codeHash": digest,
            "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=30),
            "used": False,
        })

        # short password → 400
        r = api_client.post(f"{base_url}/api/auth/password/reset",
                            json={"email": user["email"], "code": code, "newPassword": "abc"})
        assert r.status_code == 400

        # success
        r = api_client.post(f"{base_url}/api/auth/password/reset",
                            json={"email": user["email"], "code": code, "newPassword": "NewPass#2026"})
        assert r.status_code == 200, r.text

        # replay same code → 400
        r = api_client.post(f"{base_url}/api/auth/password/reset",
                            json={"email": user["email"], "code": code, "newPassword": "NewPass#2027"})
        assert r.status_code == 400

        # login with new password
        login = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": user["email"], "password": "NewPass#2026"},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert login.status_code == 200

    def test_reset_wrong_code_returns_400(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/auth/password/reset",
                            json={"email": "admin@ss-coffee.de",
                                  "code": "000000", "newPassword": "SomePass#2026"})
        assert r.status_code == 400

    def test_change_password_wrong_current(self, api_client, base_url, customer_token):
        r = api_client.post(f"{base_url}/api/auth/password/change",
                            json={"currentPassword": "WrongOldPw#2026",
                                  "newPassword": "NewValid#2026"},
                            headers=hdr(customer_token))
        assert r.status_code == 400

    def test_change_password_short_new(self, api_client, base_url, customer_token):
        r = api_client.post(f"{base_url}/api/auth/password/change",
                            json={"currentPassword": "Kunde#2026", "newPassword": "short"},
                            headers=hdr(customer_token))
        assert r.status_code == 400

    def test_change_password_unauth_401(self, api_client, base_url):
        r = api_client.post(f"{base_url}/api/auth/password/change",
                            json={"currentPassword": "x", "newPassword": "abcdefgh"})
        assert r.status_code == 401


# ---------- Regression: cost hiding for sales/customer --------------------
class TestCostHiding:
    def test_sales_no_cost(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(sales_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"sales sees cost on {p['id']}"

    def test_customer_no_cost(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(customer_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p

    def test_admin_sees_cost(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" in p


# ---------- Cleanup --------------------------------------------------------
class TestCleanup:
    def test_zzz_cleanup(self, api_client, base_url, admin_token):
        db = _db()
        # delete test users
        for email in ("test_sales_i7@ss-coffee.de", "test_kunde_i7@ss-coffee.de",
                      "test_kunde2_i7@ss-coffee.de"):
            db.users.delete_one({"email": email})
        # delete test company
        db.companies.delete_many({"name": "TEST_i7_Company"})
        # cleanup password_resets
        db.password_resets.delete_many({})
        # ensure products p1..p4 are active
        for pid in ("p1", "p2", "p3", "p4"):
            db.products.update_one({"id": pid}, {"$set": {"active": True}})
        print("iter7 cleanup complete")
