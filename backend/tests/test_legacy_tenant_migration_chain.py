from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import mongomock
import pytest

from app.migrations.models import MigrationStateError
from app.migrations.registry import get_migrations
from app.migrations.runner import MIGRATION_COLLECTION, MigrationRunner
from app.migrations.runner import ReadOnlyDatabase
from app.migrations.versions import v0001_baseline as baseline
from app.migrations.versions import v0002_tenant_schema_expansion as tenant_schema
from app.migrations.versions import v0006_legacy_tenant_bridge as bridge


SS = bridge.SS_TENANT_ID


def database(name: str):
    database_name = f"ordo_test_legacy_chain_{name}"
    assert database_name != "ordo_staging"
    return mongomock.MongoClient(tz_aware=True)[database_name]


def runner(db, *, migrations=None):
    return MigrationRunner(
        db,
        app_env="test",
        application_version="legacy-chain-test",
        migrations=migrations,
        lease_seconds=3,
    )


def legacy_fixture(db) -> None:
    """Structurally represent the documented 53-document staging inventory."""

    users = [
        {"id": "u-admin", "email": "admin@example.test", "role": "admin"},
        {"id": "u-sales", "email": "sales@example.test", "role": "sales"},
        {
            "id": "u-customer",
            "email": "customer@example.test",
            "role": "customer",
            "companyId": "c1",
        },
    ]
    companies = [
        {"id": f"c{number}", "name": f"Demo Company {number}"}
        for number in range(1, 5)
    ]
    products = [
        {
            "id": f"p{number}",
            "name": f"Demo Product {number}",
            "unit": "kg",
            "standardPrice": 10.0 + number,
            "b2cPrice": 12.0 + number,
            "cost": 5.0,
            "taxRate": 7,
        }
        for number in range(1, 5)
    ]
    customer_prices = [
        {"companyId": "c1", "productId": f"p{number}", "price": 9.0 + number}
        for number in range(1, 5)
    ] + [{"companyId": "c2", "productId": "p1", "price": 10.5}]
    offers = [
        {
            "id": f"offer-{number}",
            "companyId": f"c{number}",
            "items": [{"productId": "p1", "qty": 2, "price": 11.0}],
        }
        for number in range(1, 3)
    ]
    orders = [
        {
            "id": f"order-{number:02d}",
            "companyId": f"c{(number % 4) + 1}",
            "items": [{"productId": f"p{(number % 4) + 1}", "qty": 1, "price": 12.0}],
        }
        for number in range(1, 26)
    ]
    contracts = [
        {"id": f"contract-{number}", "companyId": f"c{number}", "productId": "p1", "price": 10.0}
        for number in range(1, 3)
    ]
    invoices = [
        {
            "id": f"invoice-{number}",
            "companyId": f"c{number}",
            "orderId": f"order-{number:02d}",
            "net": 12.0,
            "taxTotal": 0.84,
            "amount": 12.84,
            "lineItems": [],
        }
        for number in range(1, 5)
    ]
    counters = [{"_id": name, "value": 1} for name in ("offer", "order", "invoice", "contract")]

    for name, documents in {
        "users": users,
        "companies": companies,
        "products": products,
        "customer_prices": customer_prices,
        "offers": offers,
        "orders": orders,
        "contracts": contracts,
        "invoices": invoices,
        "counters": counters,
    }.items():
        db[name].insert_many(documents)

    assert sum(db[name].count_documents({}) for name in db.list_collection_names()) == 53


def source_state(db):
    return {
        name: list(db[name].find({}))
        for name in sorted(db.list_collection_names())
        if name != "schema_migration_lock"
    }


def test_full_chain_dry_run_is_sequential_and_source_is_unchanged():
    db = database("dry_run")
    legacy_fixture(db)
    before = deepcopy(source_state(db))

    report = runner(db).run(dry_run=True).as_dict()

    assert [item["version"] for item in report["plannedMigrations"]] == [1, 2, 6, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13]
    bridge_plan = next(item for item in report["plannedMigrations"] if item["version"] == 6)
    assert bridge_plan["expectedChanges"]["totalDocumentsToBackfill"] == 46
    assert bridge_plan["simulatedResult"]["totalDocumentsBackfilled"] == 46
    membership_plan = next(item for item in report["plannedMigrations"] if item["version"] == 3)
    assert membership_plan["expectedChanges"]["membershipsToInsert"] == 3
    assert source_state(db) == before
    assert "tenants" not in db.list_collection_names()
    assert MIGRATION_COLLECTION not in db.list_collection_names()


