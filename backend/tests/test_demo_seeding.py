from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path
import secrets
import subprocess
import sys

import bcrypt
import mongomock
import pytest

# Importing the application creates a lazy Motor client. Force a non-routable,
# isolated target first so test collection can never inherit an ORDO database.
os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_demo_seed_import"
os.environ["JWT_SECRET"] = "test-only"
os.environ["APP_ENV"] = "test"

from app import main as app_main  # noqa: E402
from app.demo_seed import (  # noqa: E402
    DEMO_FINGERPRINT_FIELD,
    DEMO_SEED_VERSION,
    GLOBAL_SEED_COLLECTIONS,
    TENANT_SCOPED_COLLECTIONS,
    DemoSeedConfigurationError,
    DemoSeedConflictError,
    _document_fingerprint,
    _validate_manifest,
    build_demo_manifest,
    seed_demo,
)
from app.migrations.registry import get_migrations  # noqa: E402
from app.migrations.runner import MIGRATION_COLLECTION, MigrationRunner  # noqa: E402
from app.routers import machines as machines_router  # noqa: E402
from app.tenant_access import TenantBusinessAccess  # noqa: E402
from app.tenancy import SS_TENANT_ID, TenantContext, TenantResolutionSource  # noqa: E402
from scripts import seed_demo as seed_cli  # noqa: E402


PASSWORDS = {role: secrets.token_urlsafe(24) for role in ("admin", "sales", "customer")}
FIXED_NOW = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)


class AsyncCursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def sort(self, *args, **kwargs):
        self.cursor = self.cursor.sort(*args, **kwargs)
        return self

    async def to_list(self, length=None):
        rows = list(self.cursor)
        return rows if length is None else rows[:length]


class AsyncCollection:
    def __init__(self, database, name):
        self.database = database
        self.name = name
        self.collection = database.raw[name]

    def find(self, *args, **kwargs):
        return AsyncCursor(self.collection.find(*args, **kwargs))

    async def find_one(self, *args, **kwargs):
        return self.collection.find_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self.collection.count_documents(*args, **kwargs)

    async def insert_one(self, document):
        self.database.total_insert_attempts += 1
        if self.database.race_insert == self.name:
            self.database.race_insert = None
            self.collection.insert_one(deepcopy(document))
        race_document = self.database.race_document.pop(self.name, None)
        if race_document is not None:
            self.collection.insert_one(deepcopy(race_document))
        if self.database.fail_on_attempt == self.database.total_insert_attempts:
            raise RuntimeError("simulated insert failure")
        return self.collection.insert_one(deepcopy(document))

    async def insert_many(self, documents):
        return self.collection.insert_many(deepcopy(documents))

    async def update_one(self, *args, **kwargs):
        return self.collection.update_one(*args, **kwargs)

    async def create_index(self, *args, **kwargs):
        return self.collection.create_index(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name="ordo_test_demo_seed"):
        assert name != "ordo_staging"
        self.raw = mongomock.MongoClient(tz_aware=True)[name]
        self.name = name
        self.commands = []
        self.total_insert_attempts = 0
        self.fail_on_attempt = None
        self.race_insert = None
        self.race_document = {}

    def __getitem__(self, name):
        return AsyncCollection(self, name)

    def __getattr__(self, name):
        return self[name]

    async def command(self, command):
        self.commands.append(command)
        return {"ok": 1}


def run(coroutine):
    return asyncio.run(coroutine)


def tenant_access(database):
    return TenantBusinessAccess(database, TenantContext(
        tenant_id=SS_TENANT_ID,
        actor_user_id="test-user",
        resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
    ))


def prepare_tenant(database):
    if database.raw.tenants.count_documents({"id": SS_TENANT_ID}) == 0:
        MigrationRunner(
            database.raw,
            app_env="test",
            application_version="tenant-aware-seed-test",
            migrations=get_migrations(),
            lease_seconds=3,
        ).run()


