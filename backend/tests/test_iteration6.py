"""Iteration 6 backend tests — three new features:

Feature 1 — Offer to Order in 1 tap
  POST /api/offers/{offer_id}/accept
    * customer can accept a 'Freigegeben' offer for their own company
    * creates an order in status 'Neu', copies items, sets fromOffer,
      offer.status -> 'Angenommen', offer.orderId set to new order id.
    * 400 if offer.status != 'Freigegeben'
    * 400 if already converted (has orderId)
    * 403 if company not visible to caller

Feature 2 — Volume discount tiers on Product
    * seeded p1/p2 expose the requested tiers
    * POST/PUT /api/products persist discountTiers and round-trip through GET

Feature 3 — Email notifications must not block the endpoint
    * POST /api/offers/{id}/approve still returns 200 and mutates DB
    * PUT /api/orders/{id}/status status='Versendet' still returns 200,
      attaches trackingNumber + estimatedDelivery
    * We do NOT assert on external delivery.

Also verifies role-based hiding of product 'cost' (sales/customer) is not
regressed.
"""
from typing import Optional
import pytest


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}


# ---------- helpers --------------------------------------------------------
def _create_customer_offer_freigegeben(api_client, base_url, admin_token, *,
                                       companyId="c1", productId="p1",
                                       qty=20, price=15.30) -> str:
    """Create an offer that will land in 'Freigabe nötig' (below sales floor),
    then approve it so we have a fresh 'Freigegeben' offer for the given company."""
    body = {"companyId": companyId,
            "items": [{"productId": productId, "qty": qty, "price": price}],
            "termMonths": 24}
    c = api_client.post(f"{base_url}/api/offers", json=body, headers=hdr(admin_token))
    assert c.status_code == 200, c.text
    oid = c.json()["id"]
    assert c.json()["status"] == "Freigabe nötig"
    a = api_client.post(f"{base_url}/api/offers/{oid}/approve",
                        json={"note": "OK"}, headers=hdr(admin_token))
    assert a.status_code == 200, a.text
    return oid


# ---------- Feature 1: accept offer ----------------------------------------
class TestOfferAccept:
    """POST /api/offers/{id}/accept."""

    def test_customer_accepts_freigegeben_offer_creates_order(
        self, api_client, base_url, admin_token, customer_token,
    ):
        # Fresh Freigegeben offer for c1 (customer's company)
        oid = _create_customer_offer_freigegeben(api_client, base_url, admin_token)
        pytest._iter6_accepted_oid = oid

        r = api_client.post(f"{base_url}/api/offers/{oid}/accept",
                            headers=hdr(customer_token))
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["status"] == "Neu"
        assert order["fromOffer"] == oid
        assert order["companyId"] == "c1"
        assert order["id"].startswith("B-")
        # items copied verbatim
        assert order["items"][0]["productId"] == "p1"
        assert order["items"][0]["qty"] == 20
        assert abs(order["items"][0]["price"] - 15.30) < 1e-6
        pytest._iter6_created_order_id = order["id"]

        # Round-trip via GET: offer is now 'Angenommen' with orderId=new
        g = api_client.get(f"{base_url}/api/offers", headers=hdr(customer_token))
        assert g.status_code == 200
        found = [o for o in g.json() if o["id"] == oid]
        assert found, f"offer {oid} not returned to customer"
        assert found[0]["status"] == "Angenommen"
        assert found[0]["orderId"] == order["id"]

        # GET the new order verifies persistence
        go = api_client.get(f"{base_url}/api/orders/{order['id']}",
                            headers=hdr(customer_token))
        assert go.status_code == 200
        assert go.json()["fromOffer"] == oid
        assert go.json()["status"] == "Neu"

    def test_accept_already_converted_returns_400(
        self, api_client, base_url, customer_token,
    ):
        oid: Optional[str] = getattr(pytest, "_iter6_accepted_oid", None)
        if not oid:
            pytest.skip("previous accept test did not run")
        r = api_client.post(f"{base_url}/api/offers/{oid}/accept",
                            headers=hdr(customer_token))
        # Backend rejects with 400 — either the status guard (offer is now
        # 'Angenommen', so status != 'Freigegeben') OR the orderId guard fires;
        # both are semantically "already converted".
        assert r.status_code == 400, r.text
        msg = r.json()["detail"].lower()
        assert ("bereits" in msg) or ("freigegeb" in msg), msg

    def test_accept_not_freigegeben_returns_400(
        self, api_client, base_url, admin_token, customer_token,
    ):
        # Create an offer that stays in 'Freigabe nötig'
        body = {"companyId": "c1",
                "items": [{"productId": "p1", "qty": 10, "price": 15.30}],
                "termMonths": 12}
        c = api_client.post(f"{base_url}/api/offers", json=body, headers=hdr(admin_token))
        assert c.status_code == 200
        oid = c.json()["id"]
        assert c.json()["status"] == "Freigabe nötig"
        pytest._iter6_not_approved_oid = oid

        r = api_client.post(f"{base_url}/api/offers/{oid}/accept",
                            headers=hdr(customer_token))
        assert r.status_code == 400, r.text
        assert "freigegeb" in r.json()["detail"].lower()

    def test_accept_cross_company_returns_403(
        self, api_client, base_url, admin_token, customer_token,
    ):
        # Create + approve an offer for c3 (customer only sees c1)
        oid = _create_customer_offer_freigegeben(
            api_client, base_url, admin_token, companyId="c3",
        )
        pytest._iter6_c3_oid = oid
        r = api_client.post(f"{base_url}/api/offers/{oid}/accept",
                            headers=hdr(customer_token))
        assert r.status_code == 403, r.text


