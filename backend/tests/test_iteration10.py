"""Iteration 10 — B2C public shop tests
- Public catalog & settings (no auth, no cost leak)
- Guest order + shipping math
- Stripe checkout (502 expected in preview)
- Admin shop settings + orders (RBAC)
- B2C price roundtrip on products
"""
import pytest
import requests
import os

BASE_URL = ""


@pytest.fixture(scope="module", autouse=True)
def _configure_base_url(base_url):
    global BASE_URL
    BASE_URL = base_url
    yield
    BASE_URL = ""


@pytest.fixture(scope="module")
def admin_tok(admin_token):
    return admin_token


@pytest.fixture(scope="module")
def sales_tok(sales_token):
    return sales_token


@pytest.fixture(scope="module")
def customer_tok(customer_token):
    return customer_token


def hdr(t):
    return {"Authorization": f"Bearer {t}"}


# --- 1. Public catalog: no auth, no cost leak ---
class TestShopPublicCatalog:
    def test_shop_products_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/shop/products", timeout=30)
        assert r.status_code == 200
        items = r.json()
        assert isinstance(items, list) and len(items) > 0, "expected active B2C products"
        allowed = {"id", "brand", "name", "unit", "imageUrl", "description", "b2cPrice", "taxRate", "stock"}
        for p in items:
            leaked = set(p.keys()) - allowed
            assert not leaked, f"public product leaked fields: {leaked}"
            for forbidden in ("cost", "salesFloor", "absoluteFloor", "standardPrice", "discountTiers"):
                assert forbidden not in p, f"public product exposed {forbidden}: {p}"
            assert p["b2cPrice"] > 0

    def test_shop_settings_no_auth(self):
        r = requests.get(f"{BASE_URL}/api/shop/settings", timeout=30)
        assert r.status_code == 200
        s = r.json()
        assert set(s.keys()) == {"freeShippingThreshold", "shippingFee"}
        assert s["freeShippingThreshold"] > 0
        assert s["shippingFee"] >= 0