def seed_test_database(database, *, passwords=PASSWORDS):
    prepare_tenant(database)
    return seed_demo(
        database,
        app_env="test",
        target_confirmation=f"test:{database.name}",
        passwords=passwords,
        now=FIXED_NOW,
    )


def all_documents(database):
    manifest_collections = set(build_demo_manifest(FIXED_NOW))
    return {
        name: list(database.raw[name].find({}).sort("_id", 1))
        for name in sorted(manifest_collections)
        if database.raw[name].count_documents({})
    }


@pytest.mark.parametrize("enable_demo_seed", [None, "true", "false"])
def test_normal_application_startup_creates_no_demo_or_business_documents(
    monkeypatch,
    enable_demo_seed,
):
    database = AsyncDatabase("ordo_test_startup")

    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(app_main, "db", database)
    monkeypatch.setattr(app_main, "init_storage", lambda: None)
    monkeypatch.setattr(app_main, "run_in_threadpool", immediate)
    for variable in (
        "SEED_ADMIN_PASSWORD",
        "SEED_SALES_PASSWORD",
        "SEED_CUSTOMER_PASSWORD",
    ):
        monkeypatch.delenv(variable, raising=False)
    if enable_demo_seed is None:
        monkeypatch.delenv("ENABLE_DEMO_SEED", raising=False)
    else:
        monkeypatch.setenv("ENABLE_DEMO_SEED", enable_demo_seed)

    run(app_main.on_startup())
    indexes_after_first_start = {
        name: deepcopy(database.raw[name].index_information())
        for name in database.raw.list_collection_names()
    }
    run(app_main.on_startup())

    assert database.commands == ["ping", "ping"]
    assert sum(database.raw[name].count_documents({}) for name in database.raw.list_collection_names()) == 0
    assert not any(
        database.raw[name].find_one({"_demoSeed": {"$exists": True}})
        for name in database.raw.list_collection_names()
    )
    expected_indexes = {
        "users": ("uniq_email", [("email", 1)], True, None),
        "password_resets": ("ttl_reset", [("expiresAt", 1)], False, 0),
        "auth_rate_limits": ("ttl_auth_rate_limit", [("expiresAt", 1)], False, 0),
        "offers": ("uniq_offer_id", [("id", 1)], True, None),
        "orders": ("uniq_order_id", [("id", 1)], True, None),
        "products": ("uniq_product_id", [("id", 1)], True, None),
    }
    assert set(database.raw.list_collection_names()) == set(expected_indexes)
    for collection, (index_name, keys, unique, ttl) in expected_indexes.items():
        information = database.raw[collection].index_information()
        assert set(information) == {"_id_", index_name}
        assert information[index_name]["key"] == keys
        assert information[index_name].get("unique", False) is unique
        assert information[index_name].get("expireAfterSeconds") == ttl
    assert {
        name: database.raw[name].index_information()
        for name in database.raw.list_collection_names()
    } == indexes_after_first_start


def test_machine_catalog_read_does_not_implicitly_seed(monkeypatch):
    database = AsyncDatabase("ordo_test_machine_catalog")
    monkeypatch.setattr(machines_router, "db", database)

    assert run(machines_router.list_machines({"role": "admin"}, tenant_access(database))) == []
    assert database.raw.machines.count_documents({}) == 0


def test_machine_catalog_does_not_expose_internal_seed_metadata(monkeypatch):
    database = AsyncDatabase("ordo_test_machine_seed_metadata")
    database.raw.machines.insert_one({
        "_id": "demo-machine",
        "id": "demo-machine",
        "name": "Demo machine",
        "price": 1,
        "active": True,
        "tenantId": SS_TENANT_ID,
        "_demoSeed": DEMO_SEED_VERSION,
        DEMO_FINGERPRINT_FIELD: "internal",
    })
    monkeypatch.setattr(machines_router, "db", database)

    rows = run(machines_router.list_machines({"role": "admin"}, tenant_access(database)))

    assert rows == [{"id": "demo-machine", "name": "Demo machine", "price": 1, "active": True}]