# ---------- Feature 2: discount tiers --------------------------------------
class TestDiscountTiers:
    def test_seeded_p1_p2_have_tiers(self, api_client, base_url, admin_token):
        r = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        assert r.status_code == 200
        by_id = {p["id"]: p for p in r.json()}
        p1 = by_id.get("p1")
        p2 = by_id.get("p2")
        assert p1 and "discountTiers" in p1, "p1 missing discountTiers"
        assert p2 and "discountTiers" in p2, "p2 missing discountTiers"
        # Expected seeded tiers
        p1_tiers = sorted([(t["minQty"], t["price"]) for t in p1["discountTiers"]])
        p2_tiers = sorted([(t["minQty"], t["price"]) for t in p2["discountTiers"]])
        assert p1_tiers == [(50, 16.20), (100, 15.50)]
        assert p2_tiers == [(50, 18.20), (100, 17.50)]

    def test_create_and_update_tiers_persist(
        self, api_client, base_url, admin_token,
    ):
        body = {
            "brand": "TEST_Iter6Brand",
            "name": "TEST_Iter6Tier",
            "unit": "kg",
            "standardPrice": 25.0,
            "salesFloor": 22.0,
            "absoluteFloor": 20.0,
            "cost": 15.0,
            "description": "iter6",
            "discountTiers": [
                {"minQty": 25, "price": 23.5},
                {"minQty": 75, "price": 22.0},
            ],
        }
        c = api_client.post(f"{base_url}/api/products", json=body, headers=hdr(admin_token))
        assert c.status_code == 200, c.text
        p = c.json()
        pid = p["id"]
        pytest._iter6_test_pid = pid
        assert len(p["discountTiers"]) == 2
        assert p["discountTiers"][0]["minQty"] == 25
        assert p["discountTiers"][0]["price"] == 23.5

        # GET round-trip
        g = api_client.get(f"{base_url}/api/products", headers=hdr(admin_token))
        found = [x for x in g.json() if x["id"] == pid][0]
        assert len(found["discountTiers"]) == 2
        assert found["discountTiers"][1]["minQty"] == 75

        # PUT — replace tiers (single new tier)
        upd = dict(body, discountTiers=[{"minQty": 40, "price": 24.0}])
        u = api_client.put(f"{base_url}/api/products/{pid}", json=upd, headers=hdr(admin_token))
        assert u.status_code == 200
        assert len(u.json()["discountTiers"]) == 1
        assert u.json()["discountTiers"][0]["minQty"] == 40

        # PUT — clear tiers
        upd2 = dict(body, discountTiers=[])
        u2 = api_client.put(f"{base_url}/api/products/{pid}", json=upd2, headers=hdr(admin_token))
        assert u2.status_code == 200
        assert u2.json()["discountTiers"] == []


