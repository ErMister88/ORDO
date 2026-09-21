from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os
from pathlib import Path

import mongomock
import pytest
from fastapi import HTTPException
from starlette.requests import Request

# Application imports create a lazy Motor client. Pin it to a non-routable
# test-only target before importing any app module.
os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_tenant_business_import"
os.environ["JWT_SECRET"] = "test-only"
os.environ["APP_ENV"] = "test"

from app import core, deps
from app.models import (
    AcceptOfferIn,
    CustomerPriceIn,
    MachineTermsIn,
    MachineIn,
    MachineRequestIn,
    MachineRespondIn,
    OfferCreate,
    OfferItemIn,
    OrderCreate,
    OrderItemIn,
    OrderStatusIn,
    ProductIn,
    SubscriptionIn,
    NewsletterIn,
    PushBroadcastIn,
    ShopCustomerIn,
    ShopItemIn,
    ShopOrderIn,
    ShopSettingsIn,
    ShopStatusIn,
    ValidateCodeIn,
)
from app.routers import (
    billing,
    companies,
    invoices,
    machines,
    offers,
    orders,
    pricing,
    products,
    newsletter,
    push,
    shop,
    subscriptions,
)
from app.tenant_access import (
    TENANT_SCOPED_BUSINESS_COLLECTIONS,
    TenantBusinessAccess,
    TenantScopeViolation,
)
from app.tenancy import (
    SS_TENANT,
    SS_TENANT_ID,
    TenantContext,
    TenantResolutionSource,
    tenant_to_document,
)


TENANT_A = "tnt_test_a"
TENANT_B = "tnt_test_b"


class AsyncCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def sort(self, *args, **kwargs):
        self._cursor = self._cursor.sort(*args, **kwargs)
        return self

    async def to_list(self, length=None):
        documents = list(self._cursor)
        return documents if length is None else documents[:length]


class AsyncCollection:
    def __init__(self, collection):
        self._collection = collection

    def find(self, *args, **kwargs):
        return AsyncCursor(self._collection.find(*args, **kwargs))

    async def find_one(self, *args, **kwargs):
        return self._collection.find_one(*args, **kwargs)

    async def insert_one(self, document, *args, **kwargs):
        return self._collection.insert_one(deepcopy(document), *args, **kwargs)

    async def update_one(self, *args, **kwargs):
        return self._collection.update_one(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self._collection.delete_one(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self._collection.count_documents(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name: str):
        assert name != "ordo_staging"
        self.raw = mongomock.MongoClient(tz_aware=True)[name]

    def __getitem__(self, name):
        return AsyncCollection(self.raw[name])

    def __getattr__(self, name):
        return self[name]


def run(coroutine):
    return asyncio.run(coroutine)


def context(tenant_id: str) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        actor_user_id="synthetic-user",
        resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
    )


def access(database: AsyncDatabase, tenant_id: str) -> TenantBusinessAccess:
    return TenantBusinessAccess(database, context(tenant_id))


def unscoped_collection_accesses(source: str, filename: str = "<source>") -> list[str]:
    tree = ast.parse(source, filename=filename)
    database_names = {"db", "database"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("core"):
            for imported in node.names:
                if imported.name == "db":
                    database_names.add(imported.asname or imported.name)

    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            if not (
                isinstance(value, ast.Name)
                and value.id in database_names
                or isinstance(value, ast.Attribute)
                and value.attr == "db"
            ):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in database_names:
                    database_names.add(target.id)
                    changed = True

    def is_database_expression(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Name)
            and node.id in database_names
            or isinstance(node, ast.Attribute)
            and node.attr == "db"
        )

    violations = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr in TENANT_SCOPED_BUSINESS_COLLECTIONS
            and is_database_expression(node.value)
        ):
            violations.append(f"{filename}:{node.lineno}:attribute:{node.attr}")
        if isinstance(node, ast.Subscript) and is_database_expression(node.value):
            if not isinstance(node.slice, ast.Constant):
                violations.append(f"{filename}:{node.lineno}:dynamic-subscript")
            elif node.slice.value in TENANT_SCOPED_BUSINESS_COLLECTIONS:
                violations.append(
                    f"{filename}:{node.lineno}:subscript:{node.slice.value}"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and is_database_expression(node.args[0])
        ):
            collection_arg = node.args[1]
            if not isinstance(collection_arg, ast.Constant):
                violations.append(f"{filename}:{node.lineno}:dynamic-getattr")
            elif collection_arg.value in TENANT_SCOPED_BUSINESS_COLLECTIONS:
                violations.append(
                    f"{filename}:{node.lineno}:getattr:{collection_arg.value}"
                )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get_collection"
            and is_database_expression(node.func.value)
            and node.args
        ):
            collection_arg = node.args[0]
            if not isinstance(collection_arg, ast.Constant):
                violations.append(f"{filename}:{node.lineno}:dynamic-get-collection")
            elif collection_arg.value in TENANT_SCOPED_BUSINESS_COLLECTIONS:
                violations.append(
                    f"{filename}:{node.lineno}:get-collection:{collection_arg.value}"
                )
    return violations


def test_company_reads_distinguish_identical_business_ids_between_tenants():
    database = AsyncDatabase("tenant_access_company_reads")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "name": "Company A"}))
    run(tenant_b.companies.insert_one({"id": "c1", "name": "Company B"}))

    assert run(tenant_a.companies.find_one({"id": "c1"}))["name"] == "Company A"
    assert run(tenant_b.companies.find_one({"id": "c1"}))["name"] == "Company B"


def test_cross_tenant_company_update_and_delete_match_nothing():
    database = AsyncDatabase("tenant_access_company_mutations")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.companies.insert_one({"id": "only-b", "name": "Company B"}))

    update = run(tenant_a.companies.update_one(
        {"id": "only-b"},
        {"$set": {"name": "Compromised"}},
    ))
    deletion = run(tenant_a.companies.delete_one({"id": "only-b"}))

    assert update.matched_count == 0
    assert deletion.deleted_count == 0
    assert run(tenant_b.companies.find_one({"id": "only-b"}))["name"] == "Company B"


@pytest.mark.parametrize("collection_name", sorted(TENANT_SCOPED_BUSINESS_COLLECTIONS))
def test_create_sets_tenant_server_side_without_mutating_input(collection_name):
    database = AsyncDatabase(f"tenant_access_insert_{collection_name}")
    tenant_a = access(database, TENANT_A)
    document = {"id": "business-id", "value": 1}

    run(getattr(tenant_a, collection_name).insert_one(document))

    assert "tenantId" not in document
    stored = database.raw[collection_name].find_one({"id": "business-id"})
    assert stored["tenantId"] == TENANT_A


