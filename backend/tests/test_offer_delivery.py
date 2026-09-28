from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_offer_delivery")
os.environ.setdefault("JWT_SECRET", "test-only-offer-delivery-secret")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_URL", "https://app.example.test")

import pytest
from fastapi import HTTPException

from app.models import OfferDeliveryIn, OfferDocumentIn, OfferPublicLinkIn
from app.email_provider import ProviderReceipt
from app.emailer import deliver_outbox_email, send_email
from app.offer_documents import build_offer_document_snapshot, public_offer_payload, render_offer_pdf
from app.routers import offer_delivery
from app.runtime_security import ABUSE_RULES
from app.migrations.versions import v0017_offer_delivery as migration17
from test_tenant_business_access import AsyncDatabase, TENANT_A, TENANT_B, access, principal, run


def offer_row(*, offer_id="A-2026-0001", creator="sales-a", company_id=None) -> dict:
    recipient = {
        "name": "Ristorante Roma", "contactName": "Ada Rossi",
        "email": "ada@example.test", "city": "Roma", "country": "IT",
    }
    return {
        "id": offer_id,
        "companyId": company_id,
        "offerKind": "customer" if company_id else "prospect",
        "createdBy": creator,
        "status": "Freigegeben",
        "items": [{
            "snapshotVersion": 1, "productId": "p-1", "sku": "ESP-1",
            "productName": "Espresso Original", "description": "Snapshot text",
            "unit": "kg", "qty": 2, "price": 20.0, "unitPriceMinor": 2000,
            "lineTotalMinor": 4000, "currency": "EUR", "taxRate": 7,
            "discountMinor": 0, "discountPercent": 0, "priceSource": "offer_manual",
            "costMinor": 900, "baseUnitPriceMinor": 2100,
        }],
        "currency": "EUR", "netTotalMinor": 4000, "snapshotVersion": 1,
        "recipientSnapshot": recipient,
        "companySnapshot": {"name": "Ristorante Roma", "email": "changed@example.test"},
        "billingAddressSnapshot": {
            "street": "Via Roma", "houseNumber": "1", "zip": "00100",
            "city": "Roma", "country": "IT",
        },
        "createdAt": "2026-09-28T10:00:00+00:00",
    }


def test_document_snapshot_and_pdf_exclude_internal_economics():
    snapshot = build_offer_document_snapshot(offer_row(), locale="de")
    pdf = render_offer_pdf(snapshot)

    assert pdf.startswith(b"%PDF-1.4")
    assert snapshot["recipient"]["name"] == "Ristorante Roma"
    assert snapshot["items"][0]["unitPriceMinor"] == 2000
    assert snapshot["totals"] == {"netMinor": 4000, "taxMinor": 280, "grossMinor": 4280}
    rendered = pdf.decode("cp1252", errors="ignore")
    for forbidden in ("costMinor", "baseUnitPriceMinor", "priceFloor", "margin", "provision"):
        assert forbidden not in str(snapshot)
        assert forbidden not in rendered


def test_document_finalization_is_immutable_after_master_data_changes():
    database = AsyncDatabase("offer_document_snapshot")
    scoped = access(database, TENANT_A, actor_user_id="admin-a", role="admin")
    actor = principal(scoped)
    run(scoped.offers.insert_one(offer_row()))

    first = run(offer_delivery.finalize_offer_document(
        "A-2026-0001", OfferDocumentIn(locale="de"), actor, scoped,
    ))
    run(scoped.offers.update_one(
        {"id": "A-2026-0001"},
        {"$set": {
            "recipientSnapshot.name": "Changed customer",
            "items.0.productName": "Changed product",
            "items.0.unitPriceMinor": 9999,
        }},
    ))
    second = run(offer_delivery.finalize_offer_document(
        "A-2026-0001", OfferDocumentIn(locale="en"), actor, scoped,
    ))
    stored = database.raw.offers.find_one({"id": "A-2026-0001"})["documentSnapshot"]

    assert first["storageMode"] == "secure_on_demand"
    assert second["locale"] == "de"
    assert stored["recipient"]["name"] == "Ristorante Roma"
    assert stored["items"][0]["productName"] == "Espresso Original"
    assert stored["items"][0]["unitPriceMinor"] == 2000


