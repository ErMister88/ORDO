"""Iteration 5 backend tests:
   - Image upload (POST /api/upload) + serving (GET /api/files/{path})
   - Product description + imageUrl persistence
   - Role-based hiding of Deckungsbeitrag / Marge (products + analytics)
   - Regression: 3-role login, offer approve/reject, order status/cancel, invoices pay
"""
import io
import struct
import zlib
import pytest


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


def _tiny_png_bytes() -> bytes:
    """Deterministic 1x1 red PNG."""
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00" + bytes([255, 0, 0])  # filter + RGB pixel
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


# ---------------------------------------------------------------------------
# FEATURE 1: /api/upload + /api/files/{path}
# ---------------------------------------------------------------------------
class TestUpload:
    def test_admin_upload_returns_url_and_path(self, api_client, base_url, admin_token):
        png = _tiny_png_bytes()
        files = {"file": ("test.png", io.BytesIO(png), "image/png")}
        r = api_client.post(f"{base_url}/api/upload", files=files, headers=hdr(admin_token), timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert "url" in body and "path" in body
        assert body["url"].startswith("/api/files/")
        assert body["url"].endswith(body["path"])
        # persist path for downstream tests
        pytest._iter5_uploaded_path = body["path"]
        pytest._iter5_uploaded_url = body["url"]

    def test_get_file_returns_image_bytes(self, api_client, base_url, admin_token):
        path = getattr(pytest, "_iter5_uploaded_path", None)
        if not path:
            pytest.skip("upload test did not run first")
        r = api_client.get(f"{base_url}/api/files/{path}", timeout=60)
        assert r.status_code == 200, r.text
        ct = r.headers.get("Content-Type", "")
        assert ct.startswith("image/"), f"unexpected content-type {ct}"
        # PNG magic bytes
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_non_image_rejected(self, api_client, base_url, admin_token):
        files = {"file": ("note.txt", io.BytesIO(b"hello world"), "text/plain")}
        r = api_client.post(f"{base_url}/api/upload", files=files, headers=hdr(admin_token), timeout=30)
        assert r.status_code == 400
        assert "bild" in r.json()["detail"].lower()

    def test_sales_cannot_upload(self, api_client, base_url, sales_token):
        files = {"file": ("t.png", io.BytesIO(_tiny_png_bytes()), "image/png")}
        r = api_client.post(f"{base_url}/api/upload", files=files, headers=hdr(sales_token), timeout=30)
        assert r.status_code == 403

    def test_customer_cannot_upload(self, api_client, base_url, customer_token):
        files = {"file": ("t.png", io.BytesIO(_tiny_png_bytes()), "image/png")}
        r = api_client.post(f"{base_url}/api/upload", files=files, headers=hdr(customer_token), timeout=30)
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# FEATURE 2: Product description + imageUrl persistence
# ---------------------------------------------------------------------------
class TestProductDescriptionAndImage:
    def test_create_and_update_with_description_and_image(self, api_client, base_url, admin_token):
        url = getattr(pytest, "_iter5_uploaded_url", "/api/files/placeholder.png")
        body = {
            "brand": "TEST_Brand5",
            "name": "TEST_Prod5",
            "unit": "kg",
            "standardPrice": 22.0,
            "salesFloor": 20.0,
            "absoluteFloor": 18.0,
            "cost": 13.0,
            "description": "Testbeschreibung für Iteration 5",
            "imageUrl": url,
        }
        c = api_client.post(f"{base_url}/api/products", json=body, headers=hdr(admin_token), timeout=30)
        assert c.status_code == 200, c.text
        p = c.json()
        pid = p["id"]
        assert p["description"] == body["description"]
        assert p["imageUrl"] == body["imageUrl"]
        pytest._iter5_test_pid = pid

        # GET as admin - description + imageUrl round-trip
        g = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert g.status_code == 200
        found = [x for x in g.json() if x["id"] == pid]
        assert found and found[0]["description"] == body["description"]
        assert found[0]["imageUrl"] == body["imageUrl"]

        # PUT update
        upd = dict(body, description="Aktualisierte Beschreibung", standardPrice=23.0)
        u = api_client.put(f"{base_url}/api/products/{pid}", json=upd, headers=hdr(admin_token))
        assert u.status_code == 200
        assert u.json()["description"] == "Aktualisierte Beschreibung"
        assert u.json()["standardPrice"] == 23.0


# ---------------------------------------------------------------------------
# FEATURE 3: DB / Marge hiding on /api/products
# ---------------------------------------------------------------------------
class TestProductRoleHiding:
    def test_admin_sees_cost(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        # every product should include cost
        for p in r.json():
            assert "cost" in p, f"admin missing cost on {p['id']}"
            assert "salesFloor" in p
            assert "absoluteFloor" in p

    def test_sales_has_no_internal_costs_or_floors(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(sales_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"sales unexpectedly sees cost on {p['id']}"
            assert "salesFloor" not in p, f"sales leaked salesFloor on {p['id']}"
            assert "absoluteFloor" not in p, f"sales leaked absoluteFloor on {p['id']}"

    def test_customer_no_cost_no_floors(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(customer_token))
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p
            assert "salesFloor" not in p
            assert "absoluteFloor" not in p


# ---------------------------------------------------------------------------
# FEATURE 4: DB / Marge hiding on /api/analytics
# ---------------------------------------------------------------------------
class TestAnalyticsRoleHiding:
    def test_admin_analytics_has_margin(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/analytics?months=6", headers=hdr(admin_token))
        assert r.status_code == 200
        d = r.json()
        assert d["showMargin"] is True
        assert "totalMargin" in d
        assert "marginPct" in d
        assert len(d["series"]) == 6
        for row in d["series"]:
            assert "margin" in row
            assert "revenue" in row and "kg" in row

    def test_sales_analytics_no_margin(self, api_client, base_url, sales_token):
        r = api_client.get(f"{base_url}/api/analytics?months=6", headers=hdr(sales_token))
        assert r.status_code == 200
        d = r.json()
        assert d["showMargin"] is False
        assert "totalMargin" not in d
        assert "marginPct" not in d
        for row in d["series"]:
            assert "margin" not in row, f"sales row unexpectedly has margin: {row}"
            assert "revenue" in row and "kg" in row

    def test_customer_analytics_forbidden(self, api_client, base_url, customer_token):
        r = api_client.get(f"{base_url}/api/analytics?months=6", headers=hdr(customer_token))
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# REGRESSION
# ---------------------------------------------------------------------------
class TestRegression:
    def test_login_three_roles(self, admin_token, sales_token, customer_token):
        assert admin_token and sales_token and customer_token

    def test_offer_create_and_approve(self, api_client, base_url, admin_token):
        # Below sales floor to force approval
        body = {"companyId": "c1", "items": [{"productId": "p1", "qty": 20, "price": 15.30}], "termMonths": 24}
        c = api_client.post(f"{base_url}/api/offers", json=body, headers=hdr(admin_token))
        assert c.status_code == 200
        offer = c.json()
        assert offer["status"] == "Freigabe nötig"
        oid = offer["id"]
        a = api_client.post(f"{base_url}/api/offers/{oid}/approve", json={"note": "OK"}, headers=hdr(admin_token))
        assert a.status_code == 200
        assert a.json()["status"] == "Freigegeben"

    def test_offer_reject(self, api_client, base_url, admin_token):
        body = {"companyId": "c1", "items": [{"productId": "p1", "qty": 10, "price": 15.30}], "termMonths": 12}
        c = api_client.post(f"{base_url}/api/offers", json=body, headers=hdr(admin_token))
        oid = c.json()["id"]
        r = api_client.post(f"{base_url}/api/offers/{oid}/reject", json={"note": "nope"}, headers=hdr(admin_token))
        assert r.status_code == 200
        assert r.json()["status"] == "Abgelehnt"

    def test_order_status_and_cancel_flow(self, api_client, base_url, admin_token, customer_token):
        # create -> Neu
        r = api_client.post(f"{base_url}/api/orders",
                            json={"companyId": "c1", "items": [{"productId": "p1", "qty": 3, "price": 15.90}]},
                            headers=hdr(admin_token))
        assert r.status_code == 200
        oid = r.json()["id"]
        assert oid.startswith("B-")
        # advance
        adv = api_client.put(f"{base_url}/api/orders/{oid}/status",
                             json={"status": "Bestätigt"}, headers=hdr(admin_token))
        assert adv.status_code == 200
        # cancel now blocked
        cx = api_client.put(f"{base_url}/api/orders/{oid}/cancel", headers=hdr(admin_token))
        assert cx.status_code == 400

        # separate order and cancel
        r2 = api_client.post(f"{base_url}/api/orders",
                             json={"companyId": "c1", "items": [{"productId": "p1", "qty": 2, "price": 15.90}]},
                             headers=hdr(admin_token))
        oid2 = r2.json()["id"]
        cx2 = api_client.put(f"{base_url}/api/orders/{oid2}/cancel", headers=hdr(customer_token))
        assert cx2.status_code == 200
        assert cx2.json()["status"] == "Storniert"

    def test_customer_price_and_history(self, api_client, base_url, admin_token):
        body = {"companyId": "c1", "productId": "p1", "price": 15.75}
        u = api_client.post(f"{base_url}/api/customer-prices", json=body, headers=hdr(admin_token))
        assert u.status_code == 200
        h = api_client.get(f"{base_url}/api/companies/c1/price-history", headers=hdr(admin_token))
        assert h.status_code == 200
        assert isinstance(h.json(), list)
        # restore old price 15.90
        api_client.post(f"{base_url}/api/customer-prices",
                        json={"companyId": "c1", "productId": "p1", "price": 15.90},
                        headers=hdr(admin_token))

    def test_invoice_pay(self, api_client, base_url, admin_token):
        # Use an Offen invoice and pay it, then set it back to Offen via mongo? Just verify endpoint accepts.
        r = api_client.get(f"{base_url}/api/invoices", headers=hdr(admin_token))
        assert r.status_code == 200
        invs = r.json()
        offen = [i for i in invs if i["status"] != "Bezahlt"]
        if not offen:
            pytest.skip("no open invoice to pay")
        inv_id = offen[0]["id"]
        p = api_client.put(f"{base_url}/api/invoices/{inv_id}/pay", headers=hdr(admin_token))
        assert p.status_code == 200
        assert p.json()["status"] == "Bezahlt"