def test_conflicting_client_tenant_is_rejected_and_context_is_unchanged():
    database = AsyncDatabase("tenant_access_client_override")
    tenant_a = access(database, TENANT_A)

    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(tenant_a.products.insert_one({"id": "p1", "tenantId": TENANT_B}))
    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(tenant_a.products.find_one({"id": "p1", "tenantId": TENANT_B}))
    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(tenant_a.products.find_one({
            "$or": [{"id": "p1"}, {"tenantId": TENANT_B}],
        }))

    assert tenant_a.context.tenant_id == TENANT_A
    assert database.raw.products.count_documents({}) == 0


def test_server_dependency_resolves_only_configured_database_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_server_resolution")
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    database.raw.tenants.insert_one(tenant_to_document(
        SS_TENANT,
        created_at=now,
        updated_at=now,
    ))
    monkeypatch.setenv("TENANCY_MODE", "single")
    monkeypatch.setenv("DEFAULT_TENANT_ID", SS_TENANT_ID)
    monkeypatch.setattr(deps, "db", database)

    resolved = run(deps._resolve_tenant_context("u-admin"))

    assert resolved.tenant_id == SS_TENANT_ID
    assert resolved.actor_user_id == "u-admin"


def test_server_dependency_sees_active_tenant_beyond_first_thousand(monkeypatch):
    database = AsyncDatabase("tenant_access_complete_directory")
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    database.raw.tenants.insert_one(tenant_to_document(
        SS_TENANT,
        created_at=now,
        updated_at=now,
    ))
    database.raw.tenants.insert_many([
        {
            "id": f"tnt_inactive_{index:04d}",
            "slug": f"inactive-{index:04d}",
            "displayName": f"Inactive {index}",
            "legalName": None,
            "status": "inactive",
            "defaultCurrency": "EUR",
            "defaultLocale": "de-DE",
            "timezone": "Europe/Berlin",
        }
        for index in range(999)
    ])
    database.raw.tenants.insert_one({
        "id": TENANT_B,
        "slug": "second-active",
        "displayName": "Second active tenant",
        "legalName": None,
        "status": "active",
        "defaultCurrency": "EUR",
        "defaultLocale": "de-DE",
        "timezone": "Europe/Berlin",
    })
    monkeypatch.setenv("TENANCY_MODE", "single")
    monkeypatch.setenv("DEFAULT_TENANT_ID", SS_TENANT_ID)
    monkeypatch.setattr(deps, "db", database)

    with pytest.raises(HTTPException) as exc:
        run(deps._resolve_tenant_context("u-admin"))

    assert exc.value.status_code == 503
    assert exc.value.detail == "Tenant-Kontext nicht verfügbar"


def test_tenant_id_cannot_be_changed_by_update_or_rename():
    database = AsyncDatabase("tenant_access_immutable_tenant")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.products.insert_one({"id": "p1", "name": "Product"}))

    with pytest.raises(TenantScopeViolation, match="immutable"):
        run(tenant_a.products.update_one(
            {"id": "p1"},
            {"$set": {"tenantId": TENANT_B}},
        ))
    with pytest.raises(TenantScopeViolation, match="immutable"):
        run(tenant_a.products.update_one(
            {"id": "p1"},
            {"$rename": {"name": "tenantId"}},
        ))

    assert run(tenant_a.products.find_one({"id": "p1"}))["tenantId"] == TENANT_A


def test_legacy_document_without_tenant_id_is_never_visible_or_modified():
    database = AsyncDatabase("tenant_access_legacy")
    database.raw.companies.insert_one({"_id": "legacy", "id": "c1", "name": "Legacy"})
    tenant_a = access(database, TENANT_A)

    assert run(tenant_a.companies.find_one({"id": "c1"})) is None
    result = run(tenant_a.companies.update_one(
        {"id": "c1"},
        {"$set": {"name": "Claimed"}},
    ))

    assert result.matched_count == 0
    assert database.raw.companies.find_one({"_id": "legacy"}) == {
        "_id": "legacy",
        "id": "c1",
        "name": "Legacy",
    }


def test_products_customer_prices_history_and_upload_metadata_are_isolated():
    database = AsyncDatabase("tenant_access_all_collections")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    for scoped_collection, document in (
        ("products", {"id": "p1", "name": "Product"}),
        ("customer_prices", {"companyId": "c1", "productId": "p1", "price": 10.0}),
        ("price_history", {"companyId": "c1", "productId": "p1", "newPrice": 10.0}),
        ("uploads", {"storagePath": "shared/path.jpg", "ownerId": "user-a"}),
    ):
        run(getattr(tenant_a, scoped_collection).insert_one(document))
        assert run(getattr(tenant_b, scoped_collection).find_one(document)) is None

    run(tenant_b.uploads.insert_one({"storagePath": "shared/path.jpg", "ownerId": "user-b"}))
    assert run(tenant_a.uploads.find_one({"storagePath": "shared/path.jpg"}))["ownerId"] == "user-a"
    assert run(tenant_b.uploads.find_one({"storagePath": "shared/path.jpg"}))["ownerId"] == "user-b"


def test_upsert_sets_tenant_id_and_stays_inside_tenant():
    database = AsyncDatabase("tenant_access_upsert")
    tenant_a = access(database, TENANT_A)

    result = run(tenant_a.customer_prices.update_one(
        {"companyId": "c1", "productId": "p1"},
        {"$set": {"price": 12.5}},
        upsert=True,
    ))

    assert result.upserted_id is not None
    stored = database.raw.customer_prices.find_one({"companyId": "c1"})
    assert stored["tenantId"] == TENANT_A


def test_customer_price_upserts_distinguish_same_references_between_tenants():
    database = AsyncDatabase("tenant_access_customer_price_same_references")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)

    run(tenant_a.customer_prices.update_one(
        {"companyId": "c1", "productId": "p1"},
        {"$set": {"price": 11.0}},
        upsert=True,
    ))
    run(tenant_b.customer_prices.update_one(
        {"companyId": "c1", "productId": "p1"},
        {"$set": {"price": 22.0}},
        upsert=True,
    ))

    assert run(tenant_a.customer_prices.find_one(
        {"companyId": "c1", "productId": "p1"}
    ))["price"] == 11.0
    assert run(tenant_b.customer_prices.find_one(
        {"companyId": "c1", "productId": "p1"}
    ))["price"] == 22.0
    assert database.raw.customer_prices.count_documents({}) == 2


