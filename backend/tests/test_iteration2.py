"""Iteration 2: products CRUD, customer-prices, order status flow, ID uniqueness"""
from tests.conftest import hdr


# ---------- Products CRUD (admin) ----------
class TestProductsCRUD:
    def test_create_product_admin(self, api_client, base_url, admin_token):
        payload = {"brand": "TEST_Brand", "name": "TEST_Prod", "unit": "kg",
                   "standardPrice": 20.0, "salesFloor": 18.0, "absoluteFloor": 16.0,
                   "cost": 12.0, "active": True}
        r = api_client.post(f"{base_url}/api/products", json=payload, headers=hdr(admin_token))
        assert r.status_code == 200, r.text
        p = r.json()
        assert p["id"].startswith("p") and p["name"] == "TEST_Prod"
        # persistence
        r2 = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert any(x["id"] == p["id"] for x in r2.json())
        # keep id for update test
        TestProductsCRUD.created_id = p["id"]

    def test_create_product_non_admin_forbidden(self, api_client, base_url, sales_token):
        payload = {"brand": "X", "name": "Y", "unit": "kg",
                   "standardPrice": 1, "salesFloor": 1, "absoluteFloor": 1, "cost": 1}
        r = api_client.post(f"{base_url}/api/products", json=payload, headers=hdr(sales_token))
        assert r.status_code == 403

    def test_update_product_admin(self, api_client, base_url, admin_token):
        pid = getattr(TestProductsCRUD, "created_id", None)
        assert pid, "created_id missing from previous test"
        payload = {"brand": "TEST_Brand", "name": "TEST_Prod_Updated", "unit": "kg",
                   "standardPrice": 22.0, "salesFloor": 20.0, "absoluteFloor": 18.0,
                   "cost": 12.5, "active": True}
        r = api_client.put(f"{base_url}/api/products/{pid}", json=payload, headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["name"] == "TEST_Prod_Updated"

    def test_update_product_404(self, api_client, base_url, admin_token):
        payload = {"brand": "x", "name": "y", "unit": "kg",
                   "standardPrice": 1, "salesFloor": 1, "absoluteFloor": 1, "cost": 1}
        r = api_client.put(f"{base_url}/api/products/does-not-exist",
                           json=payload, headers=hdr(admin_token))
        assert r.status_code == 404


# ---------- Customer Prices ----------
class TestCustomerPrices:
    def test_upsert_customer_price_admin(self, api_client, base_url, admin_token):
        r = api_client.post(f"{base_url}/api/customer-prices",
                            json={"companyId": "c2", "productId": "p1", "price": 15.75},
                            headers=hdr(admin_token))
        assert r.status_code == 200
        # verify via GET
        r2 = api_client.get(f"{base_url}/api/companies/c2/prices", headers=hdr(admin_token))
        rows = r2.json()
        p1 = [x for x in rows if x["productId"] == "p1"]
        assert p1 and p1[0]["price"] == 15.75

    def test_upsert_customer_price_updates_existing(self, api_client, base_url, admin_token):
        r = api_client.post(f"{base_url}/api/customer-prices",
                            json={"companyId": "c2", "productId": "p1", "price": 15.20},
                            headers=hdr(admin_token))
        assert r.status_code == 200
        r2 = api_client.get(f"{base_url}/api/companies/c2/prices", headers=hdr(admin_token))
        p1 = [x for x in r2.json() if x["productId"] == "p1"]
        assert p1[0]["price"] == 15.20

    def test_delete_customer_price_admin(self, api_client, base_url, admin_token):
        r = api_client.delete(f"{base_url}/api/customer-prices",
                              params={"companyId": "c2", "productId": "p1"},
                              headers=hdr(admin_token))
        assert r.status_code == 200
        r2 = api_client.get(f"{base_url}/api/companies/c2/prices", headers=hdr(admin_token))
        assert not any(x["productId"] == "p1" for x in r2.json())

    def test_customer_price_non_admin_forbidden(self, api_client, base_url, sales_token):
        r = api_client.post(f"{base_url}/api/customer-prices",
                            json={"companyId": "c1", "productId": "p1", "price": 10.0},
                            headers=hdr(sales_token))
        assert r.status_code == 403
        r2 = api_client.delete(f"{base_url}/api/customer-prices",
                               params={"companyId": "c1", "productId": "p1"},
                               headers=hdr(sales_token))
        assert r2.status_code == 403


# ---------- Order detail + status transitions ----------
class TestOrderDetailAndStatus:
    def test_get_order_admin_ok(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/orders/B-2026-00987", headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["id"] == "B-2026-00987"

    def test_get_order_missing_404(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/orders/B-2099-99999", headers=hdr(admin_token))
        assert r.status_code == 404

    def test_get_order_other_company_403(self, api_client, base_url, customer_token):
        # customer is c1, create an order for c2 via admin, then try customer read
        # first find any c2 order
        r_all = api_client.get(f"{base_url}/api/orders",
                               headers=hdr(customer_token))
        # customer only sees c1 orders; try fetching a c2 order id directly
        # we know B-2026-00007 area belongs to c2 due to seed; use dashboard/admin listing
        r_admin = api_client.get(f"{base_url}/api/orders",
                                 headers={"Authorization": "Bearer INVALID"})
        # simpler: pick a known seeded c2 order by asking admin
        pass  # handled by next test using admin listing

    def test_customer_forbidden_on_other_company_order(self, api_client, base_url,
                                                      admin_token, customer_token):
        r_admin = api_client.get(f"{base_url}/api/orders", headers=hdr(admin_token))
        c2_ids = [o["id"] for o in r_admin.json() if o["companyId"] == "c2"]
        assert c2_ids, "expected seeded c2 orders"
        r = api_client.get(f"{base_url}/api/orders/{c2_ids[0]}", headers=hdr(customer_token))
        assert r.status_code == 403

    def test_set_status_valid_admin(self, api_client, base_url, admin_token, customer_token):
        # create a fresh order as customer
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 3, "price": 15.90}]}
        r = api_client.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token))
        assert r.status_code == 200
        oid = r.json()["id"]
        # advance
        r2 = api_client.put(f"{base_url}/api/orders/{oid}/status",
                            json={"status": "Bestätigt"}, headers=hdr(admin_token))
        assert r2.status_code == 200 and r2.json()["status"] == "Bestätigt"
        # verify persisted
        r3 = api_client.get(f"{base_url}/api/orders/{oid}", headers=hdr(admin_token))
        assert r3.json()["status"] == "Bestätigt"

    def test_set_status_invalid_400(self, api_client, base_url, admin_token):
        r = api_client.put(f"{base_url}/api/orders/B-2026-00987/status",
                           json={"status": "Bogus"}, headers=hdr(admin_token))
        assert r.status_code == 400

    def test_set_status_customer_forbidden(self, api_client, base_url, customer_token):
        r = api_client.put(f"{base_url}/api/orders/B-2026-00987/status",
                           json={"status": "Bestätigt"}, headers=hdr(customer_token))
        assert r.status_code == 403


# ---------- Regression: unique sequential IDs ----------
class TestUniqueIDs:
    def test_two_rapid_offers_distinct_ids(self, api_client, base_url, sales_token):
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 2, "price": 16.90}]}
        r1 = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        r2 = api_client.post(f"{base_url}/api/offers", json=payload, headers=hdr(sales_token))
        assert r1.status_code == 200 and r2.status_code == 200
        id1, id2 = r1.json()["id"], r2.json()["id"]
        assert id1 != id2
        assert id1.startswith("A-") and id2.startswith("A-")

    def test_two_rapid_orders_distinct_ids(self, api_client, base_url, customer_token):
        payload = {"companyId": "c1", "items": [{"productId": "p1", "qty": 1, "price": 15.90}]}
        r1 = api_client.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token))
        r2 = api_client.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token))
        assert r1.status_code == 200 and r2.status_code == 200
        assert r1.json()["id"] != r2.json()["id"]
