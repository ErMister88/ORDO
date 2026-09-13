"""Iteration 3: price history, order shipping (Versendet), invoice pay, multi-line offer"""
from tests.conftest import hdr


# ---------- Price History ----------
class TestPriceHistory:
    def test_price_change_records_history_and_no_dupe_on_unchanged(
        self, api_client, base_url, admin_token
    ):
        # Cleanup: remove any existing history for c4/p1 to keep test deterministic
        # (no delete endpoint; we just assert relative growth)
        r0 = api_client.get(f"{base_url}/api/companies/c4/price-history",
                            headers=hdr(admin_token))
        assert r0.status_code == 200
        before = len(r0.json())

        # First change: 16.20 -> 15.80
        r1 = api_client.post(f"{base_url}/api/customer-prices",
                             json={"companyId": "c4", "productId": "p1", "price": 15.80},
                             headers=hdr(admin_token))
        assert r1.status_code == 200
        # Second change: 15.80 -> 15.60
        r2 = api_client.post(f"{base_url}/api/customer-prices",
                             json={"companyId": "c4", "productId": "p1", "price": 15.60},
                             headers=hdr(admin_token))
        assert r2.status_code == 200
        # No change: 15.60 -> 15.60 (should NOT create a new history row)
        r3 = api_client.post(f"{base_url}/api/customer-prices",
                             json={"companyId": "c4", "productId": "p1", "price": 15.60},
                             headers=hdr(admin_token))
        assert r3.status_code == 200

        r4 = api_client.get(f"{base_url}/api/companies/c4/price-history",
                            headers=hdr(admin_token))
        after = r4.json()
        assert len(after) == before + 2, f"expected +2 rows, got {len(after) - before}"

        # newest-first
        newest = after[0]
        assert newest["productId"] == "p1"
        assert newest["newPrice"] == 15.60
        assert newest["oldPrice"] == 15.80
        assert "changedByName" in newest and newest["changedByName"]

    def test_price_history_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/companies/c1/price-history",
                           headers=hdr(customer_token))
        assert r.status_code == 403

    def test_price_history_other_company_forbidden(self, api_client, base_url, sales_token):
        # sales sees c1/c3/c4; c2 is admin-only
        r = api_client.get(f"{base_url}/api/companies/c2/price-history",
                           headers=hdr(sales_token))
        assert r.status_code == 403

    def test_price_history_sales_own_ok(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/companies/c1/price-history",
                           headers=hdr(sales_token))
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------- Order Shipping ----------
class TestOrderShipping:
    def test_versendet_sets_tracking_and_eta_and_is_idempotent(
        self, api_client, base_url, admin_token, customer_token
    ):
        # create a fresh order as customer
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 2, "price": 15.90}]}
        r = api_client.post(f"{base_url}/api/orders", json=payload,
                            headers=hdr(customer_token))
        assert r.status_code == 200
        oid = r.json()["id"]

        for status in ["Bestätigt", "Kommissioniert", "Versendet"]:
            rr = api_client.put(f"{base_url}/api/orders/{oid}/status",
                                json={"status": status}, headers=hdr(admin_token))
            assert rr.status_code == 200, rr.text

        g1 = api_client.get(f"{base_url}/api/orders/{oid}",
                            headers=hdr(admin_token)).json()
        assert g1["status"] == "Versendet"
        assert g1.get("trackingNumber", "").startswith("SS")
        assert g1.get("shippedAt")
        assert g1.get("estimatedDelivery")

        tracking1 = g1["trackingNumber"]
        eta1 = g1["estimatedDelivery"]

        # setting status again — should NOT overwrite tracking
        api_client.put(f"{base_url}/api/orders/{oid}/status",
                       json={"status": "Versendet"}, headers=hdr(admin_token))
        g2 = api_client.get(f"{base_url}/api/orders/{oid}",
                            headers=hdr(admin_token)).json()
        assert g2["trackingNumber"] == tracking1
        assert g2["estimatedDelivery"] == eta1

        # Store id for the frontend test to use
        TestOrderShipping.shipped_order_id = oid