@pytest.mark.parametrize("role,creator", [("admin", "admin-a"), ("sales", "sales-a")])
def test_admin_and_sales_can_create_secure_link_for_own_offer(monkeypatch, role, creator):
    database = AsyncDatabase(f"offer_link_{role}")
    scoped = access(database, TENANT_A, actor_user_id=creator, role=role)
    actor = principal(scoped)
    run(scoped.offers.insert_one(offer_row(creator=creator)))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_URL", "https://app.example.test")
    monkeypatch.setenv("JWT_SECRET", "test-only-offer-delivery-secret-with-32-bytes")

    result = run(offer_delivery.create_offer_public_link(
        "A-2026-0001", OfferPublicLinkIn(locale="it", expiresInDays=14), actor, scoped,
    ))
    token = urlparse(result["url"]).path.rsplit("/", 1)[-1]
    stored = database.raw.offers.find_one({"id": "A-2026-0001"})

    assert len(token) >= 32
    assert token not in str(stored)
    assert stored["publicAccess"]["tokenHash"] == offer_delivery._token_hash(token)
    assert stored["tenantId"] == TENANT_A
    assert stored["documentSnapshot"]["locale"] == "it"


def test_retry_reuses_delivery_link_without_resetting_viewed_status(monkeypatch):
    database = AsyncDatabase("offer_link_retry")
    scoped = access(database, TENANT_A, actor_user_id="admin-a", role="admin")
    actor = principal(scoped)
    row = offer_row(creator="admin-a")
    run(scoped.offers.insert_one(row))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_URL", "https://app.example.test")

    token = "deterministic-token-value-with-more-than-32-characters"
    first_url, first_link = run(offer_delivery._ensure_active_link(
        row, scoped, locale="de", expires_in_days=30, actor=actor,
        token=token, link_id="olnk_retry",
    ))
    run(scoped.offers.update_one({"id": row["id"]}, {"$set": {"responseStatus": "VIEWED"}}))
    current = run(scoped.offers.find_one({"id": row["id"]}))
    second_url, second_link = run(offer_delivery._ensure_active_link(
        current, scoped, locale="de", expires_in_days=30, actor=actor,
        token=token, link_id="olnk_retry",
    ))

    stored = database.raw.offers.find_one({"id": row["id"]})
    assert second_url == first_url
    assert second_link["id"] == first_link["id"]
    assert second_link["tokenHash"] == first_link["tokenHash"]
    assert abs((second_link["createdAt"] - first_link["createdAt"]).total_seconds()) < 0.001
    assert stored["responseStatus"] == "VIEWED"


def test_sales_cannot_create_link_for_foreign_prospect():
    database = AsyncDatabase("offer_sales_isolation")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.offers.insert_one(offer_row(creator="sales-b")))

    with pytest.raises(HTTPException) as denied:
        run(offer_delivery.create_offer_public_link(
            "A-2026-0001", OfferPublicLinkIn(), principal(scoped), scoped,
        ))
    assert denied.value.status_code == 404


def _linked_offer(database: AsyncDatabase, *, token="secure-token-value-with-more-than-32-characters", tenant=TENANT_A):
    scoped = access(database, tenant)
    row = offer_row()
    row["documentSnapshot"] = build_offer_document_snapshot(row, locale="de")
    row["deliveryStatus"] = "READY"
    row["responseStatus"] = "OPEN"
    row["publicAccess"] = {
        "id": "olnk_test", "tokenHash": offer_delivery._token_hash(token),
        "createdAt": datetime.now(timezone.utc),
        "expiresAt": datetime.now(timezone.utc) + timedelta(days=2),
        "revokedAt": None,
    }
    run(scoped.offers.insert_one(row))
    return scoped, token


def test_public_view_and_accept_are_redacted_idempotent_and_do_not_create_order():
    database = AsyncDatabase("offer_public_accept")
    scoped, token = _linked_offer(database)

    viewed = run(offer_delivery.view_public_offer(token, scoped))
    accepted = run(offer_delivery.accept_public_offer(token, scoped))
    replay = run(offer_delivery.accept_public_offer(token, scoped))

    assert viewed["responseStatus"] == "VIEWED"
    assert accepted == {"ok": True, "status": "ACCEPTED", "idempotentReplay": False}
    assert replay == {"ok": True, "status": "ACCEPTED", "idempotentReplay": True}
    assert database.raw.orders.count_documents({}) == 0
    assert database.raw.invoices.count_documents({}) == 0
    stored = database.raw.offers.find_one({"id": "A-2026-0001"})
    assert stored["acceptanceSnapshot"]["tokenReference"] == "olnk_test"
    exposed = str(viewed)
    for forbidden in ("tenantId", "companyId", "productId", "costMinor", "priceFloor", "salesAttribution"):
        assert forbidden not in exposed