@pytest.mark.parametrize("missing_reference", ["company", "product"])
def test_customer_price_rejects_cross_tenant_references_without_disclosure(
    monkeypatch,
    missing_reference,
):
    database = AsyncDatabase(f"tenant_access_cross_reference_{missing_reference}")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    company_access = tenant_b if missing_reference == "company" else tenant_a
    product_access = tenant_b if missing_reference == "product" else tenant_a
    run(company_access.companies.insert_one({"id": "c1", "name": "Company"}))
    run(product_access.products.insert_one({"id": "p1", "name": "Product"}))

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pricing, "audit", no_audit)
    with pytest.raises(HTTPException) as exc:
        run(pricing.upsert_customer_price(
            CustomerPriceIn(companyId="c1", productId="p1", price=10.0),
            {"id": "u-admin", "name": "Admin"},
            tenant_a,
        ))

    assert exc.value.status_code == 404
    assert exc.value.detail == "Kunde oder Produkt nicht gefunden"
    assert database.raw.customer_prices.count_documents({}) == 0
    assert database.raw.price_history.count_documents({}) == 0


def test_valid_customer_price_and_history_receive_the_same_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_valid_customer_price")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "name": "Company"}))
    run(tenant_a.products.insert_one({"id": "p1", "name": "Product"}))

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(pricing, "audit", no_audit)
    result = run(pricing.upsert_customer_price(
        CustomerPriceIn(companyId="c1", productId="p1", price=10.0),
        {"id": "u-admin", "name": "Admin"},
        tenant_a,
    ))

    assert result == {"ok": True, "companyId": "c1", "productId": "p1", "price": 10.0}
    assert database.raw.customer_prices.find_one({})["tenantId"] == TENANT_A
    assert database.raw.price_history.find_one({})["tenantId"] == TENANT_A


def test_fixed_customer_price_still_precedes_quantity_tiers():
    database = AsyncDatabase("tenant_access_pricing_regression")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.customer_prices.insert_one({
        "companyId": "c1",
        "productId": "p1",
        "price": 15.9,
    }))
    product = {
        "id": "p1",
        "standardPrice": 16.9,
        "discountTiers": [{"minQty": 50, "price": 14.0}],
    }

    assert run(orders._resolve_unit_price(tenant_a, "c1", product, 100)) == 15.9


def test_contract_price_lookup_is_fail_closed_to_resolved_tenant():
    database = AsyncDatabase("tenant_access_contract_price_edge")
    tenant_a = access(database, TENANT_A)
    product = {
        "id": "p1",
        "standardPrice": 16.9,
        "discountTiers": [{"minQty": 50, "price": 14.0}],
    }
    database.raw.contracts.insert_many([
        {
            "id": "contract-b",
            "tenantId": TENANT_B,
            "companyId": "c1",
            "productId": "p1",
            "price": 7.0,
        },
        {
            "id": "contract-legacy",
            "companyId": "c1",
            "productId": "p1",
            "price": 8.0,
        },
    ])

    assert run(orders._resolve_unit_price(tenant_a, "c1", product, 100)) == 14.0

    database.raw.contracts.insert_one({
        "id": "contract-a",
        "tenantId": TENANT_A,
        "companyId": "c1",
        "productId": "p1",
        "price": 12.0,
    })

    assert run(orders._resolve_unit_price(tenant_a, "c1", product, 100)) == 12.0


def test_product_request_model_does_not_forward_client_tenant_id():
    body = ProductIn.model_validate({
        "brand": "Brand",
        "name": "Product",
        "unit": "kg",
        "standardPrice": 10.0,
        "salesFloor": 9.0,
        "absoluteFloor": 8.0,
        "cost": 5.0,
        "tenantId": TENANT_B,
    })

    assert "tenantId" not in body.model_dump()


def test_product_create_ignores_client_tenant_and_uses_server_context(monkeypatch):
    database = AsyncDatabase("tenant_access_product_route_create")
    tenant_a = access(database, TENANT_A)
    body = ProductIn.model_validate({
        "brand": "Brand",
        "name": "Product",
        "unit": "kg",
        "standardPrice": 10.0,
        "salesFloor": 9.0,
        "absoluteFloor": 8.0,
        "cost": 5.0,
        "tenantId": TENANT_B,
    })

    async def fixed_sequence(_name):
        return 1

    monkeypatch.setattr(products, "next_seq", fixed_sequence)
    response = run(products.create_product(body, {"id": "u-admin"}, tenant_a))

    assert "tenantId" not in response
    assert database.raw.products.find_one({"id": "p1"})["tenantId"] == TENANT_A


def test_cross_tenant_company_and_upload_look_like_missing_resources():
    database = AsyncDatabase("tenant_access_not_found")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.companies.insert_one({"id": "c1", "name": "Other tenant"}))
    run(tenant_b.uploads.insert_one({"storagePath": "other/file.jpg", "ownerId": "u-b"}))

    with pytest.raises(HTTPException) as company_error:
        run(companies.get_company("c1", {"id": "u-admin", "role": "admin"}, tenant_a))
    with pytest.raises(HTTPException) as upload_error:
        run(products.serve_file("other/file.jpg", tenant_a))

    assert company_error.value.status_code == 404
    assert upload_error.value.status_code == 404


def test_customer_company_visibility_requires_company_in_resolved_tenant():
    database = AsyncDatabase("tenant_access_customer_company_visibility")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.companies.insert_one({"id": "c1", "name": "Other tenant"}))
    user = {"id": "u-customer", "role": "customer", "companyId": "c1"}

    assert run(deps.visible_company_ids(user, tenant_a)) == []
    assert run(deps.visible_company_ids(user, tenant_b)) == ["c1"]


def test_customer_company_visibility_rejects_non_string_database_value():
    database = AsyncDatabase("tenant_access_customer_company_injection")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "name": "Tenant A"}))
    user = {
        "id": "u-corrupt",
        "role": "customer",
        "companyId": {"$ne": None},
    }

    assert run(deps.visible_company_ids(user, tenant_a)) == []


def test_machine_customer_snapshot_rejects_company_from_other_tenant():
    database = AsyncDatabase("tenant_access_machine_customer_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.companies.insert_one({"id": "c1", "name": "Other tenant"}))

    with pytest.raises(HTTPException) as exc:
        run(machines._customer_snapshot(
            {"id": "u-customer", "companyId": "c1", "name": "Customer", "email": "c@example.test"},
            tenant_a,
        ))

    assert exc.value.status_code == 404
    assert exc.value.detail == "Kunde nicht gefunden"


def test_machine_customer_snapshot_rejects_non_string_company_value():
    database = AsyncDatabase("tenant_access_machine_company_injection")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "name": "Tenant A"}))

    with pytest.raises(HTTPException) as exc:
        run(machines._customer_snapshot(
            {
                "id": "u-corrupt",
                "companyId": {"$ne": None},
                "name": "Customer",
                "email": "c@example.test",
            },
            tenant_a,
        ))

    assert exc.value.status_code == 404
    assert exc.value.detail == "Kunde nicht gefunden"