def test_full_chain_real_migration_backfills_and_verifies_legacy_fixture():
    db = database("apply")
    legacy_fixture(db)
    users_before = deepcopy(list(db.users.find({})))

    report = runner(db).run().as_dict()

    assert [item["version"] for item in report["executedMigrations"]] == [1, 2, 6, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13]
    assert db.tenants.count_documents({"id": SS, "slug": bridge.SS_TENANT_SLUG, "status": "active"}) == 1
    for collection in bridge.BUSINESS_COLLECTIONS:
        assert db[collection].count_documents({
            "$or": [{"tenantId": {"$exists": False}}, {"tenantId": None}],
        }) == 0
    assert list(db.users.find({})) == users_before
    assert all("tenantId" not in user for user in db.users.find({}))
    assert db.tenant_memberships.count_documents({"tenantId": SS}) == 3
    assert db.products.find_one({"id": "p1"})["standardPriceMinor"] == 1100
    assert db.settings.find_one({"tenantId": SS, "key": "shop"}) is not None
    assert "uniq_tenant_company_id" in db.companies.index_information()


def test_rerun_is_idempotent_and_dry_run_afterwards_plans_nothing():
    db = database("rerun")
    legacy_fixture(db)
    runner(db).run()
    before = deepcopy(source_state(db))

    second = runner(db).run().as_dict()
    preview = runner(db).run(dry_run=True).as_dict()

    assert second["executedMigrations"] == []
    assert preview["plannedMigrations"] == []
    assert source_state(db) == before


def test_partially_backfilled_canonical_documents_resume_safely():
    db = database("partial")
    legacy_fixture(db)
    db.companies.update_one({"id": "c1"}, {"$set": {"tenantId": SS}})
    db.products.update_one({"id": "p1"}, {"$set": {"tenantId": SS}})

    report = runner(db).run().as_dict()
    result = next(item for item in report["executedMigrations"] if item["version"] == 6)

    assert result["totalDocumentsBackfilled"] == 44
    assert db.companies.count_documents({"tenantId": SS}) == 4
    assert db.products.count_documents({"tenantId": SS}) == 4


def test_empty_database_chain_is_valid_and_does_not_invent_business_data():
    db = database("empty")

    report = runner(db).run().as_dict()

    bridge_result = next(item for item in report["executedMigrations"] if item["version"] == 6)
    assert bridge_result["totalDocumentsBackfilled"] == 0
    assert db.users.count_documents({}) == 0
    assert db.tenant_memberships.count_documents({}) == 0


def test_ambiguous_second_tenant_blocks_backfill_without_source_writes():
    db = database("other_tenant")
    legacy_fixture(db)
    db.tenants.insert_one({"id": "tnt_other", "slug": "other", "status": "active"})
    before = deepcopy(source_state(db))

    with pytest.raises(MigrationStateError, match="multiple tenants"):
        runner(db).run(dry_run=True)

    assert source_state(db) == before


def test_foreign_tenant_document_beside_legacy_data_fails_closed():
    db = database("foreign_document")
    legacy_fixture(db)
    db.products.update_one({"id": "p1"}, {"$set": {"tenantId": "tnt_other"}})

    with pytest.raises(MigrationStateError, match="another tenant|unknown tenant"):
        runner(db).run(dry_run=True)


@pytest.mark.parametrize("tenant_id", [None, "", " tnt_ss_0001 "])
def test_invalid_explicit_tenant_id_fails_closed(tenant_id):
    db = database(f"invalid_tenant_{tenant_id!r}")
    legacy_fixture(db)
    db.products.update_one({"id": "p1"}, {"$set": {"tenantId": tenant_id}})

    with pytest.raises(MigrationStateError, match="invalid .*tenantId"):
        runner(db).run(dry_run=True)


def test_duplicate_or_inactive_canonical_tenant_fails_closed():
    duplicate = database("duplicate_canonical")
    legacy_fixture(duplicate)
    now = datetime.now(timezone.utc)
    canonical = {
        **tenant_schema.SS_TENANT_FIELDS,
        "createdAt": now,
        "updatedAt": now,
    }
    duplicate.tenants.insert_many([canonical, deepcopy(canonical)])
    with pytest.raises(MigrationStateError):
        runner(duplicate).run(dry_run=True)

    inactive = database("inactive_canonical")
    legacy_fixture(inactive)
    inactive.tenants.insert_one({**canonical, "status": "inactive"})
    with pytest.raises(MigrationStateError):
        runner(inactive).run(dry_run=True)


def test_unknown_populated_collection_blocks_legacy_ownership_claim():
    db = database("unknown_collection")
    legacy_fixture(db)
    db.unrecognized_business.insert_one({"id": "unknown"})

    with pytest.raises(MigrationStateError, match="unknown populated collections"):
        runner(db).run(dry_run=True)


@pytest.mark.parametrize("role", [None, "owner", "ADMIN"])
def test_ambiguous_legacy_user_role_fails_closed(role):
    db = database(f"ambiguous_role_{role}")
    legacy_fixture(db)
    db.users.update_one({"id": "u-sales"}, {"$set": {"role": role}})

    with pytest.raises(MigrationStateError, match="ambiguous tenant role"):
        runner(db).run(dry_run=True)


