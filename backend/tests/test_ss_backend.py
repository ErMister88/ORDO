"""S&S B2B backend API tests"""
import requests
from tests.conftest import hdr, _login


# ---------- Auth ----------
class TestAuth:
    def test_login_admin_returns_token_and_user(self, api_client, base_url):
        r = _login(api_client, base_url, "admin@ss-coffee.de", "Admin#2026")
        assert r.status_code == 200
        data = r.json()
        assert "access_token" in data and data["user"]["role"] == "admin"

    def test_login_sales(self, api_client, base_url):
        r = _login(api_client, base_url, "vertrieb@ss-coffee.de", "Sales#2026")
        assert r.status_code == 200 and r.json()["user"]["role"] == "sales"

    def test_login_customer(self, api_client, base_url):
        r = _login(api_client, base_url, "kunde@ss-coffee.de", "Kunde#2026")
        assert r.status_code == 200 and r.json()["user"]["role"] == "customer"
        assert r.json()["user"]["companyId"] == "c1"

    def test_login_wrong_password(self, api_client, base_url):
        r = _login(api_client, base_url, "admin@ss-coffee.de", "wrong")
        assert r.status_code == 401

    def test_me_returns_role(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/auth/me", headers=hdr(admin_token))
        assert r.status_code == 200 and r.json()["role"] == "admin"

    def test_me_no_token_401(self, api_client, base_url):
        r = requests.get(f"{base_url}/api/auth/me")
        assert r.status_code == 401


# ---------- Companies (visibility) ----------
class TestCompanies:
    def test_admin_sees_4(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/companies", headers=hdr(admin_token))
        assert r.status_code == 200
        ids = sorted([c["id"] for c in r.json()])
        assert ids == ["c1", "c2", "c3", "c4"]

    def test_sales_sees_3(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/companies", headers=hdr(sales_token))
        assert r.status_code == 200
        ids = sorted([c["id"] for c in r.json()])
        assert ids == ["c1", "c3", "c4"]

    def test_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/companies", headers=hdr(customer_token))
        assert r.status_code == 403


# ---------- Products (customer redaction) ----------
class TestProducts:
    def test_admin_sees_floors(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        p = r.json()[0]
        assert "cost" in p and "salesFloor" in p and "absoluteFloor" in p

    def test_customer_no_floors(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(customer_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p and "salesFloor" not in p and "absoluteFloor" not in p


# ---------- Dashboard ----------
class TestDashboard:
    def test_admin_dashboard_kpis(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/dashboard", headers=hdr(admin_token))
        assert r.status_code == 200
        d = r.json()
        for k in ["revenueMonth", "activeCustomers", "openOffers", "pendingApprovals", "followups"]:
            assert k in d

    def test_sales_dashboard(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/dashboard", headers=hdr(sales_token))
        assert r.status_code == 200 and r.json()["role"] == "sales"

    def test_customer_dashboard(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/dashboard", headers=hdr(customer_token))
        assert r.status_code == 200
        d = r.json()
        assert d["role"] == "customer"
        assert d["companyName"] == "Ristorante Roma GmbH"
        assert "contract" in d and "openInvoices" in d


# ---------- Offers (floor-price logic) ----------
class TestOffers:
    def test_offer_approved_when_at_or_above_sales_floor(self, api_client, base_url, sales_token):
        payload = {"companyId": "c1", "termMonths": 48,
                   "items": [{"productId": "p1", "qty": 10, "price": 16.90}]}
        r = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r.status_code == 200
        assert r.json()["status"] == "Freigegeben"

    def test_offer_needs_approval_below_sales_floor(self, api_client, base_url, sales_token):
        # p1 salesFloor 15.90, absoluteFloor 14.90
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 5, "price": 15.50}]}
        r = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r.status_code == 200
        assert r.json()["status"] == "Freigabe nötig"

    def test_offer_rejected_below_absolute_floor(self, api_client, base_url, sales_token):
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 5, "price": 14.00}]}
        r = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r.status_code == 400

    def test_sales_cannot_offer_for_c2(self, api_client, base_url, sales_token):
        payload = {"companyId": "c2", "items": [{"productId": "p1", "qty": 5, "price": 16.90}]}
        r = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r.status_code == 403

    def test_admin_approve_and_reject_flow(self, api_client, base_url, admin_token, sales_token):
        # create an offer needing approval
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 5, "price": 15.50}]}
        r = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r.status_code == 200
        oid = r.json()["id"]
        # approve
        r2 = api_client.post(f"{base_url}/api/offers/{oid}/approve", json={"note": "ok"},
                             headers=hdr(admin_token))
        assert r2.status_code == 200 and r2.json()["status"] == "Freigegeben"
        # create another & reject
        r3 = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        oid2 = r3.json()["id"]
        r4 = api_client.post(f"{base_url}/api/offers/{oid2}/reject", json={"note": "no"},
                             headers=hdr(admin_token))
        assert r4.status_code == 200 and r4.json()["status"] == "Abgelehnt"

    def test_non_admin_cannot_approve(self, api_client, base_url, sales_token):
        r = api_client.post(f"{base_url}/api/offers/A-2026-0187/approve", json={"note": ""},
                            headers=hdr(sales_token))
        assert r.status_code == 403


# ---------- Orders ----------
class TestOrders:
    def test_customer_orders_own_company(self, api_client, base_url, customer_token):
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 5, "price": 15.90}]}
        r = api_client.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token))
        assert r.status_code == 200
        oid = r.json()["id"]
        # verify persistence
        r2 = api_client.get(f"{base_url}/api/orders", headers=hdr(customer_token))
        assert any(o["id"] == oid for o in r2.json())

    def test_customer_cannot_order_c2(self, api_client, base_url, customer_token):
        payload = {"companyId": "c2", "items": [{"productId": "p1", "qty": 5, "price": 16.90}]}
        r = api_client.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token))
        assert r.status_code == 403


# ---------- Contracts / Invoices ----------
class TestContractsInvoices:
    def test_customer_contracts_only_c1(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/contracts", headers=hdr(customer_token))
        assert r.status_code == 200
        assert all(c["companyId"] == "c1" for c in r.json())

    def test_customer_invoices_only_c1(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/invoices", headers=hdr(customer_token))
        assert r.status_code == 200
        assert all(i["companyId"] == "c1" for i in r.json())


# ---------- Analytics ----------
class TestAnalytics:
    def test_admin_analytics(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/analytics?months=6", headers=hdr(admin_token))
        assert r.status_code == 200
        d = r.json()
        assert len(d["series"]) == 6 and "totalRevenue" in d and "marginPct" in d

    def test_customer_analytics_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/analytics", headers=hdr(customer_token))
        assert r.status_code == 403