def test_public_decline_is_idempotent_and_blocks_later_acceptance():
    database = AsyncDatabase("offer_public_decline")
    scoped, token = _linked_offer(database)
    first = run(offer_delivery.decline_public_offer(token, scoped))
    replay = run(offer_delivery.decline_public_offer(token, scoped))
    with pytest.raises(HTTPException) as conflict:
        run(offer_delivery.accept_public_offer(token, scoped))
    assert first["status"] == replay["status"] == "DECLINED"
    assert replay["idempotentReplay"] is True
    assert conflict.value.status_code == 409


def test_parallel_public_acceptance_has_one_economic_transition():
    database = AsyncDatabase("offer_public_parallel_accept")
    scoped, token = _linked_offer(database)

    async def accept_twice():
        return await asyncio.gather(
            offer_delivery.accept_public_offer(token, scoped),
            offer_delivery.accept_public_offer(token, scoped),
        )

    results = run(accept_twice())
    assert sorted(result["idempotentReplay"] for result in results) == [False, True]
    assert database.raw.audit_log.count_documents({"action": "offer.public.accept"}) == 1
    assert database.raw.orders.count_documents({}) == 0
    assert database.raw.invoices.count_documents({}) == 0


@pytest.mark.parametrize("state", ["revoked", "expired"])
def test_public_token_revoked_or_expired_fails_closed(state):
    database = AsyncDatabase(f"offer_public_{state}")
    scoped, token = _linked_offer(database)
    update = (
        {"publicAccess.revokedAt": datetime.now(timezone.utc)}
        if state == "revoked"
        else {"publicAccess.expiresAt": datetime.now(timezone.utc) - timedelta(seconds=1)}
    )
    run(scoped.offers.update_one({"id": "A-2026-0001"}, {"$set": update}))
    with pytest.raises(HTTPException) as invalid:
        run(offer_delivery.view_public_offer(token, scoped))
    assert invalid.value.status_code == 410


def test_token_is_tenant_bound_and_invalid_token_is_indistinguishable():
    database = AsyncDatabase("offer_public_tenant")
    _tenant_a, token = _linked_offer(database, tenant=TENANT_A)
    tenant_b = access(database, TENANT_B)
    with pytest.raises(HTTPException) as foreign:
        run(offer_delivery.view_public_offer(token, tenant_b))
    with pytest.raises(HTTPException) as invalid:
        run(offer_delivery.view_public_offer("x" * 48, tenant_b))
    assert foreign.value.status_code == invalid.value.status_code == 404
    assert foreign.value.detail == invalid.value.detail


def test_public_offer_routes_are_covered_by_abuse_protection():
    assert any(rule.matches("GET", "/api/public/offers/token-value") for rule in ABUSE_RULES)
    assert any(rule.matches("GET", "/api/public/offers/token-value/pdf") for rule in ABUSE_RULES)
    assert any(rule.matches("POST", "/api/public/offers/token-value/accept") for rule in ABUSE_RULES)
    assert any(rule.matches("POST", "/api/public/offers/token-value/decline") for rule in ABUSE_RULES)


def test_public_app_origin_rejects_paths(monkeypatch):
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("APP_URL", "https://app.example.test/unexpected")
    with pytest.raises(HTTPException) as invalid:
        offer_delivery._app_origin()
    assert invalid.value.status_code == 503


def test_email_not_configured_is_explicit_and_writes_no_outbox(monkeypatch):
    database = AsyncDatabase("offer_email_disabled")
    scoped = access(database, TENANT_A, actor_user_id="admin-a", role="admin")
    database.raw.idempotency_records.create_index(
        [("tenantId", 1), ("operation", 1), ("actorId", 1), ("key", 1)], unique=True,
    )
    run(scoped.offers.insert_one(offer_row(creator="admin-a")))
    monkeypatch.setattr(offer_delivery, "email_configuration_status", lambda: ("not_configured", "disabled"))

    with pytest.raises(HTTPException) as unavailable:
        run(offer_delivery.send_offer_document(
            "A-2026-0001", OfferDeliveryIn(), principal(scoped), scoped, "offer-send-disabled-0001",
        ))
    assert unavailable.value.status_code == 503
    assert database.raw.email_outbox.count_documents({}) == 0