def test_machine_terms_reject_product_from_other_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_machine_product_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({
        "id": "p1",
        "brand": "Other",
        "name": "Tenant",
        "absoluteFloor": 8.0,
    }))
    run(tenant_a.machines.insert_one({"id": "machine-1", "active": True}))
    run(tenant_a.machine_requests.insert_one({
        "id": "mr-1",
        "machineId": "machine-1",
        "type": "purchase",
        "termMonths": 1,
        "machineName": "Machine",
        "customer": {"companyId": "c1", "email": ""},
    }))
    monkeypatch.setattr(machines, "db", database)

    with pytest.raises(HTTPException) as exc:
        run(machines.set_machine_terms(
            "mr-1",
            MachineTermsIn(productId="p1"),
            {"id": "u-admin", "role": "admin"},
            tenant_a,
        ))

    assert exc.value.status_code == 404
    assert exc.value.detail == "Produkt nicht gefunden"
    assert "terms" not in database.raw.machine_requests.find_one({"id": "mr-1"})


def test_application_has_no_direct_unscoped_access_to_converted_collections():
    app_dir = Path(__file__).resolve().parents[1] / "app"
    allowed = {
        Path("database_setup.py"),
        Path("demo_seed.py"),
        Path("tenant_access.py"),
    }
    violations = []
    for path in app_dir.rglob("*.py"):
        relative = path.relative_to(app_dir)
        if relative in allowed or "migrations" in relative.parts:
            continue
        violations.extend(unscoped_collection_accesses(
            path.read_text(encoding="utf-8"),
            str(relative),
        ))

    assert violations == []


@pytest.mark.parametrize("source", [
    "from app.core import db as mongo\nmongo.products.find_one({})",
    "getattr(db, 'companies').find_one({})",
    "collection_name = 'uploads'\ndb[collection_name].find_one({})",
    "raw = db\nraw.customer_prices.update_many({}, {'$set': {'price': 1}})",
    "import app.core as core\ncore.db.price_history.aggregate([])",
    "import app.core as core\nraw = core.db\nraw.orders.find({})",
    "from app import core\nraw: object = core.db\nraw.offers.find({})",
])
def test_static_guard_detects_direct_access_bypass_shapes(source):
    assert unscoped_collection_accesses(source)


@pytest.mark.parametrize(
    "collection_name",
    ["orders", "offers", "invoices", "contracts", "subscriptions"],
)
def test_commercial_collections_separate_same_id_and_hide_legacy_documents(collection_name):
    database = AsyncDatabase(f"tenant_access_commercial_{collection_name}")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(getattr(tenant_a, collection_name).insert_one({"id": "same-id", "marker": "a"}))
    run(getattr(tenant_b, collection_name).insert_one({"id": "same-id", "marker": "b"}))
    database.raw[collection_name].insert_one({"id": "legacy", "marker": "legacy"})

    assert run(getattr(tenant_a, collection_name).find_one({"id": "same-id"}))["marker"] == "a"
    assert run(getattr(tenant_b, collection_name).find_one({"id": "same-id"}))["marker"] == "b"
    assert run(getattr(tenant_a, collection_name).find_one({"id": "legacy"})) is None
    assert run(getattr(tenant_b, collection_name).find_one({"id": "legacy"})) is None


def test_order_create_sets_server_tenant_and_ignores_client_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_order_create")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "name": "Company", "active": True}))
    run(tenant_a.products.insert_one({
        "id": "p1",
        "standardPrice": 12.5,
        "discountTiers": [],
        "active": True,
    }))

    async def fixed_sequence(_name):
        return 7

    monkeypatch.setattr(orders, "next_seq", fixed_sequence)
    body = OrderCreate.model_validate({
        "companyId": "c1",
        "items": [{"productId": "p1", "qty": 2}],
        "tenantId": TENANT_B,
    })
    response = run(orders.create_order(body, {"id": "u1", "role": "admin"}, tenant_a))

    assert "tenantId" not in body.model_dump()
    assert "tenantId" not in response
    assert database.raw.orders.find_one({"id": response["id"]})["tenantId"] == TENANT_A


@pytest.mark.parametrize("foreign_reference", ["company", "product"])
def test_order_create_rejects_cross_tenant_references(monkeypatch, foreign_reference):
    database = AsyncDatabase(f"tenant_access_order_reference_{foreign_reference}")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    company_access = tenant_b if foreign_reference == "company" else tenant_a
    product_access = tenant_b if foreign_reference == "product" else tenant_a
    run(company_access.companies.insert_one({"id": "c1", "active": True}))
    run(product_access.products.insert_one({
        "id": "p1",
        "standardPrice": 10.0,
        "discountTiers": [],
        "active": True,
    }))

    async def sequence_must_not_run(_name):
        raise AssertionError("counter advanced before reference validation")

    monkeypatch.setattr(orders, "next_seq", sequence_must_not_run)
    with pytest.raises(HTTPException) as exc:
        run(orders.create_order(
            OrderCreate(companyId="c1", items=[OrderItemIn(productId="p1", qty=1)]),
            {"id": "u1", "role": "admin"},
            tenant_a,
        ))

    expected = 403 if foreign_reference == "company" else 400
    assert exc.value.status_code == expected
    assert database.raw.orders.count_documents({}) == 0


def test_foreign_and_missing_order_have_identical_status_response():
    database = AsyncDatabase("tenant_access_order_status_not_found")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.orders.insert_one({
        "id": "foreign",
        "companyId": "c-b",
        "status": "Neu",
        "items": [],
    }))

    for order_id in ("foreign", "missing"):
        with pytest.raises(HTTPException) as exc:
            run(orders.set_order_status(
                order_id,
                OrderStatusIn(status="Bestätigt"),
                {"id": "u1", "role": "admin"},
                tenant_a,
            ))
        assert (exc.value.status_code, exc.value.detail) == (404, "Bestellung nicht gefunden")
    assert database.raw.orders.find_one({"id": "foreign"})["status"] == "Neu"


def test_offer_create_is_tenant_scoped_and_rejects_foreign_product(monkeypatch):
    database = AsyncDatabase("tenant_access_offer_create")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({
        "id": "p1",
        "absoluteFloor": 5.0,
        "salesFloor": 8.0,
    }))

    async def sequence_must_not_run(_name):
        raise AssertionError("counter advanced before reference validation")

    monkeypatch.setattr(offers, "next_seq", sequence_must_not_run)
    body = OfferCreate(companyId="c1", items=[OfferItemIn(productId="p1", qty=1, price=10)])
    with pytest.raises(HTTPException) as exc:
        run(offers.create_offer(body, {"id": "u1", "role": "admin"}, tenant_a))

    assert (exc.value.status_code, exc.value.detail) == (400, "Produkt unbekannt")
    assert database.raw.offers.count_documents({}) == 0


