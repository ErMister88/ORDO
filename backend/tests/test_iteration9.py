"""Iteration 9 – multi-item reorder, offer accept-note, audit log.

Covers:
- POST /api/orders with multi-item cart (customer)
- POST /api/offers/{id}/approve (admin) + POST /api/offers/{id}/accept {note}
  → creates order with customerNote and offer.orderId link
- GET /api/audit (admin only; 403 for sales/customer); entries logged for
  offer.approve, offer.accept, order.status
- Cost invariant: sales & customer never receive `cost` on /api/products
"""
import os
import time
import requests
import pytest
from conftest import hdr


# ----------------------------- CLEANUP HELPERS ------------------------------
@pytest.fixture(scope="module", autouse=True)
def _module_cleanup(api_client, base_url, admin_token):
    """Track things created during this module and clean them up at the end."""
    state = {"orders": set(), "invoices": set(), "restore_offer": "A-2026-0187"}
    yield state

    admin_h = hdr(admin_token)
    # Delete created orders + linked invoices
    for oid in list(state["orders"]):
        try:
            requests.delete(f"{base_url}/api/orders/{oid}", headers=admin_h, timeout=15)
        except Exception:
            pass
    for inv in list(state["invoices"]):
        try:
            requests.delete(f"{base_url}/api/invoices/{inv}", headers=admin_h, timeout=15)
        except Exception:
            pass

    # Restore offer A-2026-0187 to 'Freigabe nötig' (unset orderId + decisionNote)
    # Uses direct DB via a debug endpoint if available; otherwise skipped.
    # We rely on module_admin to do it via a raw pymongo call.
    try:
        from motor.motor_asyncio import AsyncIOMotorClient  # noqa
        import pymongo
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME")
        if mongo_url and db_name:
            c = pymongo.MongoClient(mongo_url)
            db = c[db_name]
            db.offers.update_one(
                {"id": state["restore_offer"]},
                {"$set": {"status": "Freigabe nötig"},
                 "$unset": {"orderId": "", "decisionNote": ""}},
            )
            # Delete orders that came from-Offer or were created here
            for oid in list(state["orders"]):
                doc = db.orders.find_one({"id": oid})
                if doc:
                    inv = doc.get("invoiceId")
                    if inv:
                        db.invoices.delete_one({"id": inv})
                    db.orders.delete_one({"id": oid})
            # Align counters: order → max existing numeric seq, invoice → 1000
            max_seq = 0
            for o in db.orders.find({}, {"id": 1}):
                try:
                    n = int(o["id"].split("-")[-1])
                    if n > max_seq:
                        max_seq = n
                except Exception:
                    pass
            if max_seq:
                db.counters.update_one({"_id": "order"}, {"$set": {"seq": max_seq}}, upsert=True)
            db.counters.update_one({"_id": "invoice"}, {"$set": {"seq": 1000}}, upsert=True)
            c.close()
    except Exception as e:
        print(f"cleanup warn: {e}")


