from __future__ import annotations

import os

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_final_product_review")
os.environ.setdefault("JWT_SECRET", "test-only-final-review-secret")
os.environ.setdefault("APP_ENV", "test")

import pytest
import mongomock
from fastapi import HTTPException

from app.models import CompanyCreateIn, OfferCreate, OfferCustomerCreateIn, OfferItemIn
from app.routers import companies, offers
from app.pricing_engine import PricingEngine
from app.migrations.registry import get_migrations
from app.migrations.versions import v0014_final_product_review as migration14
from test_tenant_business_access import AsyncDatabase, TENANT_A, access, principal, run


def product() -> dict:
    return {
        "id": "p1", "brand": "ORDO", "name": "Espresso", "unit": "kg",
        "active": True, "b2bAvailable": True, "taxRate": 7,
        "standardPrice": 20.0, "standardPriceMinor": 2000,
        "salesFloor": 17.0, "salesFloorMinor": 1700,
        "absoluteFloor": 15.0, "absoluteFloorMinor": 1500,
        "cost": 10.0, "costMinor": 1000, "currency": "EUR",
    }


def test_prospect_quote_uses_only_b2b_standard_price():
    database = AsyncDatabase("final_review_prospect_price")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.products.insert_one(product()))
    run(scoped.pricing_promotions.insert_one({
        "id": "promo", "productId": "p1", "companyId": None,
        "price": 12.0, "priceMinor": 1200, "currency": "EUR",
        "active": True, "startsAt": "2020-01-01T00:00:00+00:00",
        "endsAt": "2030-01-01T00:00:00+00:00",
    }))

    _stored, quote = run(PricingEngine(scoped).quote_b2b_prospect("p1", 5))

    assert quote.final_unit_price_minor == 2000
    assert quote.price_source == "b2b_standard"
    assert quote.promotion is None


def test_sales_can_create_prospect_offer_with_immutable_recipient_snapshot(monkeypatch):
    database = AsyncDatabase("final_review_prospect_offer")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.products.insert_one(product()))

    async def sequence(_name):
        return 14

    monkeypatch.setattr(offers, "next_seq", sequence)
    response = run(offers.create_offer(
        OfferCreate(
            prospectRecipient={
                "name": "Prospect GmbH", "email": "PROSPECT@example.test",
                "street": "Testweg", "zip": "10115", "city": "Berlin",
            },
            items=[OfferItemIn(productId="p1", qty=10, price=18)],
        ),
        principal(scoped),
        scoped,
    ))

    stored = database.raw.offers.find_one({"id": response["id"]})
    assert stored["tenantId"] == TENANT_A
    assert stored["companyId"] is None
    assert stored["offerKind"] == "prospect"
    assert stored["recipientSnapshot"]["name"] == "Prospect GmbH"
    assert stored["recipientSnapshot"]["email"] == "prospect@example.test"
    assert stored["billingAddressSnapshot"]["city"] == "Berlin"
    assert stored["items"][0]["baseUnitPriceMinor"] == 2000
    assert "costMinor" not in response["items"][0]


def test_prospect_offer_rejects_whitespace_only_recipient(monkeypatch):
    database = AsyncDatabase("final_review_empty_prospect")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.products.insert_one(product()))

    async def sequence(_name):
        return 15

    monkeypatch.setattr(offers, "next_seq", sequence)
    with pytest.raises(HTTPException) as invalid:
        run(offers.create_offer(
            OfferCreate(
                prospectRecipient={"name": "   "},
                items=[OfferItemIn(productId="p1", qty=10, price=18)],
            ),
            principal(scoped), scoped,
        ))

    assert invalid.value.status_code == 400
    assert database.raw.offers.count_documents({}) == 0


def test_sales_only_lists_own_prospect_offers():
    database = AsyncDatabase("final_review_prospect_visibility")
    sales_a = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    sales_b = access(database, TENANT_A, actor_user_id="sales-b", role="sales")
    admin = access(database, TENANT_A, actor_user_id="admin", role="admin")
    for offer_id, creator in (("own", "sales-a"), ("foreign", "sales-b")):
        run(admin.offers.insert_one({
            "id": offer_id, "companyId": None, "createdBy": creator,
            "offerKind": "prospect",
            "createdAt": offer_id, "recipientSnapshot": {"name": offer_id},
            "items": [], "currency": "EUR",
        }))
    run(admin.offers.insert_one({
        "id": "legacy-malformed", "companyId": None, "createdBy": "sales-a",
        "createdAt": "legacy", "items": [], "currency": "EUR",
    }))

    own = run(offers.get_offers(principal(sales_a), sales_a))
    foreign = run(offers.get_offers(principal(sales_b), sales_b))
    all_rows = run(offers.get_offers(principal(admin), admin))

    assert {row["id"] for row in own} == {"own"}
    assert {row["id"] for row in foreign} == {"foreign"}
    assert {row["id"] for row in all_rows} == {"own", "foreign"}