def test_startup_index_setup_preserves_preexisting_indexes():
    database = AsyncDatabase("ordo_test_existing_indexes")
    database.raw.products.create_index("name", name="existing_product_name")

    run(app_main.ensure_required_indexes(database))
    run(app_main.ensure_required_indexes(database))

    assert "existing_product_name" in database.raw.products.index_information()
    assert database.raw.products.count_documents({}) == 0


def test_explicit_demo_seed_populates_all_declared_documents():
    database = AsyncDatabase("ordo_test_explicit_seed")
    manifest = build_demo_manifest(FIXED_NOW)

    report = run(seed_test_database(database))

    expected_count = sum(len(documents) for documents in manifest.values())
    assert expected_count == 65
    assert report["inserted"] == expected_count
    assert report["unchanged"] == 0
    assert sum(database.raw[name].count_documents({}) for name in manifest) == expected_count
    admin = database.raw.users.find_one({"id": "u-admin"})
    assert bcrypt.checkpw(
        PASSWORDS["admin"].encode("utf-8"),
        admin["hashed_password"].encode("utf-8"),
    )
    assert all(password not in repr(report) for password in PASSWORDS.values())
    assert all(
        document.get(DEMO_FINGERPRINT_FIELD)
        for collection in manifest
        for document in database.raw[collection].find({})
    )
    assert database.raw.machines.count_documents({"_demoSeed": DEMO_SEED_VERSION}) == 3
    assert database.raw[MIGRATION_COLLECTION].find_one({"version": 2})["status"] == "completed"


def test_all_business_demo_documents_are_tenant_scoped_and_global_documents_are_not():
    database = AsyncDatabase("ordo_test_seed_tenant_scope")

    run(seed_test_database(database))

    assert TENANT_SCOPED_COLLECTIONS == {
        "companies",
        "contracts",
        "customer_prices",
        "invoices",
        "machine_requests",
        "machines",
        "offers",
        "orders",
        "pricing_promotions",
        "products",
        "shop_orders",
        "subscriptions",
        "tenant_memberships",
    }
    for collection in TENANT_SCOPED_COLLECTIONS:
        documents = list(database.raw[collection].find({}))
        assert documents
        assert all(document.get("tenantId") == SS_TENANT_ID for document in documents)
    assert GLOBAL_SEED_COLLECTIONS == {"users", "counters"}
    for collection in GLOBAL_SEED_COLLECTIONS:
        assert not any("tenantId" in document for document in database.raw[collection].find({}))


def test_demo_manifest_tells_one_linked_b2b_and_b2c_story_without_real_contact_data():
    manifest = build_demo_manifest(FIXED_NOW)

    customer = next(row for row in manifest["companies"] if row["id"] == "c1")
    assert customer["email"].endswith(".example.test")
    assert any(row["companyId"] == customer["id"] for row in manifest["customer_prices"])
    assert any(row["companyId"] == customer["id"] for row in manifest["offers"])
    assert any(row["companyId"] == customer["id"] for row in manifest["orders"])
    assert any(row["companyId"] == customer["id"] for row in manifest["invoices"])
    assert any(row["companyId"] == customer["id"] for row in manifest["contracts"])
    assert any(row["customer"]["companyId"] == customer["id"] for row in manifest["machine_requests"])
    assert any(row["companyId"] == customer["id"] for row in manifest["subscriptions"])
    assert any(row.get("companyId") == customer["id"] for row in manifest["pricing_promotions"])

    shop_products = [row for row in manifest["products"] if row.get("b2cPriceMinor")]
    assert len(shop_products) >= 3
    assert all(row.get("b2cTiers") for row in shop_products)
    assert manifest["subscriptions"][0]["items"][0]["priceSource"] == "customer_price"
    assert manifest["shop_orders"][0]["pricingContext"] == "b2c"
    assert all(
        row["email"].endswith(".example.test")
        for row in manifest["users"] + manifest["companies"]
    )


def test_default_manifest_is_deterministic_across_builds():
    assert build_demo_manifest() == build_demo_manifest()