def test_email_send_uses_outbox_idempotently_and_can_save_delivery_address(monkeypatch):
    database = AsyncDatabase("offer_email_ready")
    scoped = access(database, TENANT_A, actor_user_id="admin-a", role="admin")
    database.raw.idempotency_records.create_index(
        [("tenantId", 1), ("operation", 1), ("actorId", 1), ("key", 1)], unique=True,
    )
    run(scoped.offers.insert_one(offer_row(creator="admin-a")))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("APP_URL", "https://app.example.test")
    monkeypatch.setenv("JWT_SECRET", "test-only-offer-delivery-secret-with-32-bytes")
    monkeypatch.setattr(offer_delivery, "email_configuration_status", lambda: ("available", "ok"))

    calls = []
    async def fake_send_email(**kwargs):
        calls.append(kwargs)
        return {"id": "mail_test", "status": "pending"}
    monkeypatch.setattr(offer_delivery, "send_email", fake_send_email)

    body = OfferDeliveryIn(email="new@example.test", locale="en", saveRecipientEmail=True)
    first = run(offer_delivery.send_offer_document(
        "A-2026-0001", body, principal(scoped), scoped, "offer-send-ready-0001",
    ))
    replay = run(offer_delivery.send_offer_document(
        "A-2026-0001", body, principal(scoped), scoped, "offer-send-ready-0001",
    ))
    stored = database.raw.offers.find_one({"id": "A-2026-0001"})

    assert first == replay == {"ok": True, "status": "READY", "outboxId": "mail_test"}
    assert len(calls) == 1
    assert calls[0]["template_key"] == "offer.delivery"
    assert stored["deliveryRecipient"] == {"email": "new@example.test", "savedForLater": True}
    assert stored["recipientSnapshot"]["email"] == "ada@example.test"


def test_offer_outbox_failure_retry_and_duplicate_delivery_are_safe(monkeypatch):
    database = AsyncDatabase("offer_email_worker_status")
    scoped = access(database, TENANT_A, actor_user_id="admin-a", role="admin")
    database.raw.email_outbox.create_index(
        [("tenantId", 1), ("deduplicationKey", 1)], unique=True,
    )
    run(scoped.offers.insert_one(offer_row(creator="admin-a")))

    class Provider:
        name = "mock"
        fail = True
        calls = 0

        def send(self, _message):
            self.calls += 1
            if self.fail:
                raise RuntimeError("mock delivery failure")
            return ProviderReceipt("provider-message-1")

    provider = Provider()
    monkeypatch.setattr("app.emailer.get_email_provider", lambda: provider)
    row = run(send_email(
        access=scoped, to="recipient@example.test", subject="Ihr Angebot",
        html="<p>Offer delivery</p>", idempotency_key="offer:A-2026-0001:delivery:test",
        template_key="offer.delivery", resource_type="offer", resource_id="A-2026-0001",
    ))
    assert database.raw.offers.find_one({"id": "A-2026-0001"})["deliveryStatus"] == "READY"

    with pytest.raises(RuntimeError):
        run(deliver_outbox_email(scoped, row["id"], attempt=1))
    assert database.raw.offers.find_one({"id": "A-2026-0001"})["deliveryStatus"] == "DELIVERY_FAILED"

    provider.fail = False
    run(deliver_outbox_email(scoped, row["id"], attempt=2))
    run(deliver_outbox_email(scoped, row["id"], attempt=3))
    assert provider.calls == 2
    assert database.raw.email_outbox.count_documents({}) == 1
    assert database.raw.offers.find_one({"id": "A-2026-0001"})["deliveryStatus"] == "SENT"


def test_public_payload_rejects_unfinalized_offer():
    with pytest.raises(Exception):
        public_offer_payload(offer_row())


def test_offer_delivery_migration_is_additive():
    plan = migration17.inspect(None)
    assert plan.expected_changes == {"indexesToEnsure": 2, "documentsChanged": 0}
    assert migration17.MIGRATION.version == 17
