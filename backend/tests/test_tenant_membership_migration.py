from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import mongomock
import pytest

from app.migrations.models import MigrationStateError
from app.migrations.runner import MIGRATION_COLLECTION, MigrationRunner
from app.migrations.versions import v0003_tenant_memberships as memberships
from app.tenancy import SS_TENANT, SS_TENANT_ID, tenant_to_document


FIXED_TIME = datetime(2026, 9, 22, 8, 0, tzinfo=timezone.utc)


def database(name: str):
    database_name = f"ordo_test_membership_migration_{name}"
    assert database_name != "ordo_staging"
    return mongomock.MongoClient(tz_aware=True)[database_name]


def prepare(database, *, tenant_status: str = "active"):
    tenant = tenant_to_document(SS_TENANT, created_at=FIXED_TIME, updated_at=FIXED_TIME)
    tenant["status"] = tenant_status
    database.tenants.insert_one(tenant)


def runner(database):
    return MigrationRunner(
        database,
        app_env="test",
        application_version="membership-test",
        migrations=(memberships.MIGRATION,),
        lease_seconds=3,
    )


def legacy_users(database):
    database.users.insert_many([
        {"id": "admin", "role": "admin"},
        {"id": "sales", "role": "sales", "salesRepId": "sales"},
        {"id": "customer", "role": "customer", "companyId": "c1"},
        {"id": "shop", "role": "shopuser"},
    ])
    database.companies.insert_one({"tenantId": SS_TENANT_ID, "id": "c1"})


def test_dry_run_is_read_only_and_reports_explicit_backfill():
    db = database("preview")
    prepare(db)
    legacy_users(db)
    before = {
        name: list(db[name].find({}))
        for name in db.list_collection_names()
    }

    report = runner(db).run(dry_run=True).as_dict()

    expected = report["plannedMigrations"][0]["expectedChanges"]
    assert expected["membershipsToInsert"] == 3
    assert expected["shopUsersExcluded"] == 1
    assert expected["globalUsersModified"] == 0
    assert "tenant_memberships" not in db.list_collection_names()
    assert MIGRATION_COLLECTION not in db.list_collection_names()
    assert {
        name: list(db[name].find({}))
        for name in db.list_collection_names()
    } == before


def test_apply_creates_deterministic_memberships_and_indexes_without_user_writes():
    db = database("apply")
    prepare(db)
    legacy_users(db)
    users_before = list(db.users.find({}))

    report = runner(db).run().as_dict()

    assert report["executedMigrations"][0]["membershipsInserted"] == 3
    assert list(db.users.find({})) == users_before
    assert db.tenant_memberships.count_documents({}) == 3
    customer = db.tenant_memberships.find_one({"userId": "customer"})
    assert customer["id"] == memberships.membership_id(SS_TENANT_ID, "customer")
    assert customer["companyId"] == "c1"
    assert db.tenant_memberships.find_one({"userId": "shop"}) is None
    information = db.tenant_memberships.index_information()
    assert set(spec.name for spec in memberships.INDEX_SPECS) <= set(information)


def test_reapplication_is_idempotent():
    db = database("idempotent")
    prepare(db)
    legacy_users(db)
    first = runner(db).run().as_dict()
    before = list(db.tenant_memberships.find({}))

    second = runner(db).run().as_dict()

    assert first["executedMigrations"][0]["membershipsInserted"] == 3
    assert second["executedMigrations"] == []
    assert list(db.tenant_memberships.find({})) == before


def test_customer_without_tenant_company_fails_before_writes():
    db = database("missing_company")
    prepare(db)
    db.users.insert_one({"id": "customer", "role": "customer", "companyId": "legacy"})

    with pytest.raises(MigrationStateError, match="references no S&S company"):
        runner(db).run(dry_run=True)

    assert "tenant_memberships" not in db.list_collection_names()


def test_inactive_tenant_and_conflicting_membership_fail_closed():
    inactive = database("inactive_tenant")
    prepare(inactive, tenant_status="inactive")
    inactive.users.insert_one({"id": "admin", "role": "admin"})
    with pytest.raises(MigrationStateError, match="active"):
        runner(inactive).run(dry_run=True)

    conflict = database("conflict")
    prepare(conflict)
    conflict.users.insert_one({"id": "admin", "role": "admin"})
    conflict.tenant_memberships.insert_one({
        "id": memberships.membership_id(SS_TENANT_ID, "admin"),
        "tenantId": SS_TENANT_ID,
        "userId": "admin",
        "role": "sales",
        "status": "active",
        "companyId": None,
        "createdAt": FIXED_TIME.isoformat(),
        "updatedAt": FIXED_TIME.isoformat(),
    })
    before = deepcopy(list(conflict.tenant_memberships.find({})))
    with pytest.raises(MigrationStateError, match="conflicts"):
        runner(conflict).run(dry_run=True)
    assert list(conflict.tenant_memberships.find({})) == before


def test_existing_foreign_tenant_membership_does_not_block_ss_membership():
    db = database("multi_tenant")
    prepare(db)
    db.tenants.insert_one({
        "id": "tnt_other", "slug": "other", "displayName": "Other",
        "status": "active",
    })
    db.users.insert_one({"id": "shared", "role": "admin"})
    db.tenant_memberships.insert_one({
        "id": "mbr-other", "tenantId": "tnt_other", "userId": "shared",
        "role": "sales", "status": "active", "companyId": None,
        "createdAt": FIXED_TIME.isoformat(), "updatedAt": FIXED_TIME.isoformat(),
    })

    runner(db).run()

    assert db.tenant_memberships.count_documents({"userId": "shared"}) == 2
    assert db.tenant_memberships.find_one({
        "tenantId": SS_TENANT_ID, "userId": "shared",
    })["role"] == "admin"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"role": "platform_admin"}, "invalid role"),
        ({"status": "pending"}, "invalid status"),
        ({"companyId": "foreign-company"}, "cannot reference a company"),
        ({"createdAt": None}, "invalid timestamps"),
    ],
)
def test_existing_membership_invariant_violation_blocks_migration(changes, message):
    db = database(f"invalid_{message.replace(' ', '_')}")
    prepare(db)
    db.users.insert_one({"id": "admin", "role": "admin"})
    document = {
        "id": memberships.membership_id(SS_TENANT_ID, "admin"),
        "tenantId": SS_TENANT_ID,
        "userId": "admin",
        "role": "admin",
        "status": "active",
        "companyId": None,
        "createdAt": FIXED_TIME.isoformat(),
        "updatedAt": FIXED_TIME.isoformat(),
        **changes,
    }
    db.tenant_memberships.insert_one(document)

    with pytest.raises(MigrationStateError, match=message):
        runner(db).run(dry_run=True)

    assert db.tenant_memberships.count_documents({}) == 1
    assert MIGRATION_COLLECTION not in db.list_collection_names()