def test_concurrent_offer_conversion_returns_customer_created_by_other_runner(monkeypatch):
    database = AsyncDatabase("final_review_offer_conversion_race")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    actor = principal(scoped)
    run(scoped.offers.insert_one({
        "id": "A-race", "companyId": None, "offerKind": "prospect",
        "createdBy": "sales-a", "recipientSnapshot": {"name": "Race Bar"},
        "items": [], "currency": "EUR",
    }))

    async def concurrent_create(*_args, **_kwargs):
        await scoped.companies.insert_one({
            "id": "c-race", "name": "Race Bar", "sourceOfferId": "A-race",
            "sourceOfferKey": f"{TENANT_A}:A-race", "active": True,
        })
        raise HTTPException(status_code=409, detail={"code": "potential_customer_duplicate"})

    async def no_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(offers, "create_company_record", concurrent_create)
    monkeypatch.setattr(offers, "tenant_audit", no_side_effect)
    result = run(offers.convert_offer_recipient_to_customer(
        "A-race", OfferCustomerCreateIn(name="Race Bar"), actor, scoped,
    ))

    assert result["id"] == "c-race"
    assert database.raw.offers.find_one({"id": "A-race"})["companyId"] == "c-race"


def test_duplicate_warning_is_tenant_scoped_and_requires_confirmation(monkeypatch):
    database = AsyncDatabase("final_review_duplicates")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.companies.insert_one({
        "id": "existing", "name": "Ristorante Roma", "email": "roma@example.test",
        "vatId": "IT123", "active": True,
    }))

    with pytest.raises(HTTPException) as duplicate:
        run(companies.create_company_record(
            CompanyCreateIn(name="  ristorante   roma  ", email="ROMA@example.test"),
            principal(scoped), scoped,
        ))
    assert duplicate.value.status_code == 409
    assert duplicate.value.detail["code"] == "potential_customer_duplicate"


def test_sales_duplicate_warning_does_not_disclose_another_salespersons_customer():
    database = AsyncDatabase("final_review_duplicate_visibility")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.companies.insert_one({
        "id": "foreign", "name": "Hidden Customer", "city": "Secret City",
        "email": "hidden@example.test", "vatId": "", "active": True,
        "assignedSalesRepId": "sales-b",
    }))

    result = run(companies.check_company_duplicates(
        CompanyCreateIn(name="Hidden Customer", email="hidden@example.test"),
        principal(scoped), scoped,
    ))

    assert len(result["matches"]) == 1
    assert result["matches"][0]["restricted"] is True
    assert result["matches"][0]["name"] == "Möglicherweise bereits vorhanden"
    assert result["matches"][0]["city"] == ""
    assert "foreign" not in str(result)
    assert "Secret City" not in str(result)


def test_offer_to_customer_preserves_snapshot_and_links_company(monkeypatch):
    database = AsyncDatabase("final_review_offer_conversion")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    actor = principal(scoped)
    run(scoped.offers.insert_one({
        "id": "A-1", "companyId": None, "createdBy": "sales-a",
        "recipientSnapshot": {
            "name": "Nuovo Bar", "email": "bar@example.test", "phone": "123",
            "street": "Via Roma", "houseNumber": "1", "zip": "39100",
            "city": "Bolzano", "country": "IT", "vatId": "IT999",
        },
        "items": [], "currency": "EUR",
    }))

    async def no_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(companies, "record_customer_activity", no_side_effect)
    monkeypatch.setattr(companies, "tenant_audit", no_side_effect)
    monkeypatch.setattr(offers, "tenant_audit", no_side_effect)
    result = run(offers.convert_offer_recipient_to_customer(
        "A-1", OfferCustomerCreateIn(name="Nuovo Bar"), actor, scoped,
    ))

    stored_offer = database.raw.offers.find_one({"id": "A-1"})
    stored_company = database.raw.companies.find_one({"id": result["id"]})
    assert stored_offer["companyId"] == result["id"]
    assert stored_offer["recipientSnapshot"]["name"] == "Nuovo Bar"
    assert stored_company["sourceOfferId"] == "A-1"
    assert stored_company["sourceOfferKey"] == f"{TENANT_A}:A-1"
    assert stored_company["assignedSalesRepId"] == "sales-a"
    assert database.raw.customer_addresses.find_one({
        "companyId": result["id"]
    })["city"] == "Bolzano"