@pytest.mark.parametrize(
    "mutation, message",
    [
        (
            lambda manifest: manifest["products"][0].pop("tenantId"),
            "invalid tenantId",
        ),
        (
            lambda manifest: manifest["users"][0].update({"tenantId": SS_TENANT_ID}),
            "must not contain tenantId",
        ),
        (
            lambda manifest: manifest["orders"][0]["items"][0].update(
                {"productId": "foreign-product"}
            ),
            "points outside",
        ),
    ],
)
def test_manifest_validation_rejects_tenant_scope_and_reference_regressions(
    mutation,
    message,
):
    manifest = build_demo_manifest(FIXED_NOW)
    mutation(manifest)

    with pytest.raises(DemoSeedConfigurationError, match=message):
        _validate_manifest(manifest)


def test_seed_requires_existing_tenant_and_does_not_run_migration_automatically():
    database = AsyncDatabase("ordo_test_seed_missing_tenant")

    with pytest.raises(DemoSeedConflictError, match="migration 2"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation=f"test:{database.name}",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert database.raw.list_collection_names() == []
    assert database.raw[MIGRATION_COLLECTION].count_documents({}) == 0


@pytest.mark.parametrize(
    "mutation",
    [
        {"$set": {"status": "inactive"}},
        {"$set": {"displayName": "Manipulated tenant"}},
        {"$unset": {"timezone": ""}},
    ],
)
def test_seed_rejects_inactive_manipulated_or_incomplete_tenant(mutation):
    database = AsyncDatabase("ordo_test_seed_invalid_tenant")
    prepare_tenant(database)
    database.raw.tenants.update_one({"id": SS_TENANT_ID}, mutation)

    with pytest.raises(DemoSeedConflictError, match="canonical S&S tenant"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation=f"test:{database.name}",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert all_documents(database) == {}


def test_repeated_demo_seed_is_idempotent_and_does_not_rehash_passwords():
    database = AsyncDatabase("ordo_test_idempotent")
    prepare_tenant(database)
    hash_calls = []

    def recording_hasher(password):
        hash_calls.append(password)
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    first_report = run(seed_demo(
        database,
        app_env="test",
        target_confirmation=f"test:{database.name}",
        passwords=PASSWORDS,
        password_hasher=recording_hasher,
        now=FIXED_NOW,
    ))
    before = all_documents(database)

    second_report = run(seed_demo(
        database,
        app_env="test",
        target_confirmation=f"test:{database.name}",
        passwords=PASSWORDS,
        password_hasher=recording_hasher,
        now=FIXED_NOW,
    ))

    assert first_report["inserted"] == 65
    assert second_report["inserted"] == 0
    assert second_report["unchanged"] == 65
    assert hash_calls == [PASSWORDS["admin"], PASSWORDS["sales"], PASSWORDS["customer"]]
    assert all_documents(database) == before


@pytest.mark.parametrize(
    "mutation",
    [
        {"$set": {"name": "Modified demo product"}},
        {"$unset": {"standardPrice": ""}},
        {"$unset": {DEMO_FINGERPRINT_FIELD: ""}},
        {"$set": {DEMO_FINGERPRINT_FIELD: "0" * 64}},
    ],
)
def test_modified_or_incomplete_marked_demo_document_fails_safe(mutation):
    database = AsyncDatabase("ordo_test_modified_marked_demo")
    run(seed_test_database(database))
    database.raw.products.update_one({"id": "p1"}, mutation)
    before = all_documents(database)

    with pytest.raises(DemoSeedConflictError, match="modified or incomplete"):
        run(seed_test_database(database))

    assert all_documents(database) == before


def test_refingerprinted_tenant_aware_document_still_conflicts_with_manifest():
    database = AsyncDatabase("ordo_test_refingerprinted_demo")
    run(seed_test_database(database))
    product = database.raw.products.find_one({"id": "p1"})
    product["name"] = "Manipulated and refingerprinted"
    product[DEMO_FINGERPRINT_FIELD] = _document_fingerprint(product)
    database.raw.products.replace_one({"_id": product["_id"]}, product)
    before = all_documents(database)

    with pytest.raises(DemoSeedConflictError, match="differs from the manifest"):
        run(seed_test_database(database))

    assert all_documents(database) == before


def test_refingerprinted_demo_user_with_another_password_still_conflicts():
    database = AsyncDatabase("ordo_test_refingerprinted_demo_user")
    run(seed_test_database(database))
    user = database.raw.users.find_one({"id": "u-admin"})
    user["hashed_password"] = bcrypt.hashpw(
        b"another-password",
        bcrypt.gensalt(),
    ).decode("utf-8")
    user[DEMO_FINGERPRINT_FIELD] = _document_fingerprint(user)
    database.raw.users.replace_one({"_id": user["_id"]}, user)
    before = all_documents(database)

    with pytest.raises(DemoSeedConflictError, match="differs from the manifest") as exc:
        run(seed_test_database(database))

    assert all(password not in str(exc.value) for password in PASSWORDS.values())
    assert all_documents(database) == before


def test_legacy_demo_document_without_tenant_id_requires_explicit_upgrade():
    database = AsyncDatabase("ordo_test_legacy_demo_conflict")
    prepare_tenant(database)
    legacy = deepcopy(build_demo_manifest(FIXED_NOW)["products"][0])
    legacy.pop("tenantId")
    legacy["_id"] = "ordo-demo-v1:products:p1"
    legacy["_demoSeed"] = "ordo-demo-v1"
    legacy[DEMO_FINGERPRINT_FIELD] = _document_fingerprint(legacy)
    database.raw.products.insert_one(legacy)

    with pytest.raises(DemoSeedConflictError, match="explicit upgrade"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation=f"test:{database.name}",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert database.raw.products.find_one({"_id": legacy["_id"]}) == legacy


def test_existing_demo_document_with_wrong_tenant_id_is_rejected():
    database = AsyncDatabase("ordo_test_wrong_demo_tenant")
    prepare_tenant(database)
    product = deepcopy(build_demo_manifest(FIXED_NOW)["products"][0])
    product["tenantId"] = "tnt_other_0001"
    product[DEMO_FINGERPRINT_FIELD] = _document_fingerprint(product)
    database.raw.products.insert_one(product)

    with pytest.raises(DemoSeedConflictError, match="differs from the manifest"):
        run(seed_test_database(database))


def test_cross_tenant_reference_identity_conflict_aborts_before_seed_writes():
    database = AsyncDatabase("ordo_test_cross_tenant_reference")
    prepare_tenant(database)
    foreign_product = {
        "_id": "foreign-product",
        "tenantId": "tnt_other_0001",
        "id": "p1",
        "name": "Other tenant product",
    }
    database.raw.products.insert_one(deepcopy(foreign_product))

    with pytest.raises(DemoSeedConflictError, match="existing business document"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation=f"test:{database.name}",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert all_documents(database) == {"products": [foreign_product]}


def test_concurrent_identical_insert_is_treated_as_unchanged():
    database = AsyncDatabase("ordo_test_concurrent_seed")
    database.race_insert = "users"

    report = run(seed_test_database(database))

    assert report["inserted"] == 64
    assert report["unchanged"] == 1
    assert sum(
        database.raw[name].count_documents({})
        for name in build_demo_manifest(FIXED_NOW)
    ) == 65


def test_concurrent_foreign_unique_key_insert_fails_without_overwrite_and_can_retry():
    database = AsyncDatabase("ordo_test_concurrent_foreign_unique")
    database.raw.users.create_index("email", unique=True)
    foreign = {
        "_id": "foreign-user",
        "id": "foreign-user",
        "email": "admin@ordo.example.test",
        "name": "Existing user",
    }
    database.race_document["users"] = foreign

    with pytest.raises(DemoSeedConflictError, match="existing business document"):
        run(seed_test_database(database))

    assert database.raw.users.find_one({"_id": "foreign-user"}) == foreign
    assert database.raw.users.count_documents({}) == 1
    with pytest.raises(DemoSeedConflictError):
        run(seed_test_database(database))

    database.raw.users.delete_one({"_id": "foreign-user"})
    report = run(seed_test_database(database))
    assert report["inserted"] == 65


def test_concurrent_foreign_non_unique_identity_is_detected_after_insert():
    database = AsyncDatabase("ordo_test_concurrent_foreign_non_unique")
    foreign = {"_id": "foreign-company", "id": "c1", "name": "Existing company"}
    database.race_document["companies"] = foreign

    with pytest.raises(DemoSeedConflictError, match="multiple documents"):
        run(seed_test_database(database))

    assert database.raw.companies.find_one({"_id": "foreign-company"}) == foreign
    assert database.raw.companies.count_documents({"id": "c1"}) == 2
    before_retry = all_documents(database)
    with pytest.raises(DemoSeedConflictError, match="multiple documents"):
        run(seed_test_database(database))
    assert all_documents(database) == before_retry


def test_non_conflicting_business_documents_are_left_unchanged():
    database = AsyncDatabase("ordo_test_foreign_data")
    foreign = {"_id": "foreign-product", "id": "foreign", "name": "Real product", "active": True}
    database.raw.products.insert_one(deepcopy(foreign))

    report = run(seed_test_database(database))

    assert report["inserted"] == 65
    assert database.raw.products.find_one({"_id": "foreign-product"}) == foreign


def test_conflict_aborts_before_first_seed_write_and_never_overwrites():
    database = AsyncDatabase("ordo_test_conflict")
    conflicting = {"_id": "business-product", "id": "p1", "name": "Existing business product"}
    database.raw.products.insert_one(deepcopy(conflicting))

    with pytest.raises(DemoSeedConflictError, match="existing business document"):
        run(seed_test_database(database))

    assert all_documents(database) == {"products": [conflicting]}


def test_user_email_or_id_collision_is_rejected_before_writes():
    database = AsyncDatabase("ordo_test_user_collision")
    existing = {"_id": "real-user", "id": "another-id", "email": "admin@ordo.example.test"}
    database.raw.users.insert_one(deepcopy(existing))

    with pytest.raises(DemoSeedConflictError):
        run(seed_test_database(database))

    assert all_documents(database) == {"users": [existing]}


def test_missing_demo_passwords_fail_before_writes():
    database = AsyncDatabase("ordo_test_missing_password")

    with pytest.raises(DemoSeedConfigurationError, match="customer"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation=f"test:{database.name}",
            passwords={key: value for key, value in PASSWORDS.items() if key != "customer"},
            now=FIXED_NOW,
        ))

    assert database.raw.list_collection_names() == []


@pytest.mark.parametrize("failure_at", [1, 7, 65])
def test_partial_operational_failure_is_not_reported_successful_and_retry_is_safe(
    failure_at,
):
    database = AsyncDatabase(f"ordo_test_partial_failure_{failure_at}")
    database.fail_on_attempt = failure_at

    with pytest.raises(RuntimeError, match="simulated insert failure"):
        run(seed_test_database(database))

    before_retry = all_documents(database)
    assert sum(len(documents) for documents in before_retry.values()) == failure_at - 1

    database.fail_on_attempt = None
    report = run(seed_test_database(database))
    assert report["inserted"] + report["unchanged"] == 65
    after_retry = all_documents(database)
    assert sum(len(documents) for documents in after_retry.values()) == 65
    all_ids = [document["_id"] for documents in after_retry.values() for document in documents]
    assert len(all_ids) == len(set(all_ids)) == 65
    for collection, existing_documents in before_retry.items():
        for existing in existing_documents:
            assert database.raw[collection].find_one({"_id": existing["_id"]}) == existing


@pytest.mark.parametrize(
    "app_env",
    ["prod", "production", "live", "PRODUCTION", "Prod", " Production "],
)
def test_seed_core_blocks_production_without_writes(app_env):
    database = AsyncDatabase("ordo_test_core_production_guard")

    with pytest.raises(DemoSeedConfigurationError, match="blocked in production"):
        run(seed_demo(
            database,
            app_env=app_env,
            target_confirmation=f"{app_env.strip().lower()}:{database.name}",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert database.raw.list_collection_names() == []


@pytest.mark.parametrize("app_env", ["", "   ", "prduction", "PRODUCTIONAL", None])
def test_seed_core_rejects_missing_or_unknown_environment_without_writes(app_env):
    database = AsyncDatabase("ordo_test_core_invalid_environment")

    with pytest.raises(DemoSeedConfigurationError):
        run(seed_demo(
            database,
            app_env=app_env,
            target_confirmation="test:ordo_test_core_invalid_environment",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert database.raw.list_collection_names() == []


def test_seed_core_requires_confirmation_for_actual_database_name():
    database = AsyncDatabase("ordo_test_actual_target")

    with pytest.raises(DemoSeedConfigurationError, match="does not match"):
        run(seed_demo(
            database,
            app_env="test",
            target_confirmation="test:ordo_test_other_target",
            passwords=PASSWORDS,
            now=FIXED_NOW,
        ))

    assert database.raw.list_collection_names() == []


@pytest.mark.parametrize("app_env", ["", "   ", "prduction", "PRODUCTIONAL"])
def test_cli_invalid_environment_fails_closed_before_connection(monkeypatch, app_env):
    connected = {"value": False}

    async def forbidden_run_seed(*_args, **_kwargs):
        connected["value"] = True
        raise AssertionError("database connection must not be attempted")

    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DB_NAME", "ordo_test_cli")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:1")
    monkeypatch.setattr(seed_cli, "run_seed", forbidden_run_seed)
    monkeypatch.setattr(sys, "argv", ["seed_demo.py", "--confirm-target", "test:ordo_test_cli"])

    assert seed_cli.main() == 2
    assert connected["value"] is False


@pytest.mark.parametrize(
    "missing_variable",
    [
        "DB_NAME",
        "MONGO_URL",
        "SEED_ADMIN_PASSWORD",
        "SEED_SALES_PASSWORD",
        "SEED_CUSTOMER_PASSWORD",
    ],
)
def test_cli_missing_configuration_fails_before_connection(monkeypatch, missing_variable):
    connected = {"value": False}

    async def forbidden_run_seed(*_args, **_kwargs):
        connected["value"] = True
        raise AssertionError("database connection must not be attempted")

    environment = {
        "APP_ENV": "test",
        "DB_NAME": "ordo_test_cli_missing_configuration",
        "MONGO_URL": "mongodb://127.0.0.1:1",
        "SEED_ADMIN_PASSWORD": PASSWORDS["admin"],
        "SEED_SALES_PASSWORD": PASSWORDS["sales"],
        "SEED_CUSTOMER_PASSWORD": PASSWORDS["customer"],
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv(missing_variable, raising=False)
    monkeypatch.setattr(seed_cli, "run_seed", forbidden_run_seed)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "seed_demo.py",
            "--confirm-target",
            "test:ordo_test_cli_missing_configuration",
        ],
    )

    assert seed_cli.main() == 2
    assert connected["value"] is False


@pytest.mark.parametrize(
    "app_env",
    ["prod", "production", "live", "PRODUCTION", "Prod", " Production "],
)
def test_production_seed_is_always_blocked_even_with_enable_flag(monkeypatch, app_env):
    connected = {"value": False}

    async def forbidden_run_seed(*_args, **_kwargs):
        connected["value"] = True
        raise AssertionError("production database connection must not be attempted")

    normalized = app_env.strip().lower()
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.setenv("DB_NAME", "ordo_production")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:1")
    monkeypatch.setenv("ENABLE_DEMO_SEED", "true")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", PASSWORDS["admin"])
    monkeypatch.setenv("SEED_SALES_PASSWORD", PASSWORDS["sales"])
    monkeypatch.setenv("SEED_CUSTOMER_PASSWORD", PASSWORDS["customer"])
    monkeypatch.setattr(seed_cli, "run_seed", forbidden_run_seed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_demo.py", "--confirm-target", f"{normalized}:ordo_production"],
    )

    assert seed_cli.main() == 2
    assert connected["value"] is False


def test_cli_requires_exact_target_confirmation(monkeypatch):
    called = {"value": False}

    async def forbidden_run_seed(*_args, **_kwargs):
        called["value"] = True
        return {}

    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("DB_NAME", "safe_test_database")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:1")
    monkeypatch.setattr(seed_cli, "run_seed", forbidden_run_seed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_demo.py", "--confirm-target", "staging:another_database"],
    )

    assert seed_cli.main() == 2
    assert called["value"] is False


def test_cli_explicit_success_reports_only_after_seed_completion(monkeypatch, capsys):
    async def successful_run_seed(
        _url,
        database_name,
        app_env,
        target_confirmation,
        passwords,
    ):
        assert database_name == "ordo_test_cli_success"
        assert app_env == "test"
        assert target_confirmation == "test:ordo_test_cli_success"
        assert passwords == PASSWORDS
        return {"seedVersion": DEMO_SEED_VERSION, "inserted": 65, "unchanged": 0}

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_NAME", "ordo_test_cli_success")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:1")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", PASSWORDS["admin"])
    monkeypatch.setenv("SEED_SALES_PASSWORD", PASSWORDS["sales"])
    monkeypatch.setenv("SEED_CUSTOMER_PASSWORD", PASSWORDS["customer"])
    monkeypatch.setattr(seed_cli, "run_seed", successful_run_seed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_demo.py", "--confirm-target", "test:ordo_test_cli_success"],
    )

    assert seed_cli.main() == 0
    output = capsys.readouterr()
    assert DEMO_SEED_VERSION in output.out
    assert all(password not in output.out for password in PASSWORDS.values())
    assert all(password not in output.err for password in PASSWORDS.values())


def test_cli_failure_never_prints_success_report(monkeypatch, capsys):
    async def failing_run_seed(*_args, **_kwargs):
        raise RuntimeError(
            f"database stopped after a partial write password={PASSWORDS['admin']}"
        )

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("DB_NAME", "ordo_test_cli_failure")
    monkeypatch.setenv("MONGO_URL", "mongodb://127.0.0.1:1")
    monkeypatch.setenv("SEED_ADMIN_PASSWORD", PASSWORDS["admin"])
    monkeypatch.setenv("SEED_SALES_PASSWORD", PASSWORDS["sales"])
    monkeypatch.setenv("SEED_CUSTOMER_PASSWORD", PASSWORDS["customer"])
    monkeypatch.setattr(seed_cli, "run_seed", failing_run_seed)
    monkeypatch.setattr(
        sys,
        "argv",
        ["seed_demo.py", "--confirm-target", "test:ordo_test_cli_failure"],
    )

    assert seed_cli.main() == 3
    output = capsys.readouterr()
    assert DEMO_SEED_VERSION not in output.out
    assert "partial write" in output.err
    assert all(password not in output.out for password in PASSWORDS.values())
    assert all(password not in output.err for password in PASSWORDS.values())


def test_backend_import_still_succeeds_without_seed_passwords():
    backend_dir = Path(__file__).resolve().parents[1]
    environment = {
        **os.environ,
        "MONGO_URL": "mongodb://127.0.0.1:1",
        "DB_NAME": "ordo_test_backend_import",
        "JWT_SECRET": "test-only",
        "APP_ENV": "test",
    }
    environment.pop("SEED_ADMIN_PASSWORD", None)
    environment.pop("SEED_SALES_PASSWORD", None)
    environment.pop("SEED_CUSTOMER_PASSWORD", None)

    completed = subprocess.run(
        [sys.executable, "-c", "import server; print(server.app.title)"],
        cwd=backend_dir,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert "S&S B2B API" in completed.stdout