# ---------- Invoice Pay ----------
class TestInvoicePay:
    def test_pay_invoice_admin_and_dashboard_drop(
        self, api_client, base_url, admin_token
    ):
        # Get open invoice sum before
        d_before = api_client.get(f"{base_url}/api/dashboard",
                                  headers=hdr(admin_token)).json()
        open_before = d_before["openInvoices"]

        # Get open invoices, pick one
        invs = api_client.get(f"{base_url}/api/invoices",
                              headers=hdr(admin_token)).json()
        open_invs = [i for i in invs if i["status"] != "Bezahlt"]
        assert open_invs, "expected at least one open invoice"
        target = open_invs[0]

        r = api_client.put(f"{base_url}/api/invoices/{target['id']}/pay",
                           headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "Bezahlt"

        # verify persistence
        invs2 = api_client.get(f"{base_url}/api/invoices",
                               headers=hdr(admin_token)).json()
        row = next(i for i in invs2 if i["id"] == target["id"])
        assert row["status"] == "Bezahlt"
        assert row.get("paidAt")

        d_after = api_client.get(f"{base_url}/api/dashboard",
                                 headers=hdr(admin_token)).json()
        open_after = d_after["openInvoices"]
        assert round(open_before - open_after, 2) == round(target["amount"], 2)

        TestInvoicePay.paid_invoice_id = target["id"]
        TestInvoicePay.paid_amount = target["amount"]

    def test_pay_invoice_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.put(f"{base_url}/api/invoices/RE-2026-0921/pay",
                           headers=hdr(customer_token))
        assert r.status_code == 403

    def test_pay_invoice_missing_404(self, api_client, base_url, admin_token):
        r = api_client.put(f"{base_url}/api/invoices/RE-9999-9999/pay",
                           headers=hdr(admin_token))
        assert r.status_code == 404


# ---------- Multi-line Offers ----------
class TestMultiLineOffers:
    def test_multi_items_all_ok_freigegeben(self, api_client, base_url, sales_token):
        payload = {"companyId": "c1", "items": [
            {"productId": "p1", "qty": 10, "price": 16.90},   # >= salesFloor 15.90
            {"productId": "p2", "qty": 5, "price": 18.90},    # >= salesFloor 17.90
        ]}
        r = api_client.post(f"{base_url}/api/offers", json=payload,
                            headers=hdr(sales_token))
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["status"] == "Freigegeben"
        assert len(o["items"]) == 2

    def test_multi_items_below_salesfloor_needs_approval(
        self, api_client, base_url, sales_token
    ):
        payload = {"companyId": "c1", "items": [
            {"productId": "p1", "qty": 10, "price": 16.90},
            {"productId": "p2", "qty": 5, "price": 17.50},   # below salesFloor 17.90, above absFloor 16.90
        ]}
        r = api_client.post(f"{base_url}/api/offers", json=payload,
                            headers=hdr(sales_token))
        assert r.status_code == 200, r.text
        o = r.json()
        assert o["status"] == "Freigabe nötig"

    def test_multi_items_below_absolute_floor_400(
        self, api_client, base_url, sales_token
    ):
        payload = {"companyId": "c1", "items": [
            {"productId": "p1", "qty": 10, "price": 16.90},
            {"productId": "p2", "qty": 5, "price": 16.00},   # below absoluteFloor 16.90
        ]}
        r = api_client.post(f"{base_url}/api/offers", json=payload,
                            headers=hdr(sales_token))
        assert r.status_code == 400


# ---------- Regression: 3-role login + core endpoints ----------
class TestRegression:
    def test_three_role_login(self, api_client, base_url,
                              admin_token, sales_token, customer_token):
        assert admin_token and sales_token and customer_token

    def test_core_endpoints_ok(self, api_client, base_url, admin_token):
        for path in ["/api/products", "/api/companies", "/api/offers",
                     "/api/orders", "/api/invoices", "/api/contracts",
                     "/api/dashboard", "/api/analytics"]:
            r = api_client.get(f"{base_url}{path}", headers=hdr(admin_token))
            assert r.status_code == 200, f"{path} -> {r.status_code}"

    def test_unique_sequential_ids(self, api_client, base_url, sales_token):
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 2, "price": 16.90}]}
        r1 = api_client.post(f"{base_url}/api/offers", json=payload,
                             headers=hdr(sales_token))
        r2 = api_client.post(f"{base_url}/api/offers", json=payload,
                             headers=hdr(sales_token))
        assert r1.status_code == 200 and r2.status_code == 200
        id1, id2 = r1.json()["id"], r2.json()["id"]
        assert id1 != id2
        assert id1.startswith("A-") and id2.startswith("A-")