# ============================== TESTS =======================================
class TestCostInvariant:
    """Products cost field must never leak to sales or customer."""

    def test_admin_sees_cost(self, api_client, base_url, admin_token):
        r = requests.get(f"{base_url}/api/products", headers=hdr(admin_token), timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert len(data) >= 1
        assert all("cost" in p for p in data), "Admin must see cost on all products"

    def test_sales_no_cost(self, api_client, base_url, sales_token):
        r = requests.get(f"{base_url}/api/products", headers=hdr(sales_token), timeout=15)
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"cost leaked to sales for {p.get('id')}"

    def test_customer_no_cost(self, api_client, base_url, customer_token):
        r = requests.get(f"{base_url}/api/products", headers=hdr(customer_token), timeout=15)
        assert r.status_code == 200
        for p in r.json():
            assert "cost" not in p, f"cost leaked to customer for {p.get('id')}"
            assert "salesFloor" not in p
            assert "absoluteFloor" not in p


class TestMultiItemReorder:
    """Customer places multi-item order via POST /api/orders."""

    def test_multi_item_order_persists_and_appears_in_history(
        self, api_client, base_url, customer_token, _module_cleanup
    ):
        # Get customer's companyId
        me = requests.get(f"{base_url}/api/auth/me", headers=hdr(customer_token), timeout=15).json()
        assert me["companyId"] == "c1"

        # Get customer prices
        products = requests.get(f"{base_url}/api/products", headers=hdr(customer_token), timeout=15).json()
        assert len(products) >= 2
        p1, p2 = products[0], products[1]

        payload = {
            "companyId": "c1",
            "items": [
                {"productId": p1["id"], "qty": 5, "price": p1["standardPrice"]},
                {"productId": p2["id"], "qty": 3, "price": p2["standardPrice"]},
            ],
        }
        r = requests.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token), timeout=15)
        assert r.status_code == 200, r.text
        order = r.json()
        assert order["id"].startswith("B-")
        assert len(order["items"]) == 2
        assert order["status"] == "Neu"
        _module_cleanup["orders"].add(order["id"])

        # Verify appears in list (GET /orders)
        r2 = requests.get(f"{base_url}/api/orders", headers=hdr(customer_token), timeout=15)
        assert r2.status_code == 200
        ids = [o["id"] for o in r2.json()]
        assert order["id"] in ids

        # GET single order → verify items persisted
        r3 = requests.get(f"{base_url}/api/orders/{order['id']}", headers=hdr(customer_token), timeout=15)
        assert r3.status_code == 200
        o = r3.json()
        assert len(o["items"]) == 2
        assert {i["productId"] for i in o["items"]} == {p1["id"], p2["id"]}
        assert any(i["qty"] == 5 for i in o["items"])
        assert any(i["qty"] == 3 for i in o["items"])


class TestOfferAcceptNote:
    """Admin approves A-2026-0187 → customer accepts with note → order has customerNote."""

    def test_approve_then_accept_with_note_creates_order_note(
        self, api_client, base_url, admin_token, customer_token, _module_cleanup
    ):
        offer_id = "A-2026-0187"

        # Ensure offer exists and is 'Freigabe nötig' (module cleanup restores this)
        offers = requests.get(f"{base_url}/api/offers", headers=hdr(admin_token), timeout=15).json()
        target = next((o for o in offers if o["id"] == offer_id), None)
        assert target is not None, f"Seed offer {offer_id} missing"
        if target["status"] == "Angenommen":
            pytest.skip("Offer already accepted from earlier run (cleanup needed)")

        # If already Freigegeben (previous partial run), skip approval
        if target["status"] != "Freigegeben":
            r_appr = requests.post(
                f"{base_url}/api/offers/{offer_id}/approve",
                json={"note": ""},
                headers=hdr(admin_token),
                timeout=15,
            )
            assert r_appr.status_code == 200, r_appr.text
            assert r_appr.json()["status"] == "Freigegeben"

        # Customer accepts with a note
        NOTE = "TEST_iter9 — bitte vormittags liefern"
        r_acc = requests.post(
            f"{base_url}/api/offers/{offer_id}/accept",
            json={"note": NOTE},
            headers=hdr(customer_token),
            timeout=15,
        )
        assert r_acc.status_code == 200, r_acc.text
        order = r_acc.json()
        assert order["id"].startswith("B-")
        assert order["fromOffer"] == offer_id
        assert order["customerNote"] == NOTE
        _module_cleanup["orders"].add(order["id"])

        # Re-fetch order and verify customerNote persists
        r_get = requests.get(
            f"{base_url}/api/orders/{order['id']}", headers=hdr(customer_token), timeout=15
        )
        assert r_get.status_code == 200
        assert r_get.json()["customerNote"] == NOTE

        # Verify offer is now 'Angenommen' with orderId link
        offers2 = requests.get(f"{base_url}/api/offers", headers=hdr(customer_token), timeout=15).json()
        upd = next(o for o in offers2 if o["id"] == offer_id)
        assert upd["status"] == "Angenommen"
        assert upd["orderId"] == order["id"]

    def test_accept_already_accepted_returns_400(
        self, api_client, base_url, customer_token
    ):
        # After previous test, A-2026-0187 is Angenommen → accepting again → 400
        r = requests.post(
            f"{base_url}/api/offers/A-2026-0187/accept",
            json={"note": "again"},
            headers=hdr(customer_token),
            timeout=15,
        )
        assert r.status_code == 400