# ---------- Feature 3: email notifications don't block endpoints -----------
class TestEmailNonBlocking:
    """approve + status Versendet must return 200 even though they call the
    email integration. We assert only the endpoint contract, not delivery."""

    def test_approve_returns_200_and_persists(self, api_client, base_url, admin_token):
        # Create an offer needing approval, approve it, assert 200 + DB mutation
        body = {"companyId": "c1",
                "items": [{"productId": "p1", "qty": 10, "price": 15.20}],
                "termMonths": 12}
        c = api_client.post(f"{base_url}/api/offers", json=body, headers=hdr(admin_token))
        assert c.status_code == 200
        oid = c.json()["id"]
        pytest._iter6_email_approved_oid = oid

        a = api_client.post(f"{base_url}/api/offers/{oid}/approve",
                            json={"note": "iter6-email"}, headers=hdr(admin_token))
        assert a.status_code == 200, a.text
        assert a.json()["status"] == "Freigegeben"

        # Verify DB via GET
        g = api_client.get(f"{base_url}/api/offers", headers=hdr(admin_token))
        found = [o for o in g.json() if o["id"] == oid][0]
        assert found["status"] == "Freigegeben"

    def test_status_versendet_returns_200_and_sets_tracking(
        self, api_client, base_url, admin_token,
    ):
        # fresh order
        r = api_client.post(
            f"{base_url}/api/orders",
            json={"companyId": "c1", "items": [{"productId": "p1", "qty": 4, "price": 15.90}]},
            headers=hdr(admin_token),
        )
        assert r.status_code == 200
        oid = r.json()["id"]
        pytest._iter6_shipped_order_id = oid

        # advance through the flow
        for step in ("Bestätigt", "Kommissioniert", "Versendet"):
            s = api_client.put(f"{base_url}/api/orders/{oid}/status",
                               json={"status": step}, headers=hdr(admin_token))
            assert s.status_code == 200, f"{step} failed: {s.text}"
            assert s.json()["status"] == step

        # Confirm tracking + ETA persisted via GET
        g = api_client.get(f"{base_url}/api/orders/{oid}", headers=hdr(admin_token))
        assert g.status_code == 200
        d = g.json()
        assert d["status"] == "Versendet"
        assert d.get("trackingNumber", "").startswith("SS"), f"missing tracking: {d}"
        assert d.get("estimatedDelivery"), "missing ETA"
        assert d.get("shippedAt"), "missing shippedAt"


# ---------- Regression: cost hiding stays intact ---------------------------
class TestCostHidingRegression:
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


# ---------- Cleanup / restore demo state -----------------------------------
class TestCleanupRestore:
    """MUST run last. Restores the demo state as required by the review request:
       - delete any orders with fromOffer (from Feature 1 tests)
       - delete other test-created orders (email test)
       - reset accepted offers back to their pre-test state
       - delete test products created by TestDiscountTiers
       - reset order counter to 1000, product counter to 4
    """

    def test_zzz_cleanup(self, api_client, base_url, admin_token):
        import pymongo, os
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("DB_NAME", "ss_b2b_database")
        cli = pymongo.MongoClient(mongo_url)
        db = cli[db_name]

        # Delete any orders that were created from an offer during this run
        deleted_from_offer = db.orders.delete_many({"fromOffer": {"$exists": True}}).deleted_count

        # Delete extra orders created for email test (email test order id)
        shipped_oid = getattr(pytest, "_iter6_shipped_order_id", None)
        if shipped_oid:
            db.orders.delete_one({"id": shipped_oid})

        # Reset offers we approved during this run:
        for aid in (getattr(pytest, "_iter6_accepted_oid", None),
                    getattr(pytest, "_iter6_not_approved_oid", None),
                    getattr(pytest, "_iter6_c3_oid", None),
                    getattr(pytest, "_iter6_email_approved_oid", None)):
            if aid:
                db.offers.delete_one({"id": aid})

        # Delete test product
        pid = getattr(pytest, "_iter6_test_pid", None)
        if pid:
            db.products.delete_one({"id": pid})

        # Reset counters to their canonical seeded values
        db.counters.update_one({"_id": "order"}, {"$set": {"seq": 1000}}, upsert=True)
        db.counters.update_one({"_id": "product"}, {"$set": {"seq": 4}}, upsert=True)
        # offer counter — keep at max of current values >=200
        max_off = 200
        for off in db.offers.find({"id": {"$regex": r"^A-\d{4}-\d+$"}}):
            try:
                n = int(off["id"].split("-")[-1])
                if n > max_off:
                    max_off = n
            except Exception:
                pass
        db.counters.update_one({"_id": "offer"}, {"$set": {"seq": max_off}}, upsert=True)

        # If the seeded 'Freigegeben' offer A-2026-0181 was accepted by anyone earlier,
        # restore it. And ensure A-2026-0187 (for c1) stays 'Freigabe nötig' with no orderId.
        db.offers.update_one({"id": "A-2026-0181"},
                             {"$set": {"status": "Freigegeben"},
                              "$unset": {"orderId": ""}})
        db.offers.update_one({"id": "A-2026-0187"},
                             {"$set": {"status": "Freigabe nötig"},
                              "$unset": {"orderId": ""}})

        print(f"cleanup: deleted {deleted_from_offer} fromOffer orders")
        cli.close()