def test_customer_company_and_item_product_references_are_checked_before_writes():
    missing_company = database("missing_company")
    legacy_fixture(missing_company)
    missing_company.users.update_one(
        {"id": "u-customer"}, {"$set": {"companyId": "not-found"}},
    )
    with pytest.raises(MigrationStateError, match="no S&S company"):
        runner(missing_company).run(dry_run=True)

    missing_product = database("missing_product")
    legacy_fixture(missing_product)
    missing_product.orders.update_one(
        {"id": "order-01"}, {"$set": {"items.0.productId": "not-found"}},
    )
    with pytest.raises(MigrationStateError, match="cross-tenant productId"):
        runner(missing_product).run(dry_run=True)


def test_duplicate_prospective_tenant_key_fails_before_backfill():
    db = database("duplicate_key")
    legacy_fixture(db)
    duplicate = deepcopy(db.companies.find_one({"id": "c1"}))
    duplicate.pop("_id")
    db.companies.insert_one(duplicate)
    runner(db, migrations=(baseline.MIGRATION,)).run()
    tenant_schema._bootstrap_tenant(db)

    with pytest.raises(MigrationStateError, match="duplicate values"):
        bridge.inspect(ReadOnlyDatabase(db))


def test_global_identity_and_global_audit_rows_remain_global():
    db = database("global_rows")
    legacy_fixture(db)
    db.audit_log.insert_many([
        {"action": "login", "userId": "u-admin"},
        {"action": "identity.reset", "userId": "u-admin"},
        {"action": "order.status", "userId": "u-admin"},
    ])

    runner(db).run()

    assert db.audit_log.find_one({"action": "login"}).get("tenantId") is None
    assert db.audit_log.find_one({"action": "identity.reset"}).get("tenantId") is None
    assert db.audit_log.find_one({"action": "order.status"})["tenantId"] == SS


def test_anonymous_push_registration_is_valid_tenant_business_data():
    db = database("anonymous_push")
    legacy_fixture(db)
    db.push_registrations.insert_one({"userId": "anon:device-1", "platform": "ios"})

    runner(db).run()

    assert db.push_registrations.find_one({"userId": "anon:device-1"})["tenantId"] == SS


@pytest.mark.parametrize("collection", ["users", "counters", "password_resets", "auth_rate_limits"])
def test_global_collections_are_never_silently_tenantized(collection):
    db = database(f"global_{collection}")
    legacy_fixture(db)
    if collection == "users":
        db.users.update_one({"id": "u-admin"}, {"$set": {"tenantId": SS}})
    elif collection == "counters":
        db.counters.update_one({"_id": "order"}, {"$set": {"tenantId": SS}})
    else:
        db[collection].insert_one({"id": "global", "tenantId": SS})

    with pytest.raises(MigrationStateError, match="must not contain tenantId|Global users"):
        runner(db).run(dry_run=True)


def test_bridge_can_resume_after_interrupted_partial_business_writes():
    db = database("interrupted")
    legacy_fixture(db)
    runner(db, migrations=(baseline.MIGRATION, tenant_schema.MIGRATION)).run()
    db.companies.update_many({}, {"$set": {"tenantId": SS}})
    db.products.update_one({"id": "p1"}, {"$set": {"tenantId": SS}})

    result = runner(db).run().as_dict()

    bridge_result = next(item for item in result["executedMigrations"] if item["version"] == 6)
    assert bridge_result["totalDocumentsBackfilled"] == 41
    assert all(db[name].count_documents({"tenantId": SS}) == db[name].count_documents({})
               for name in bridge.BUSINESS_COLLECTIONS)


def test_registry_dependency_order_is_explicit_and_checksums_of_v1_to_v5_are_stable():
    migrations = get_migrations()

    assert [migration.version for migration in migrations] == [1, 2, 6, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13]
    assert {migration.version: migration.depends_on for migration in migrations} == {
        1: (), 2: (1,), 6: (2,), 3: (6,), 4: (3,), 5: (4,), 7: (5,), 8: (7,),
        9: (8,), 10: (9,), 11: (10,), 12: (11,), 13: (12,),
    }
    assert {migration.version: migration.checksum for migration in migrations if migration.version <= 5} == {
        1: "5afb4663dec625f7e02a188a5d3a9c7f42276c2fa18d804b7d4fef7eca1f9548",
        2: "16fe8c83cceb314e4522c42e4def7b11209685ac3d35af18b841e3ef48e8939f",
        3: "8ac67d308d20cc7a67886413fb928a9bd185f6fc7669f6c3028e230b2714c7ee",
        4: "a8287eea59a58c33391625bf77de7dc3593bce8eb366b1b4cfe2f94d19eb8d3a",
        5: "8f293f30824c6b5f87701cfcee70d6a095b93637df321bff7efc7798284c98fd",
    }