class TestAuditLog:
    """/api/audit admin only; entries recorded for sensitive actions."""

    def test_audit_requires_admin(self, api_client, base_url, sales_token, customer_token):
        r_s = requests.get(f"{base_url}/api/audit", headers=hdr(sales_token), timeout=15)
        assert r_s.status_code == 403
        r_c = requests.get(f"{base_url}/api/audit", headers=hdr(customer_token), timeout=15)
        assert r_c.status_code == 403

    def test_audit_lists_entries(self, api_client, base_url, admin_token):
        r = requests.get(f"{base_url}/api/audit", headers=hdr(admin_token), timeout=15)
        assert r.status_code == 200
        rows = r.json()
        assert isinstance(rows, list)
        # After earlier tests, we should see offer.approve, offer.accept, and login
        actions = {row["action"] for row in rows}
        # login must always be present (fixtures logged in)
        assert "login" in actions, f"Expected 'login' audit entry, got {actions}"

    def test_audit_records_offer_approve_and_accept(
        self, api_client, base_url, admin_token, _module_cleanup
    ):
        # Look for entries from the TestOfferAcceptNote test above
        r = requests.get(f"{base_url}/api/audit", headers=hdr(admin_token), timeout=15)
        assert r.status_code == 200
        rows = r.json()
        approve_seen = any(
            row["action"] == "offer.approve" and row.get("entity") == "A-2026-0187"
            for row in rows
        )
        accept_seen = any(
            row["action"] == "offer.accept" and row.get("entity") == "A-2026-0187"
            for row in rows
        )
        assert approve_seen, "offer.approve audit entry missing"
        assert accept_seen, "offer.accept audit entry missing"

    def test_order_status_change_creates_audit_entry(
        self, api_client, base_url, admin_token, customer_token, _module_cleanup
    ):
        # Create an order as customer, then flip status as admin
        products = requests.get(f"{base_url}/api/products", headers=hdr(customer_token), timeout=15).json()
        payload = {
            "companyId": "c1",
            "items": [{"productId": products[0]["id"], "qty": 1, "price": products[0]["standardPrice"]}],
        }
        r = requests.post(f"{base_url}/api/orders", json=payload, headers=hdr(customer_token), timeout=15)
        assert r.status_code == 200
        oid = r.json()["id"]
        _module_cleanup["orders"].add(oid)

        # Admin sets status → Bestätigt
        r_st = requests.put(
            f"{base_url}/api/orders/{oid}/status",
            json={"status": "Bestätigt"},
            headers=hdr(admin_token),
            timeout=15,
        )
        assert r_st.status_code == 200
        time.sleep(0.3)

        # Verify audit entry
        r_a = requests.get(f"{base_url}/api/audit", headers=hdr(admin_token), timeout=15)
        assert r_a.status_code == 200
        rows = r_a.json()
        match = any(
            row["action"] == "order.status"
            and row.get("entity") == oid
            and row.get("meta", {}).get("status") == "Bestätigt"
            for row in rows
        )
        assert match, f"order.status audit entry missing for {oid}"


class TestOrdersSearchFilterDataShape:
    """Backend just returns raw list — filtering happens client-side.
    We just confirm order.id and status fields are present for client filtering.
    """

    def test_orders_have_id_and_status(self, api_client, base_url, customer_token):
        r = requests.get(f"{base_url}/api/orders", headers=hdr(customer_token), timeout=15)
        assert r.status_code == 200
        orders = r.json()
        assert len(orders) >= 1
        for o in orders:
            assert "id" in o and isinstance(o["id"], str) and o["id"].startswith("B-")
            assert "status" in o
            assert "items" in o and isinstance(o["items"], list)
