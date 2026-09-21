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

# Application imports create a lazy Motor client. Pin it to a non-routable
# test-only target before importing any app module.
os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_tenant_business_import"
os.environ["JWT_SECRET"] = "test-only"
os.environ["APP_ENV"] = "test"

from app import deps
from app.models import CustomerPriceIn, MachineTermsIn, ProductIn
from app.routers import companies, machines, orders, pricing, products
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
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Name):
                continue
            if node.value.id not in database_names:
                continue
            for target in node.targets:
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


def test_fixed_customer_price_still_precedes_quantity_tiers(monkeypatch):
    database = AsyncDatabase("tenant_access_pricing_regression")
    tenant_a = access(database, TENANT_A)
    run(tenant_a.customer_prices.insert_one({
        "companyId": "c1",
        "productId": "p1",
        "price": 15.9,
    }))
    monkeypatch.setattr(orders, "db", database)
    product = {
        "id": "p1",
        "standardPrice": 16.9,
        "discountTiers": [{"minQty": 50, "price": 14.0}],
    }

    assert run(orders._resolve_unit_price(tenant_a, "c1", product, 100)) == 15.9


def test_contract_price_lookup_is_fail_closed_to_resolved_tenant(monkeypatch):
    database = AsyncDatabase("tenant_access_contract_price_edge")
    tenant_a = access(database, TENANT_A)
    monkeypatch.setattr(orders, "db", database)
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
    run(tenant_b.products.insert_one({
        "id": "p1",
        "brand": "Other",
        "name": "Tenant",
        "absoluteFloor": 8.0,
    }))
    database.raw.machine_requests.insert_one({
        "id": "mr-1",
        "type": "purchase",
        "termMonths": 1,
        "machineName": "Machine",
        "customer": {"companyId": "c1", "email": ""},
    })
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
])
def test_static_guard_detects_direct_access_bypass_shapes(source):
    assert unscoped_collection_accesses(source)
