"""Iteration 8 backend tests — refactor regression + Phase1..Phase4 features.

Covers:
 - Refactor regression (49 routes, /api/auth/me now returns must_change_password,
   cost hidden for sales/customer, present for admin)
 - Phase1: PUT /api/companies/{id} (admin only)
 - Phase1: must_change_password + login rate-limit (429 after 5 wrong pw)
 - Phase2: POST /api/orders/{id}/invoice (VAT), GET collective-invoice
 - Phase3: subscriptions (list/create/toggle/delete/run)
 - Phase4: Stripe checkout endpoint (auth, visibility, 409 on already paid)
   Note: preview STRIPE_API_KEY is placeholder → 502 on happy path is EXPECTED
"""
import os
import pymongo
import pytest


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _db():
    return pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))[
        os.environ.get("DB_NAME", "ss_b2b_database")
    ]


# ---------------- REFACTOR REGRESSION ----------------
class TestRefactorRegression:
    def test_login_admin(self, admin_token):
        assert admin_token

    def test_me_includes_must_change_password_admin(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/auth/me", headers=hdr(admin_token))
        assert r.status_code == 200
        j = r.json()
        assert "must_change_password" in j
        assert j["role"] == "admin"
        assert j["email"] == "admin@ss-coffee.de"

    def test_products_admin_has_cost(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        prods = r.json()
        assert len(prods) >= 4
        for p in prods:
            assert "cost" in p, f"admin missing cost on {p['id']}"
            assert "taxRate" in p
            assert "active" in p

    def test_products_sales_no_cost(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(sales_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p

    def test_products_customer_no_cost_no_floor(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(customer_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p
            assert "salesFloor" not in p
            assert "absoluteFloor" not in p

    def test_offers_orders_companies_dashboard_analytics(self, api_client, base_url, admin_token):
        for path in ("/api/offers", "/api/orders", "/api/companies", "/api/dashboard",
                     "/api/analytics"):
            r = api_client.get(f"{base_url}{path}", headers=hdr(admin_token))
            assert r.status_code == 200, f"{path} -> {r.status_code}"

    def test_analytics_margin_admin_only(self, api_client, base_url, admin_token, sales_token):
        ra = api_client.get(f"{base_url}/api/analytics", headers=hdr(admin_token))
        assert ra.status_code == 200
        # margin should be present for admin
        a = ra.json()
        # some flavor of margin field expected
        assert "topProducts" in a or "marginByProduct" in a or "totalMargin" in a
        rs = api_client.get(f"{base_url}/api/analytics", headers=hdr(sales_token))
        assert rs.status_code == 200
        s = rs.json()
        # margin/cost keys should be absent or zeroed for sales
        # accept either total absence or explicit zero
        blob = str(s).lower()
        assert "totalmargin" not in blob or '"totalmargin":0' in blob.replace(" ", "")


# ---------------- PHASE 1: Firmenverwaltung ----------------
class TestCompanyUpdate:
    def test_admin_can_update_company(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/companies/c1", headers=hdr(admin_token))
        assert r.status_code == 200
        original = r.json()
        pytest._i8_orig_c1 = {k: original.get(k, "") for k in
                              ("name", "city", "email", "phone", "vatId",
                               "assignedSalesRepId", "orderCycleDays", "active")}
        # ensure defaults
        pytest._i8_orig_c1.setdefault("orderCycleDays", 30)
        pytest._i8_orig_c1["active"] = original.get("active", True)

        body = {
            "name": original["name"],  # keep name
            "city": "München",
            "email": "test_edit_c1@ss.de",
            "phone": "+49 89 11111",
            "vatId": "DE999888777",
            "assignedSalesRepId": original.get("assignedSalesRepId") or "u-sales",
            "orderCycleDays": 21,
            "active": True,
        }
        u = api_client.put(f"{base_url}/api/companies/c1", json=body, headers=hdr(admin_token))
        assert u.status_code == 200, u.text
        j = u.json()
        assert j["city"] == "München"
        assert j["vatId"] == "DE999888777"
        assert j["orderCycleDays"] == 21
        # verify via GET
        g = api_client.get(f"{base_url}/api/companies/c1", headers=hdr(admin_token))
        assert g.json()["orderCycleDays"] == 21

    def test_sales_forbidden(self, api_client, base_url, sales_token):
        body = {"name": "X", "city": "x", "email": "", "phone": "", "vatId": "",
                "assignedSalesRepId": None, "orderCycleDays": 30, "active": True}
        r = api_client.put(f"{base_url}/api/companies/c1", json=body, headers=hdr(sales_token))
        assert r.status_code == 403

    def test_customer_forbidden(self, api_client, base_url, customer_token):
        body = {"name": "X", "city": "x", "email": "", "phone": "", "vatId": "",
                "assignedSalesRepId": None, "orderCycleDays": 30, "active": True}
        r = api_client.put(f"{base_url}/api/companies/c1", json=body, headers=hdr(customer_token))
        assert r.status_code == 403

    def test_restore_company(self, api_client, base_url, admin_token):
        orig = getattr(pytest, "_i8_orig_c1", None)
        if not orig:
            pytest.skip("no baseline captured")
        body = {
            "name": orig.get("name", "Ristorante Roma"),
            "city": orig.get("city", ""),
            "email": orig.get("email", ""),
            "phone": orig.get("phone", ""),
            "vatId": orig.get("vatId", ""),
            "assignedSalesRepId": orig.get("assignedSalesRepId"),
            "orderCycleDays": orig.get("orderCycleDays", 30),
            "active": orig.get("active", True),
        }
        r = api_client.put(f"{base_url}/api/companies/c1", json=body, headers=hdr(admin_token))
        assert r.status_code == 200


# ---------------- PHASE 1: Forced password + rate limit ----------------
class TestForcedPasswordAndRateLimit:
    def test_new_user_has_must_change_password_true(self, api_client, base_url, admin_token):
        body = {"name": "TEST i8 Sales", "email": "test_i8_forced@ss-coffee.de", "role": "sales"}
        r = api_client.post(f"{base_url}/api/users", json=body, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        j = r.json()
        pw = j["initialPassword"]
        pytest._i8_forced_user = j
        # login and check /me
        lg = api_client.post(f"{base_url}/api/auth/login",
                             data={"username": j["email"], "password": pw},
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert lg.status_code == 200
        tok = lg.json()["access_token"]
        me = api_client.get(f"{base_url}/api/auth/me", headers=hdr(tok))
        assert me.status_code == 200
        assert me.json()["must_change_password"] is True

    def test_change_password_flips_flag(self, api_client, base_url):
        u = getattr(pytest, "_i8_forced_user", None)
        if not u:
            pytest.skip("no forced user")
        pw = u["initialPassword"]
        lg = api_client.post(f"{base_url}/api/auth/login",
                             data={"username": u["email"], "password": pw},
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
        tok = lg.json()["access_token"]
        ch = api_client.post(f"{base_url}/api/auth/password/change",
                             json={"currentPassword": pw, "newPassword": "NewValid#2026"},
                             headers=hdr(tok))
        assert ch.status_code == 200, ch.text
        # login again & verify must_change_password False
        lg2 = api_client.post(f"{base_url}/api/auth/login",
                              data={"username": u["email"], "password": "NewValid#2026"},
                              headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert lg2.status_code == 200
        tok2 = lg2.json()["access_token"]
        me = api_client.get(f"{base_url}/api/auth/me", headers=hdr(tok2))
        assert me.json()["must_change_password"] is False

    def test_login_rate_limit_429(self, api_client, base_url):
        # use a stable unique email so we don't lock the shared seed accounts
        email = "test_i8_ratelimit@ss-coffee.de"
        # ensure user does not exist beforehand
        _db().users.delete_many({"email": email})
        # Try 5 wrong password attempts → 6th should be 429
        codes = []
        for _ in range(6):
            r = api_client.post(f"{base_url}/api/auth/login",
                                data={"username": email, "password": "WrongPass"},
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
            codes.append(r.status_code)
        assert 429 in codes, f"Expected 429 in {codes}"


# ---------------- PHASE 2: Invoices w/ VAT + collective ----------------
class TestInvoicesVat:
    def test_create_invoice_and_vat_breakdown(self, api_client, base_url, admin_token):
        # create a test order first for c1 (kunde@ visible) with product p1 (Kaffee 7%)
        prods = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token)).json()
        p1 = next(p for p in prods if p["id"] == "p1")
        body = {"companyId": "c1", "items": [{"productId": "p1", "qty": 10, "price": p1["standardPrice"]}]}
        r = api_client.post(f"{base_url}/api/orders", json=body, headers=hdr(admin_token))
        assert r.status_code == 200
        order = r.json()
        pytest._i8_order_id = order["id"]
        # create invoice
        inv_r = api_client.post(f"{base_url}/api/orders/{order['id']}/invoice",
                                headers=hdr(admin_token))
        assert inv_r.status_code == 200, inv_r.text
        inv = inv_r.json()
        pytest._i8_invoice_id = inv["id"]
        assert inv["id"].startswith("RE-")
        assert isinstance(inv.get("lineItems"), list) and len(inv["lineItems"]) == 1
        assert "taxBreakdown" in inv and "7" in inv["taxBreakdown"]
        # VAT math: net = 10 * price; tax = net*0.07; gross = net + tax
        expected_net = round(10 * p1["standardPrice"], 2)
        expected_tax = round(expected_net * 0.07, 2)
        assert abs(inv["net"] - expected_net) < 0.02, (inv["net"], expected_net)
        assert abs(inv["taxTotal"] - expected_tax) < 0.02
        assert abs(inv["amount"] - (expected_net + expected_tax)) < 0.02

    def test_duplicate_invoice_400(self, api_client, base_url, admin_token):
        oid = getattr(pytest, "_i8_order_id", None)
        if not oid:
            pytest.skip("no order")
        r = api_client.post(f"{base_url}/api/orders/{oid}/invoice", headers=hdr(admin_token))
        assert r.status_code == 400

    def test_collective_invoice(self, api_client, base_url, admin_token):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        r = api_client.get(
            f"{base_url}/api/companies/c1/collective-invoice",
            params={"year": now.year, "month": now.month},
            headers=hdr(admin_token),
        )
        assert r.status_code == 200, r.text
        j = r.json()
        assert "taxBreakdown" in j and "amount" in j and "orders" in j
        # our test order should be present
        oid = getattr(pytest, "_i8_order_id", None)
        if oid:
            assert any(o["id"] == oid for o in j["orders"])

    def test_customer_cannot_create_invoice(self, api_client, base_url, customer_token):
        oid = getattr(pytest, "_i8_order_id", None)
        if not oid:
            pytest.skip("no order")
        r = api_client.post(f"{base_url}/api/orders/{oid}/invoice", headers=hdr(customer_token))
        assert r.status_code == 403


# ---------------- PHASE 3: Subscriptions ----------------
class TestSubscriptions:
    def test_create_subscription(self, api_client, base_url, admin_token):
        body = {"companyId": "c1", "items": [{"productId": "p1", "qty": 5, "price": 12.5}],
                "intervalDays": 1}
        r = api_client.post(f"{base_url}/api/subscriptions", json=body, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        sub = r.json()
        pytest._i8_sub_id = sub["id"]
        assert sub["companyId"] == "c1"
        assert sub["active"] is True
        # force nextRun in the past so run picks it up
        _db().subscriptions.update_one({"id": sub["id"]}, {"$set": {"nextRun": "2020-01-01"}})

    def test_list_subscriptions_includes_new(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/subscriptions", headers=hdr(admin_token))
        assert r.status_code == 200
        ids = [s["id"] for s in r.json()]
        assert getattr(pytest, "_i8_sub_id", None) in ids

    def test_customer_forbidden_list(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/subscriptions", headers=hdr(customer_token))
        assert r.status_code == 403

    def test_toggle_subscription(self, api_client, base_url, admin_token):
        sid = getattr(pytest, "_i8_sub_id", None)
        if not sid:
            pytest.skip("no sub")
        r = api_client.put(f"{base_url}/api/subscriptions/{sid}/toggle",
                           headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["active"] is False
        # re-enable
        r2 = api_client.put(f"{base_url}/api/subscriptions/{sid}/toggle",
                            headers=hdr(admin_token))
        assert r2.json()["active"] is True

    def test_run_subscriptions_creates_order(self, api_client, base_url, admin_token):
        r = api_client.post(f"{base_url}/api/subscriptions/run", headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["ok"] is True
        assert j["count"] >= 1
        pytest._i8_sub_created_orders = j["created"]

    def test_run_sub_forbidden_sales(self, api_client, base_url, sales_token):
        r = api_client.post(f"{base_url}/api/subscriptions/run", headers=hdr(sales_token))
        assert r.status_code == 403

    def test_delete_subscription(self, api_client, base_url, admin_token):
        sid = getattr(pytest, "_i8_sub_id", None)
        if not sid:
            pytest.skip("no sub")
        r = api_client.delete(f"{base_url}/api/subscriptions/{sid}", headers=hdr(admin_token))
        assert r.status_code == 200
        # verify gone
        g = api_client.get(f"{base_url}/api/subscriptions", headers=hdr(admin_token))
        assert sid not in [s["id"] for s in g.json()]


# ---------------- PHASE 4: Stripe checkout ----------------
class TestStripeCheckout:
    def test_checkout_requires_auth(self, api_client, base_url):
        inv = getattr(pytest, "_i8_invoice_id", None)
        if not inv:
            pytest.skip("no invoice")
        r = api_client.post(f"{base_url}/api/invoices/{inv}/checkout")
        assert r.status_code == 401

    def test_checkout_forbidden_for_other_company(self, api_client, base_url):
        """Sales (u-sales) sees c1/c3/c4. Create invoice for c2 not visible to sales."""
        inv = getattr(pytest, "_i8_invoice_id", None)
        if not inv:
            pytest.skip("no invoice")
        # Actually check with a customer NOT on c1. Login another customer? We only have one
        # customer seed. Instead, verify a c2 invoice returns 403 for our customer token.
        db = _db()
        # move existing test invoice to c2 temporarily
        db.invoices.update_one({"id": inv}, {"$set": {"companyId": "c2"}})
        try:
            lg = api_client.post(f"{base_url}/api/auth/login",
                                 data={"username": "kunde@ss-coffee.de", "password": "Kunde#2026"},
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
            tok = lg.json()["access_token"]
            r = api_client.post(f"{base_url}/api/invoices/{inv}/checkout", headers=hdr(tok))
            assert r.status_code == 403
        finally:
            db.invoices.update_one({"id": inv}, {"$set": {"companyId": "c1"}})

    def test_checkout_happy_path_502_expected(self, api_client, base_url, admin_token):
        inv = getattr(pytest, "_i8_invoice_id", None)
        if not inv:
            pytest.skip("no invoice")
        r = api_client.post(f"{base_url}/api/invoices/{inv}/checkout", headers=hdr(admin_token))
        # Preview STRIPE_API_KEY is placeholder → 502 EXPECTED. 200 also acceptable.
        assert r.status_code in (200, 502), r.text

    def test_checkout_409_when_already_paid(self, api_client, base_url, admin_token):
        inv = getattr(pytest, "_i8_invoice_id", None)
        if not inv:
            pytest.skip("no invoice")
        # mark as paid via existing endpoint
        p = api_client.put(f"{base_url}/api/invoices/{inv}/pay", headers=hdr(admin_token))
        assert p.status_code == 200
        r = api_client.post(f"{base_url}/api/invoices/{inv}/checkout", headers=hdr(admin_token))
        assert r.status_code == 409

    def test_payment_status_returns_paid(self, api_client, base_url, admin_token):
        inv = getattr(pytest, "_i8_invoice_id", None)
        if not inv:
            pytest.skip("no invoice")
        r = api_client.get(f"{base_url}/api/invoices/{inv}/payment-status",
                           headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["status"] == "Bezahlt"


# ---------------- FINAL CLEANUP ----------------
class TestCleanup:
    def test_zzz_cleanup(self, api_client, base_url, admin_token):
        db = _db()
        # remove test users / rate-limit user
        db.users.delete_many({"email": {"$in": [
            "test_i8_forced@ss-coffee.de",
            "test_i8_ratelimit@ss-coffee.de",
        ]}})
        # remove any subs created by this run
        db.subscriptions.delete_many({"companyId": "c1", "intervalDays": 1})
        # remove test order + invoice + reset order.invoiceId if set
        oid = getattr(pytest, "_i8_order_id", None)
        inv = getattr(pytest, "_i8_invoice_id", None)
        if inv:
            db.invoices.delete_many({"id": inv})
        # also delete any subscription-created orders
        for extra in getattr(pytest, "_i8_sub_created_orders", []) or []:
            db.orders.delete_many({"id": extra})
        if oid:
            db.orders.delete_many({"id": oid})
        # reset counters to baseline (order->1000, invoice->1000)
        db.counters.update_one({"_id": "order"}, {"$set": {"seq": 1000}}, upsert=True)
        db.counters.update_one({"_id": "invoice"}, {"$set": {"seq": 1000}}, upsert=True)
        # reactivate all seed products
        for pid in ("p1", "p2", "p3", "p4"):
            db.products.update_one({"id": pid}, {"$set": {"active": True}})
        # password_resets
        db.password_resets.delete_many({})
        # restore c1 baseline (name/city/email/phone/vatId + orderCycleDays)
        db.companies.update_one({"id": "c1"}, {"$set": {
            "city": "Berlin", "email": "info@ristorante-roma.de",
            "phone": "+49 30 555 111 23", "vatId": "DE123456789",
            "orderCycleDays": 30, "active": True,
        }})
        print("iter8 cleanup complete")