def test_offer_create_sets_server_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_offer_valid_create")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_a.products.insert_one({
        "id": "p1",
        "absoluteFloor": 5.0,
        "salesFloor": 8.0,
    }))

    async def fixed_sequence(_name):
        return 3

    monkeypatch.setattr(offers, "next_seq", fixed_sequence)
    response = run(offers.create_offer(
        OfferCreate(
            companyId="c1",
            items=[OfferItemIn(productId="p1", qty=1, price=10)],
        ),
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    stored = database.raw.offers.find_one({"id": response["id"]})
    assert stored["tenantId"] == TENANT_A
    assert "tenantId" not in response


def test_accept_offer_revalidates_product_tenant_before_order(monkeypatch):
    database = AsyncDatabase("tenant_access_offer_accept_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({"id": "p1"}))
    run(tenant_a.offers.insert_one({
        "id": "offer-1",
        "companyId": "c1",
        "status": "Freigegeben",
        "items": [{"productId": "p1", "qty": 1, "price": 10.0}],
    }))

    async def sequence_must_not_run(_name):
        raise AssertionError("counter advanced before reference validation")

    monkeypatch.setattr(offers, "next_seq", sequence_must_not_run)
    with pytest.raises(HTTPException) as exc:
        run(offers.accept_offer(
            "offer-1",
            AcceptOfferIn(),
            {"id": "u1", "role": "admin"},
            tenant_a,
        ))

    assert (exc.value.status_code, exc.value.detail) == (404, "Angebot nicht gefunden")
    assert database.raw.orders.count_documents({}) == 0


def test_invoice_creation_is_scoped_to_order_and_company_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_invoice_references")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c-a", "active": True}))
    run(tenant_a.products.insert_one({
        "id": "p-a",
        "brand": "Brand",
        "name": "Product",
        "unit": "kg",
        "taxRate": 7,
    }))
    run(tenant_b.orders.insert_one({
        "id": "foreign",
        "companyId": "c-a",
        "items": [{"productId": "p-a", "qty": 1, "price": 10.0}],
    }))
    run(tenant_a.orders.insert_one({
        "id": "own-with-foreign-company",
        "companyId": "c-b",
        "items": [{"productId": "p-a", "qty": 1, "price": 10.0}],
    }))

    async def sequence_must_not_run(_name):
        raise AssertionError("counter advanced before reference validation")

    monkeypatch.setattr(billing, "next_seq", sequence_must_not_run)
    user = {"id": "u1", "role": "admin"}
    for order_id in ("foreign", "own-with-foreign-company"):
        with pytest.raises(HTTPException) as exc:
            run(billing.create_invoice_for_order(order_id, user, tenant_a))
        assert (exc.value.status_code, exc.value.detail) == (404, "Bestellung nicht gefunden")
    assert database.raw.invoices.count_documents({}) == 0


def test_invoice_creation_sets_tenant_and_keeps_order_reference_local(monkeypatch):
    database = AsyncDatabase("tenant_access_invoice_valid_create")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_a.products.insert_one({
        "id": "p1",
        "brand": "Brand",
        "name": "Product",
        "unit": "kg",
        "taxRate": 7,
    }))
    run(tenant_a.orders.insert_one({
        "id": "order-1",
        "companyId": "c1",
        "items": [{"productId": "p1", "qty": 1, "price": 10.0}],
    }))

    async def fixed_sequence(_name):
        return 4

    monkeypatch.setattr(billing, "next_seq", fixed_sequence)
    response = run(billing.create_invoice_for_order(
        "order-1",
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    stored = database.raw.invoices.find_one({"id": response["id"]})
    assert stored["tenantId"] == TENANT_A
    assert stored["orderId"] == "order-1"
    assert database.raw.orders.find_one({"id": "order-1"})["invoiceId"] == response["id"]
    assert "tenantId" not in response


def test_foreign_and_missing_invoice_have_identical_pay_response():
    database = AsyncDatabase("tenant_access_invoice_not_found")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.invoices.insert_one({"id": "foreign", "companyId": "c-b", "status": "Offen"}))

    for invoice_id in ("foreign", "missing"):
        with pytest.raises(HTTPException) as exc:
            run(invoices.mark_invoice_paid(
                invoice_id,
                {"id": "u1", "role": "admin"},
                tenant_a,
            ))
        assert (exc.value.status_code, exc.value.detail) == (404, "Rechnung nicht gefunden")
    assert database.raw.invoices.find_one({"id": "foreign"})["status"] == "Offen"


def test_invoice_with_foreign_order_reference_is_fail_closed():
    database = AsyncDatabase("tenant_access_invoice_foreign_order")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.orders.insert_one({"id": "order-b", "companyId": "c1"}))
    run(tenant_a.invoices.insert_one({
        "id": "invoice-a",
        "companyId": "c1",
        "orderId": "order-b",
        "status": "Offen",
    }))

    with pytest.raises(HTTPException) as exc:
        run(invoices.mark_invoice_paid(
            "invoice-a",
            {"id": "u1", "role": "admin"},
            tenant_a,
        ))

    assert (exc.value.status_code, exc.value.detail) == (404, "Rechnung nicht gefunden")
    assert database.raw.invoices.find_one({"id": "invoice-a"})["status"] == "Offen"


