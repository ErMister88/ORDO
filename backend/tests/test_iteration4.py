"""Iteration 4 backend tests: order cancel + product add regression."""
import pytest
def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# --- FEATURE: PUT /api/orders/{id}/cancel ---
class TestOrderCancel:
    def _create_neu_order(self, api_client, base_url, admin_token, company="c1"):
        # admin can post as any visible company
        r = api_client.post(
            f"{base_url}/api/orders",
            json={"companyId": company, "items": [{"productId": "p1", "qty": 5, "price": 15.90}]},
            headers=hdr(admin_token),
            timeout=30,
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_customer_cancels_own_neu_order(self, api_client, base_url, admin_token, customer_token):
        o = self._create_neu_order(api_client, base_url, admin_token, "c1")
        oid = o["id"]
        r = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(customer_token), timeout=30)
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Storniert"
        # verify persistence
        g = api_client.get(f"{base_url}/api/orders/{oid}", headers=hdr(customer_token))
        assert g.status_code == 200
        body = g.json()
        assert body["status"] == "Storniert"
        assert body.get("cancelledAt")
        assert body.get("cancelledBy") == "u-customer"

    def test_second_cancel_returns_400(self, api_client, base_url, admin_token, customer_token):
        o = self._create_neu_order(api_client, base_url, admin_token, "c1")
        oid = o["id"]
        r1 = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(customer_token))
        assert r1.status_code == 200
        r2 = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(customer_token))
        assert r2.status_code == 400
        assert "bearbeitet" in r2.json()["detail"].lower()

    def test_other_company_gets_403(self, api_client, base_url, admin_token, customer_token):
        # create order for company c2 (customer belongs to c1)
        o = self._create_neu_order(api_client, base_url, admin_token, "c2")
        oid = o["id"]
        r = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(customer_token))
        assert r.status_code == 403

    def test_missing_order_404(self, api_client, base_url, customer_token):
        r = api_client.put(f"{base_url}/api/orders/B-9999-99999/cancel", headers=hdr(customer_token))
        assert r.status_code == 404

    def test_cancel_blocked_when_bestaetigt(self, api_client, base_url, admin_token):
        o = self._create_neu_order(api_client, base_url, admin_token, "c1")
        oid = o["id"]
        # advance to Bestätigt
        adv = api_client.put(f"{base_url}/api/orders/{oid}/status",
                             json={"status": "Bestätigt"}, headers=hdr(admin_token))
        assert adv.status_code == 200
        r = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(admin_token))
        assert r.status_code == 400
        assert "bearbeitet" in r.json()["detail"].lower()

    def test_cancel_blocked_when_versendet(self, api_client, base_url, admin_token):
        o = self._create_neu_order(api_client, base_url, admin_token, "c1")
        oid = o["id"]
        for st in ("Bestätigt", "Kommissioniert", "Versendet"):
            api_client.put(f"{base_url}/api/orders/{oid}/status",
                           json={"status": st}, headers=hdr(admin_token))
        r = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(admin_token))
        assert r.status_code == 400


# --- REGRESSION ---
class TestRegression:
    def test_login_three_roles(self, admin_token, sales_token, customer_token):
        assert admin_token and sales_token and customer_token

    def test_create_order_and_advance_status(self, api_client, base_url, admin_token):
        r = api_client.post(
            f"{base_url}/api/orders",
            json={"companyId": "c1", "items": [{"productId": "p1", "qty": 3, "price": 15.90}]},
            headers=hdr(admin_token),
        )
        assert r.status_code == 200
        oid = r.json()["id"]
        adv = api_client.put(f"{base_url}/api/orders/{oid}/status",
                             json={"status": "Bestätigt"}, headers=hdr(admin_token))
        assert adv.status_code == 200
        # cleanup: leave it - counter test note in main summary

    def test_products_and_create_product(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        assert len(r.json()) >= 4
        # create TEST_ product (will be cleaned up after suite)
        body = {"brand": "TEST_Brand", "name": "TEST_Prod", "unit": "kg",
                "standardPrice": 20.0, "salesFloor": 18.0, "absoluteFloor": 16.0, "cost": 12.0}
        c = api_client.post(f"{base_url}/api/products", json=body, headers=hdr(admin_token))
        assert c.status_code == 200
        pid = c.json()["id"]
        assert pid.startswith("p")