def test_sales_cannot_convert_another_salespersons_prospect():
    database = AsyncDatabase("final_review_offer_conversion_scope")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    run(scoped.offers.insert_one({
        "id": "A-foreign", "companyId": None, "createdBy": "sales-b",
        "recipientSnapshot": {"name": "Foreign"}, "items": [], "currency": "EUR",
    }))

    with pytest.raises(HTTPException) as denied:
        run(offers.convert_offer_recipient_to_customer(
            "A-foreign", OfferCustomerCreateIn(name="Foreign"),
            principal(scoped), scoped,
        ))
    assert denied.value.status_code == 404
    assert database.raw.companies.count_documents({}) == 0


def test_sales_can_link_own_prospect_to_visible_existing_customer(monkeypatch):
    database = AsyncDatabase("final_review_offer_existing_link")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    actor = principal(scoped)
    run(scoped.companies.insert_one({
        "id": "visible", "name": "Existing Bar", "assignedSalesRepId": "sales-a",
        "active": True,
    }))
    run(scoped.offers.insert_one({
        "id": "A-link", "companyId": None, "offerKind": "prospect",
        "createdBy": "sales-a", "recipientSnapshot": {"name": "Existing Bar"},
        "items": [], "currency": "EUR",
    }))

    async def no_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(offers, "tenant_audit", no_side_effect)
    result = run(offers.convert_offer_recipient_to_customer(
        "A-link",
        OfferCustomerCreateIn(name="Existing Bar", existingCompanyId="visible"),
        actor, scoped,
    ))

    stored_offer = database.raw.offers.find_one({"id": "A-link"})
    assert result["id"] == "visible"
    assert stored_offer["companyId"] == "visible"
    assert stored_offer["recipientSnapshot"]["name"] == "Existing Bar"
    assert database.raw.companies.count_documents({}) == 1


def test_sales_cannot_link_prospect_to_hidden_customer(monkeypatch):
    database = AsyncDatabase("final_review_offer_hidden_link")
    scoped = access(database, TENANT_A, actor_user_id="sales-a", role="sales")
    actor = principal(scoped)
    run(scoped.companies.insert_one({
        "id": "hidden", "name": "Hidden Bar", "assignedSalesRepId": "sales-b",
        "active": True,
    }))
    run(scoped.offers.insert_one({
        "id": "A-hidden-link", "companyId": None, "offerKind": "prospect",
        "createdBy": "sales-a", "recipientSnapshot": {"name": "Hidden Bar"},
        "items": [], "currency": "EUR",
    }))

    with pytest.raises(HTTPException) as denied:
        run(offers.convert_offer_recipient_to_customer(
            "A-hidden-link",
            OfferCustomerCreateIn(name="Hidden Bar", existingCompanyId="hidden"),
            actor, scoped,
        ))

    assert denied.value.status_code == 404
    assert database.raw.offers.find_one({"id": "A-hidden-link"})["companyId"] is None


def test_migration_14_is_additive_and_registered_after_13():
    database = mongomock.MongoClient().ordo_test_final_review_migration
    before = {name: list(database[name].find({})) for name in database.list_collection_names()}
    plan = migration14.inspect(database)
    result = migration14.apply(
        database, type("Context", (), {"checkpoint": lambda self: None})()
    )

    assert result == {"indexesEnsured": len(migration14.INDEXES), "documentsChanged": 0}
    assert plan.expected_changes["documentsChanged"] == 0
    assert get_migrations()[-2].version == 14
    assert get_migrations()[-2].depends_on == (13,)
    assert all(
        keys[0][0] == "tenantId"
        for _collection, keys, name, _options in migration14.INDEXES
        if name != "uniq_company_source_offer_key"
    )
    assert all(list(database[name].find({})) == rows for name, rows in before.items())