def test_invoice_list_hides_cross_tenant_order_reference():
    database = AsyncDatabase("tenant_access_invoice_list_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.orders.insert_one({"id": "order-b", "companyId": "c1"}))
    run(tenant_a.invoices.insert_one({
        "id": "invoice-a",
        "companyId": "c1",
        "orderId": "order-b",
        "date": "2026-01-01",
    }))

    response = run(invoices.get_invoices(
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    assert response == []


def test_contract_list_hides_cross_tenant_product_reference():
    database = AsyncDatabase("tenant_access_contract_list_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({"id": "p1"}))
    run(tenant_a.contracts.insert_one({
        "id": "contract-a",
        "companyId": "c1",
        "productId": "p1",
    }))

    response = run(invoices.get_contracts(
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    assert response == []


def test_subscription_create_rejects_foreign_product_reference():
    database = AsyncDatabase("tenant_access_subscription_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({"id": "p1"}))

    with pytest.raises(HTTPException) as exc:
        run(subscriptions.create_subscription(
            SubscriptionIn(
                companyId="c1",
                items=[OfferItemIn(productId="p1", qty=1, price=10)],
            ),
            {"id": "u1", "role": "admin"},
            tenant_a,
        ))

    assert (exc.value.status_code, exc.value.detail) == (404, "Abo-Referenz nicht gefunden")
    assert database.raw.subscriptions.count_documents({}) == 0


def test_subscription_create_sets_server_tenant():
    database = AsyncDatabase("tenant_access_subscription_valid_create")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_a.products.insert_one({"id": "p1"}))

    response = run(subscriptions.create_subscription(
        SubscriptionIn(
            companyId="c1",
            items=[OfferItemIn(productId="p1", qty=1, price=10)],
        ),
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    stored = database.raw.subscriptions.find_one({"id": response["id"]})
    assert stored["tenantId"] == TENANT_A
    assert "tenantId" not in response


def test_subscription_run_processes_only_resolved_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_subscription_run")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    for scoped_access, marker in ((tenant_a, "a"), (tenant_b, "b")):
        run(scoped_access.companies.insert_one({"id": f"c-{marker}", "active": True}))
        run(scoped_access.products.insert_one({"id": f"p-{marker}"}))
        run(scoped_access.subscriptions.insert_one({
            "id": f"sub-{marker}",
            "companyId": f"c-{marker}",
            "items": [{"productId": f"p-{marker}", "qty": 1, "price": 10.0}],
            "intervalDays": 28,
            "active": True,
            "nextRun": "2020-01-01",
        }))

    async def fixed_sequence(_name):
        return 9

    monkeypatch.setattr(subscriptions, "next_seq", fixed_sequence)
    response = run(subscriptions.run_due_subscriptions(
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    assert response["count"] == 1
    created = database.raw.orders.find_one({"id": response["created"][0]})
    assert created["tenantId"] == TENANT_A
    assert created["fromSubscription"] == "sub-a"
    foreign_subscription = database.raw.subscriptions.find_one({"id": "sub-b"})
    assert foreign_subscription.get("lastRun") is None


def test_machine_contract_write_rejects_cross_tenant_references(monkeypatch):
    database = AsyncDatabase("tenant_access_machine_contract_reference")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_b.products.insert_one({"id": "p1"}))
    run(tenant_a.machines.insert_one({"id": "machine-1", "active": True}))
    run(tenant_a.machine_requests.insert_one({
        "id": "mr-1",
        "machineId": "machine-1",
        "type": "leasing",
        "status": "Angebot",
        "machineName": "Machine",
        "customer": {"companyId": "c1"},
        "terms": {"productId": "p1", "coffeePricePerKg": 10.0},
    }))
    monkeypatch.setattr(machines, "db", database)

    async def sequence_must_not_run(_name):
        raise AssertionError("counter advanced before reference validation")

    monkeypatch.setattr(machines, "next_seq", sequence_must_not_run)
    with pytest.raises(HTTPException) as exc:
        run(machines.accept_machine_offer(
            "mr-1",
            {"id": "u1", "role": "admin"},
            tenant_a,
        ))

    assert (exc.value.status_code, exc.value.detail) == (404, "Anfrage nicht gefunden")
    assert database.raw.contracts.count_documents({}) == 0
    assert database.raw.machine_requests.find_one({"id": "mr-1"})["status"] == "Angebot"


def test_machine_contract_write_sets_server_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_machine_contract_valid")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.companies.insert_one({"id": "c1", "active": True}))
    run(tenant_a.products.insert_one({"id": "p1"}))
    run(tenant_a.machines.insert_one({"id": "machine-1", "active": True}))
    run(tenant_a.machine_requests.insert_one({
        "id": "mr-1",
        "machineId": "machine-1",
        "type": "leasing",
        "status": "Angebot",
        "machineName": "Machine",
        "termMonths": 48,
        "customer": {"companyId": "c1"},
        "terms": {"productId": "p1", "coffeePricePerKg": 10.0},
    }))
    monkeypatch.setattr(machines, "db", database)

    async def fixed_sequence(_name):
        return 5

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(machines, "next_seq", fixed_sequence)
    monkeypatch.setattr(machines, "audit", no_audit)
    run(machines.accept_machine_offer(
        "mr-1",
        {"id": "u1", "role": "admin"},
        tenant_a,
    ))

    stored = database.raw.contracts.find_one({"machineRequestId": "mr-1"})
    assert stored["tenantId"] == TENANT_A
    assert stored["companyId"] == "c1"
    assert stored["productId"] == "p1"


@pytest.mark.parametrize("source", [
    "db.orders.find({})",
    "db['offers'].update_many({}, {'$set': {'status': 'x'}})",
    "getattr(db, 'invoices').find_one_and_update({}, {})",
    "database.contracts.aggregate([{'$lookup': {'from': 'companies'}}])",
    "raw = db\nraw.subscriptions.delete_many({})",
    "db.get_collection('orders').replace_one({}, {})",
    "name = 'offers'\ndb.get_collection(name).find({})",
])
def test_static_guard_covers_commercial_collection_bypasses(source):
    assert unscoped_collection_accesses(source)


def test_machine_catalog_create_list_update_and_delete_are_tenant_scoped(monkeypatch):
    database = AsyncDatabase("tenant_edge_machine_catalog")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(machines, "audit", no_audit)
    body = MachineIn(name="Machine A", price=1000)
    created = run(machines.create_machine(body, {"id": "admin-a"}, tenant_a))
    machine_id = created["id"]
    run(tenant_b.machines.insert_one({"id": machine_id, "name": "Machine B", "price": 2000}))

    assert "tenantId" not in created
    assert database.raw.machines.find_one({"tenantId": TENANT_A})["name"] == "Machine A"
    assert [row["name"] for row in run(machines.list_machines({"role": "admin"}, tenant_a))] == ["Machine A"]
    run(machines.update_machine(machine_id, MachineIn(name="Machine A2", price=1100), {"id": "admin-a"}, tenant_a))
    assert database.raw.machines.find_one({"tenantId": TENANT_A})["name"] == "Machine A2"
    assert database.raw.machines.find_one({"tenantId": TENANT_B})["name"] == "Machine B"
    run(machines.delete_machine(machine_id, {"id": "admin-a"}, tenant_a))
    assert database.raw.machines.find_one({"tenantId": TENANT_A})["active"] is False
    assert database.raw.machines.find_one({"tenantId": TENANT_B}).get("active") is None


def test_foreign_and_missing_machine_delete_are_indistinguishable(monkeypatch):
    database = AsyncDatabase("tenant_edge_machine_delete_missing")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.machines.insert_one({"id": "machine", "active": True}))

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(machines, "audit", no_audit)
    for machine_id in ("machine", "missing"):
        with pytest.raises(HTTPException) as exc:
            run(machines.delete_machine(machine_id, {"id": "admin-a"}, tenant_a))
        assert (exc.value.status_code, exc.value.detail) == (404, "Maschine nicht gefunden")
    assert run(tenant_b.machines.find_one({"id": "machine"}))["active"] is True


def test_machine_request_create_sets_tenant_and_rejects_foreign_machine(monkeypatch):
    database = AsyncDatabase("tenant_edge_machine_request_create")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.machines.insert_one({"id": "own", "name": "Own", "price": 100, "active": True}))
    run(tenant_b.machines.insert_one({"id": "foreign", "name": "Foreign", "price": 100, "active": True}))

    async def fixed_sequence(_name):
        return 4

    monkeypatch.setattr(machines, "next_seq", fixed_sequence)
    user = {"id": "user-a", "role": "customer", "name": "A", "email": "a@example.test"}
    result = run(machines.create_machine_request(MachineRequestIn(machineId="own", type="kauf"), user, tenant_a))
    assert database.raw.machine_requests.find_one({"id": result["id"]})["tenantId"] == TENANT_A
    assert "tenantId" not in result

    with pytest.raises(HTTPException) as exc:
        run(machines.create_machine_request(MachineRequestIn(machineId="foreign", type="kauf"), user, tenant_a))
    assert (exc.value.status_code, exc.value.detail) == (404, "Maschine nicht verfügbar")
    assert database.raw.machine_requests.count_documents({}) == 1


def test_machine_request_list_hides_foreign_legacy_and_cross_tenant_references():
    database = AsyncDatabase("tenant_edge_machine_request_list")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.machines.insert_one({"id": "machine-a", "price": 100}))
    run(tenant_b.machines.insert_one({"id": "machine-b", "price": 100}))
    run(tenant_a.machine_requests.insert_one({
        "id": "own", "machineId": "machine-a", "customer": {"userId": "user-a"}, "createdAt": "2026-01-01",
    }))
    run(tenant_a.machine_requests.insert_one({
        "id": "bad-ref", "machineId": "machine-b", "customer": {"userId": "user-a"}, "createdAt": "2026-01-02",
    }))
    run(tenant_b.machine_requests.insert_one({
        "id": "foreign", "machineId": "machine-b", "customer": {"userId": "user-a"}, "createdAt": "2026-01-03",
    }))
    database.raw.machine_requests.insert_one({
        "id": "legacy", "machineId": "machine-a", "customer": {"userId": "user-a"}, "createdAt": "2026-01-04",
    })

    rows = run(machines.list_machine_requests({"id": "admin-a", "role": "admin"}, tenant_a))
    assert [row["id"] for row in rows] == ["own"]


def test_machine_request_with_foreign_contract_is_not_actionable():
    database = AsyncDatabase("tenant_edge_machine_request_contract")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.machines.insert_one({"id": "machine", "price": 100}))
    run(tenant_b.contracts.insert_one({"id": "contract"}))
    run(tenant_a.machine_requests.insert_one({
        "id": "request", "machineId": "machine", "contractId": "contract",
        "status": "Angebot", "type": "finanzierung", "customer": {"userId": "user-a"},
    }))

    with pytest.raises(HTTPException) as exc:
        run(machines.respond_machine_offer(
            "request", MachineRespondIn(action="decline"),
            {"id": "user-a", "role": "customer"}, tenant_a,
        ))
    assert (exc.value.status_code, exc.value.detail) == (404, "Anfrage nicht gefunden")
    assert run(tenant_a.machine_requests.find_one({"id": "request"}))["status"] == "Angebot"


def _shop_order_input(product_id: str = "product") -> ShopOrderIn:
    return ShopOrderIn(
        items=[ShopItemIn(productId=product_id, qty=1)],
        customer=ShopCustomerIn(name="Shop Customer", email="shop@example.test"),
    )


def test_shop_order_create_sets_tenant_and_rejects_foreign_product(monkeypatch):
    database = AsyncDatabase("tenant_edge_shop_create")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.products.insert_one({
        "id": "own", "brand": "A", "name": "Coffee", "active": True,
        "b2cPrice": 12.0, "taxRate": 7,
    }))
    run(tenant_b.products.insert_one({
        "id": "foreign", "brand": "B", "name": "Coffee", "active": True,
        "b2cPrice": 1.0, "taxRate": 7,
    }))

    async def fixed_sequence(_name):
        return 7

    async def no_email(**_kwargs):
        return True

    monkeypatch.setattr(shop, "next_seq", fixed_sequence)
    monkeypatch.setattr(shop, "send_email", no_email)
    created = run(shop.create_shop_order(_shop_order_input("own"), tenant_a))
    stored = database.raw.shop_orders.find_one({"id": created["id"]})
    assert stored["tenantId"] == TENANT_A
    assert stored["items"][0]["price"] == 12.0
    assert "tenantId" not in created

    with pytest.raises(HTTPException) as exc:
        run(shop.create_shop_order(_shop_order_input("foreign"), tenant_a))
    assert (exc.value.status_code, exc.value.detail) == (400, "Ein Produkt ist nicht mehr verfügbar")
    assert database.raw.shop_orders.count_documents({}) == 1


def test_shop_order_reads_and_status_updates_are_tenant_scoped(monkeypatch):
    database = AsyncDatabase("tenant_edge_shop_mutations")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    base = {"status": "Neu", "createdAt": "2026-01-01", "customer": {"email": ""}, "statusHistory": []}
    run(tenant_a.shop_orders.insert_one({"id": "own", "userId": "user", **base}))
    run(tenant_b.shop_orders.insert_one({"id": "foreign", "userId": "user", **base}))

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(shop, "audit", no_audit)
    listed = run(shop.list_shop_orders({"role": "admin"}, tenant_a))
    mine = run(shop.shop_my_orders({"id": "user", "role": "shopuser"}, tenant_a))
    assert [row["id"] for row in listed] == ["own"]
    assert [row["id"] for row in mine] == ["own"]

    for order_id in ("foreign", "missing"):
        with pytest.raises(HTTPException) as exc:
            run(shop.update_shop_order_status(
                order_id, ShopStatusIn(status="Bestätigt"), {"id": "admin", "role": "admin"}, tenant_a,
            ))
        assert (exc.value.status_code, exc.value.detail) == (404, "Bestellung nicht gefunden")
    assert run(tenant_b.shop_orders.find_one({"id": "foreign"}))["status"] == "Neu"


def test_shop_settings_ignore_legacy_and_are_separate_per_tenant(monkeypatch):
    database = AsyncDatabase("tenant_edge_settings")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    database.raw.settings.insert_one({"_id": "shop", "shippingFee": 999})

    assert run(shop.shop_settings_get(tenant_a))["shippingFee"] == 4.90
    assert database.raw.settings.count_documents({}) == 1

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(shop, "audit", no_audit)
    run(shop.shop_settings_put(ShopSettingsIn(shippingFee=4.5), {"id": "admin"}, tenant_a))
    run(shop.shop_settings_put(ShopSettingsIn(shippingFee=8.5), {"id": "admin"}, tenant_b))
    assert run(shop.shop_settings_get(tenant_a))["shippingFee"] == 4.5
    assert run(shop.shop_settings_get(tenant_b))["shippingFee"] == 8.5
    assert database.raw.settings.count_documents({"tenantId": {"$exists": True}}) == 2


def test_newsletter_tokens_and_codes_cannot_cross_tenants(monkeypatch):
    database = AsyncDatabase("tenant_edge_newsletter")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)

    async def no_email(**_kwargs):
        return True

    monkeypatch.setattr(newsletter, "send_email", no_email)
    body = NewsletterIn(email="same@example.test")
    run(newsletter.newsletter_subscribe(body, tenant_a))
    run(newsletter.newsletter_subscribe(body, tenant_b))
    a_doc = database.raw.newsletter.find_one({"tenantId": TENANT_A})
    b_doc = database.raw.newsletter.find_one({"tenantId": TENANT_B})
    assert a_doc["email"] == b_doc["email"]

    response = run(newsletter.newsletter_unsubscribe(b_doc["unsubToken"], tenant_a))
    assert response.status_code == 404
    assert database.raw.newsletter.find_one({"tenantId": TENANT_B}) is not None

    database.raw.newsletter.update_one(
        {"tenantId": TENANT_B}, {"$set": {"confirmed": True, "code": "SS-CROSS"}},
    )
    assert run(newsletter.validate_code(ValidateCodeIn(code="SS-CROSS"), tenant_a))["valid"] is False
    assert run(newsletter.validate_code(ValidateCodeIn(code="SS-CROSS"), tenant_b))["valid"] is True


class _PushResponse:
    status_code = 200

    def raise_for_status(self):
        return None


class _PushClient:
    def __init__(self):
        self.payloads = []

    async def post(self, path, json):
        self.payloads.append((path, deepcopy(json)))
        return _PushResponse()


def _request(authorization: str | None = None) -> Request:
    headers = [] if authorization is None else [(b"authorization", authorization.encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def test_push_registration_and_broadcast_use_tenant_namespaced_targets(monkeypatch):
    database = AsyncDatabase("tenant_edge_push")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    client = _PushClient()
    monkeypatch.setattr(push, "_client", client)
    body = push.RegisterPushBody(user_id="device", platform="ios", device_token="token")

    run(push.register_push(body, _request(), tenant_a))
    run(push.register_push(body, _request(), tenant_b))
    a_doc = database.raw.push_registrations.find_one({"tenantId": TENANT_A})
    b_doc = database.raw.push_registrations.find_one({"tenantId": TENANT_B})
    assert a_doc["providerUserId"] == f"{TENANT_A}:anon:device"
    assert b_doc["providerUserId"] == f"{TENANT_B}:anon:device"

    run(push.push_broadcast(PushBroadcastIn(title="A", message="Only A"), {"role": "admin"}, tenant_a))
    broadcast = client.payloads[-1][1]
    assert broadcast["recipients"] == [f"{TENANT_A}:anon:device"]
    assert run(push.push_stats({"role": "admin"}, tenant_a)) == {"registered": 1}


def test_tenant_scoped_audit_context_is_persisted_without_response_leak(monkeypatch):
    database = AsyncDatabase("tenant_edge_audit")
    monkeypatch.setattr(core, "db", database)
    run(core.audit(
        {"id": "admin", "email": "admin@example.test", "role": "admin"},
        "edge_action",
        "entity",
        tenant_id=TENANT_A,
    ))
    assert database.raw.audit_log.find_one({"action": "edge_action"})["tenantId"] == TENANT_A


@pytest.mark.parametrize("source", [
    "db.machines.find({})",
    "db['machine_requests'].update_many({}, {'$set': {'status': 'x'}})",
    "getattr(db, 'shop_orders').find_one_and_update({}, {})",
    "database.newsletter.aggregate([{'$lookup': {'from': 'settings'}}])",
    "raw = db\nraw.push_registrations.delete_many({})",
    "db.get_collection('settings').replace_one({}, {})",
    "name = 'machines'\ndb.get_collection(name).bulk_write([])",
])
def test_static_guard_covers_edge_domain_bypass_shapes(source):
    assert unscoped_collection_accesses(source)


@pytest.mark.parametrize("collection_name", [
    "machines",
    "machine_requests",
    "shop_orders",
    "newsletter",
    "settings",
    "push_registrations",
])
def test_edge_collections_reject_client_tenant_in_filters_writes_and_updates(collection_name):
    database = AsyncDatabase(f"tenant_edge_injection_{collection_name}")
    tenant_a = access(database, TENANT_A)
    collection = getattr(tenant_a, collection_name)

    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(collection.insert_one({"id": "row", "tenantId": TENANT_B}))
    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(collection.find_one({"$or": [{"id": "row"}, {"tenantId": TENANT_B}]}))
    with pytest.raises(TenantScopeViolation, match="immutable"):
        run(collection.update_one({"id": "row"}, {"$unset": {"tenantId": ""}}))
    with pytest.raises(TenantScopeViolation, match="immutable"):
        run(collection.update_one({"id": "row"}, {"$rename": {"other": "tenantId"}}))
    assert database.raw[collection_name].count_documents({}) == 0


def test_push_count_is_tenant_scoped_and_rejects_conflicting_filter():
    database = AsyncDatabase("tenant_edge_push_count")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_a.push_registrations.insert_one({"userId": "a"}))
    run(tenant_b.push_registrations.insert_one({"userId": "b"}))

    assert run(tenant_a.push_registrations.count_documents({})) == 1
    with pytest.raises(TenantScopeViolation, match="Conflicting tenantId"):
        run(tenant_a.push_registrations.count_documents({"tenantId": TENANT_B}))


def test_foreign_shop_payment_lookup_never_calls_stripe(monkeypatch):
    database = AsyncDatabase("tenant_edge_shop_payment_foreign")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.shop_orders.insert_one({
        "id": "foreign", "token": "secret", "paymentStatus": "Offen",
        "stripeSessionId": "session", "total": 10,
    }))

    def stripe_must_not_run(*_args, **_kwargs):
        raise AssertionError("Stripe must not be called for another tenant")

    monkeypatch.setattr(shop.stripe.checkout.Session, "retrieve", stripe_must_not_run)
    for order_id in ("foreign", "missing"):
        with pytest.raises(HTTPException) as exc:
            run(shop.shop_payment_status(order_id, tenant_a, token="secret"))
        assert (exc.value.status_code, exc.value.detail) == (404, "Bestellung nicht gefunden")


def test_newsletter_confirmation_token_cannot_cross_tenants(monkeypatch):
    database = AsyncDatabase("tenant_edge_newsletter_confirm")
    tenant_a = access(database, TENANT_A)
    tenant_b = access(database, TENANT_B)
    run(tenant_b.newsletter.insert_one({
        "email": "b@example.test", "confirmed": False,
        "confirmToken": "foreign-token", "unsubToken": "unsubscribe",
    }))

    async def no_email(**_kwargs):
        raise AssertionError("No email may be sent for another tenant")

    monkeypatch.setattr(newsletter, "send_email", no_email)
    response = run(newsletter.newsletter_confirm("foreign-token", tenant_a))
    assert response.status_code == 404
    assert run(tenant_b.newsletter.find_one({"confirmToken": "foreign-token"}))["confirmed"] is False