# --- 2. Guest orders: shipping math + validation ---
class TestShopGuestOrders:
    created_ids: list = []

    @classmethod
    def teardown_class(cls):
        # cleanup via direct mongo
        try:
            import pymongo
            client = pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
            db = client[os.environ.get("DB_NAME", "ss_b2b_database")]
            for oid in cls.created_ids:
                db.shop_orders.delete_one({"id": oid})
            # reset shop seq to max remaining
            remaining = list(db.shop_orders.find({}, {"id": 1}))
            if remaining:
                max_seq = max(int(o["id"].split("-")[-1]) for o in remaining)
                db.counters.update_one({"_id": "shop"}, {"$set": {"seq": max_seq}}, upsert=True)
            else:
                db.counters.update_one({"_id": "shop"}, {"$set": {"seq": 0}}, upsert=True)
        except Exception as e:
            print(f"cleanup warning: {e}")

    def _products(self):
        r = requests.get(f"{BASE_URL}/api/shop/products", timeout=30)
        r.raise_for_status()
        return r.json()

    def _settings(self):
        r = requests.get(f"{BASE_URL}/api/shop/settings", timeout=30)
        r.raise_for_status()
        return r.json()

    def test_order_small_charges_shipping(self):
        prods = self._products()
        s = self._settings()
        # pick smallest priced product and qty=1 that's below threshold
        p = min(prods, key=lambda x: x["b2cPrice"])
        assert p["b2cPrice"] < s["freeShippingThreshold"], "seed setup: expected a small-priced product below threshold"
        payload = {
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST_iter10 Kunde", "email": "iter10@example.com",
                          "street": "Testweg 1", "zip": "10115", "city": "Berlin"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["subtotal"] == round(p["b2cPrice"], 2)
        assert j["shipping"] == round(s["shippingFee"], 2)
        assert j["total"] == round(p["b2cPrice"] + s["shippingFee"], 2)
        assert j["id"].startswith("S-") and len(j["id"].split("-")[-1]) == 5
        TestShopGuestOrders.created_ids.append(j["id"])

    def test_order_over_threshold_free_shipping(self):
        prods = self._products()
        s = self._settings()
        # pick a product where qty=large * price >= threshold
        p = max(prods, key=lambda x: x["b2cPrice"])
        qty = int((s["freeShippingThreshold"] / p["b2cPrice"]) + 1)
        payload = {
            "items": [{"productId": p["id"], "qty": qty}],
            "customer": {"name": "TEST_iter10 Big", "email": "iter10big@example.com"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["subtotal"] >= s["freeShippingThreshold"]
        assert j["shipping"] == 0.0
        assert j["total"] == j["subtotal"]
        TestShopGuestOrders.created_ids.append(j["id"])

    def test_order_missing_name_400(self):
        prods = self._products()
        p = prods[0]
        payload = {
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "", "email": "x@y.de"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 400

    def test_order_invalid_email_400(self):
        prods = self._products()
        p = prods[0]
        payload = {
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "Test", "email": "no-at-sign"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 400

    def test_order_empty_items_400(self):
        payload = {
            "items": [],
            "customer": {"name": "Test", "email": "x@y.de"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 400

    def test_server_recomputes_price_ignores_client(self):
        prods = self._products()
        p = prods[0]
        # Client sends no 'price' — but even if extra fields were sent, server uses b2cPrice from DB.
        payload = {
            "items": [{"productId": p["id"], "qty": 2, "price": 0.01}],
            "customer": {"name": "TEST_iter10 Recalc", "email": "iter10recalc@example.com"}
        }
        r = requests.post(f"{BASE_URL}/api/shop/orders", json=payload, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        assert j["subtotal"] == round(p["b2cPrice"] * 2, 2), f"server must ignore client-supplied price; got {j}"
        TestShopGuestOrders.created_ids.append(j["id"])


# --- 3. Stripe checkout (502 expected) & payment status ---
class TestShopCheckout:
    _oid = None

    @classmethod
    def teardown_class(cls):
        if cls._oid:
            try:
                import pymongo
                client = pymongo.MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
                db = client[os.environ.get("DB_NAME", "ss_b2b_database")]
                db.shop_orders.delete_one({"id": cls._oid})
            except Exception as e:
                print(f"cleanup warning: {e}")

    def test_checkout_502_expected_preview(self):
        # Create a small order first
        prods = requests.get(f"{BASE_URL}/api/shop/products", timeout=30).json()
        p = prods[0]
        r = requests.post(f"{BASE_URL}/api/shop/orders", json={
            "items": [{"productId": p["id"], "qty": 1}],
            "customer": {"name": "TEST_iter10 Chk", "email": "chk@example.com"}
        }, timeout=30)
        assert r.status_code == 200
        oid = r.json()["id"]
        TestShopCheckout._oid = oid
        r2 = requests.post(f"{BASE_URL}/api/shop/orders/{oid}/checkout", json={}, timeout=30)
        assert r2.status_code in (200, 502), f"unexpected status: {r2.status_code} {r2.text}"

    def test_payment_status_open(self):
        assert TestShopCheckout._oid
        r = requests.get(f"{BASE_URL}/api/shop/orders/{TestShopCheckout._oid}/payment-status", timeout=30)
        assert r.status_code == 200
        assert r.json()["status"] in ("Offen", "Bezahlt")

    def test_payment_status_404(self):
        r = requests.get(f"{BASE_URL}/api/shop/orders/S-9999-99999/payment-status", timeout=30)
        assert r.status_code == 404


# --- 4. Admin shop settings + orders RBAC ---
class TestShopAdmin:
    original = None
    token = None

    @classmethod
    def teardown_class(cls):
        # restore settings
        if cls.original and cls.token:
            try:
                requests.put(
                    f"{BASE_URL}/api/shop/settings",
                    json=cls.original,
                    headers=hdr(cls.token),
                    timeout=30,
                )
            except Exception as e:
                print(f"restore warning: {e}")

    def test_put_settings_admin(self, admin_tok):
        # save original
        TestShopAdmin.token = admin_tok
        TestShopAdmin.original = requests.get(f"{BASE_URL}/api/shop/settings", timeout=30).json()
        new = {"freeShippingThreshold": 75.0, "shippingFee": 5.5}
        r = requests.put(f"{BASE_URL}/api/shop/settings", json=new, headers=hdr(admin_tok), timeout=30)
        assert r.status_code == 200
        # verify persisted
        g = requests.get(f"{BASE_URL}/api/shop/settings", timeout=30).json()
        assert g["freeShippingThreshold"] == 75.0
        assert g["shippingFee"] == 5.5

    def test_put_settings_sales_403(self, sales_tok):
        r = requests.put(f"{BASE_URL}/api/shop/settings",
                         json={"freeShippingThreshold": 30, "shippingFee": 3},
                         headers=hdr(sales_tok), timeout=30)
        assert r.status_code == 403

    def test_put_settings_customer_403(self, customer_tok):
        r = requests.put(f"{BASE_URL}/api/shop/settings",
                         json={"freeShippingThreshold": 30, "shippingFee": 3},
                         headers=hdr(customer_tok), timeout=30)
        assert r.status_code == 403

    def test_put_settings_no_auth_401(self):
        r = requests.put(f"{BASE_URL}/api/shop/settings",
                         json={"freeShippingThreshold": 30, "shippingFee": 3}, timeout=30)
        assert r.status_code in (401, 403)

    def test_list_orders_admin(self, admin_tok):
        r = requests.get(f"{BASE_URL}/api/shop/orders", headers=hdr(admin_tok), timeout=30)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        for row in rows:
            assert "_id" not in row  # ObjectId must be stripped

    def test_list_orders_sales(self, sales_tok):
        r = requests.get(f"{BASE_URL}/api/shop/orders", headers=hdr(sales_tok), timeout=30)
        assert r.status_code == 200

    def test_list_orders_customer_403(self, customer_tok):
        r = requests.get(f"{BASE_URL}/api/shop/orders", headers=hdr(customer_tok), timeout=30)
        assert r.status_code == 403


# --- 5. B2C price roundtrip on products (admin) ---
class TestB2CPriceRoundtrip:
    original_b2c = None
    product_id = None
    token = None

    @classmethod
    def teardown_class(cls):
        if cls.original_b2c is not None and cls.product_id and cls.token:
            try:
                # Fetch full product (admin) then PUT again with original b2cPrice
                r = requests.get(
                    f"{BASE_URL}/api/products",
                    headers=hdr(cls.token),
                    timeout=30,
                )
                p = next((x for x in r.json() if x["id"] == cls.product_id), None)
                if p:
                    p["b2cPrice"] = cls.original_b2c
                    p.pop("id", None)
                    requests.put(
                        f"{BASE_URL}/api/products/{cls.product_id}",
                        json=p,
                        headers=hdr(cls.token),
                        timeout=30,
                    )
            except Exception as e:
                print(f"restore warning: {e}")

    def test_put_b2cprice(self, admin_tok):
        TestB2CPriceRoundtrip.token = admin_tok
        r = requests.get(f"{BASE_URL}/api/products", headers=hdr(admin_tok), timeout=30)
        assert r.status_code == 200
        prods = r.json()
        p = prods[0]
        TestB2CPriceRoundtrip.product_id = p["id"]
        TestB2CPriceRoundtrip.original_b2c = p.get("b2cPrice")
        new_price = 27.77
        body = {k: v for k, v in p.items() if k != "id"}
        body["b2cPrice"] = new_price
        r2 = requests.put(f"{BASE_URL}/api/products/{p['id']}", json=body, headers=hdr(admin_tok), timeout=30)
        assert r2.status_code == 200, r2.text
        # verify via /api/products
        rv = requests.get(f"{BASE_URL}/api/products", headers=hdr(admin_tok), timeout=30).json()
        pv = next(x for x in rv if x["id"] == p["id"])
        assert pv["b2cPrice"] == new_price
        # verify via public shop
        shop = requests.get(f"{BASE_URL}/api/shop/products", timeout=30).json()
        ps = next((x for x in shop if x["id"] == p["id"]), None)
        assert ps and ps["b2cPrice"] == new_price


# --- 6. Cost invariant on sales/customer product endpoints ---
class TestCostInvariant:
    def test_sales_products_no_cost(self, sales_tok):
        r = requests.get(f"{BASE_URL}/api/products", headers=hdr(sales_tok), timeout=30)
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"sales must not see cost: {p}"

    def test_customer_products_no_cost(self, customer_tok):
        r = requests.get(f"{BASE_URL}/api/products", headers=hdr(customer_tok), timeout=30)
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"customer must not see cost: {p}"
            assert "salesFloor" not in p
            assert "absoluteFloor" not in p

    def test_public_shop_no_cost(self):
        r = requests.get(f"{BASE_URL}/api/shop/products", timeout=30)
        for p in r.json():
            assert "cost" not in p
            assert "salesFloor" not in p
            assert "absoluteFloor" not in p
