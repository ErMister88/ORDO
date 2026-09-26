from __future__ import annotations

import asyncio
import json
import os

import mongomock
import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_package13")
os.environ.setdefault("JWT_SECRET", "test-only-package13-secret-at-least-32-bytes")
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("TENANCY_MODE", "single")
os.environ.setdefault("DEFAULT_TENANT_ID", "tnt_ss_0001")

from app.migrations.registry import get_migrations
from app.migrations.versions import v0013_production_hardening as migration13
from app.pagination import bounded_list, validate_page
from app.production_config import assert_safe_startup_configuration, validate_runtime_configuration
from app.routers import companies, orders, shop
from app.runtime_security import AbuseRule, RuntimeSecurityMiddleware
from app.tenant_access import TenantBusinessAccess, TenantScopeViolation
from app.tenancy import TenantContext, TenantResolutionSource
from test_customer_commerce_platform import AsyncCollection, AsyncCursor, run
from test_package11_operational_reliability import CapabilityDatabase
from scripts.validate_production import build_report


class AggregationCollection(AsyncCollection):
    def aggregate(self, pipeline, *args, **kwargs):
        return AsyncCursor(self.collection.aggregate(pipeline, *args, **kwargs))


class AggregateDatabase:
    def __init__(self, name: str):
        self.raw = mongomock.MongoClient(tz_aware=True)[name]
        self.calls: dict[tuple[str, str], int] = {}

    def __getitem__(self, name):
        parent = self

        class CountingCollection(AggregationCollection):
            def find(self, *args, **kwargs):
                parent.calls[(name, "find")] = parent.calls.get((name, "find"), 0) + 1
                return super().find(*args, **kwargs)

            def aggregate(self, *args, **kwargs):
                parent.calls[(name, "aggregate")] = parent.calls.get((name, "aggregate"), 0) + 1
                return super().aggregate(*args, **kwargs)

        return CountingCollection(self.raw[name])


def scoped(
    database, tenant: str = "tenant-a", *, role: str = "admin",
    actor: str = "admin", company_id: str | None = None,
) -> TenantBusinessAccess:
    return TenantBusinessAccess(database, TenantContext(
        tenant_id=tenant, actor_user_id=actor, membership_id=f"m-{actor}", role=role,
        resolution_source=TenantResolutionSource.MEMBERSHIP, company_id=company_id,
        default_currency="EUR",
    ))


def production_environment() -> dict[str, str]:
    return {
        "APP_ENV": "production",
        "APP_URL": "https://api.ordo.cloud",
        "CORS_ORIGINS": "https://app.ordo.cloud",
        "MONGO_URL": "mongodb+srv://cluster.mongodb.net/ordo",
        "DB_NAME": "ordo_production",
        "JWT_SECRET": "a" * 64,
        "TENANCY_MODE": "single",
        "DEFAULT_TENANT_ID": "tnt_ss_0001",
        "PAYMENTS_ENABLED": "false",
        "STORAGE_ENABLED": "false",
        "EMAIL_ENABLED": "false",
        "BACKGROUND_JOBS_ENABLED": "false",
        "DEMO_SEED_ENABLED": "false",
    }


def test_production_configuration_is_secret_safe_and_optional_capabilities_can_be_disabled():
    environment = production_environment()
    result = validate_runtime_configuration(environment)

    assert result["status"] == "READY" and result["ready"] is True
    assert {row["status"] for row in result["checks"] if row["name"] in {"payments", "storage", "email", "worker"}} == {"not_configured"}
    encoded = json.dumps(result)
    assert environment["JWT_SECRET"] not in encoded
    assert environment["MONGO_URL"] not in encoded


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    [
        ("CORS_ORIGINS", "*", "cors"),
        ("APP_URL", "http://api.ordo.example", "app_url"),
        ("JWT_SECRET", "replace-with-a-long-random-secret", "auth_secret"),
        ("DB_NAME", "ordo_staging", "database"),
        ("DEMO_SEED_ENABLED", "true", "demo_isolation"),
    ],
)
def test_production_configuration_rejects_unsafe_values(field, value, failed_check):
    environment = production_environment()
    environment[field] = value
    result = validate_runtime_configuration(environment)

    assert result["status"] == "NOT_READY" and result["ready"] is False
    assert next(row for row in result["checks"] if row["name"] == failed_check)["status"] == "invalid"
    with pytest.raises(RuntimeError, match=failed_check):
        assert_safe_startup_configuration(environment)


@pytest.mark.parametrize("app_env", ["", "productoin", " production-now "])
def test_startup_guard_rejects_missing_or_unknown_environment(app_env):
    environment = production_environment()
    environment["APP_ENV"] = app_env
    with pytest.raises(RuntimeError, match="environment"):
        assert_safe_startup_configuration(environment)


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    [
        ("MONGO_URL", "mongodb://localhost:27017/ordo?tls=true", "database"),
        ("APP_URL", "https://localhost", "app_url"),
        ("CORS_ORIGINS", "https://app.ordo.cloud/path", "cors"),
    ],
)
def test_production_configuration_rejects_local_or_non_origin_targets(field, value, failed_check):
    environment = production_environment()
    environment[field] = value
    result = validate_runtime_configuration(environment)
    assert next(row for row in result["checks"] if row["name"] == failed_check)["status"] == "invalid"


def test_enabled_provider_placeholders_are_rejected():
    environment = production_environment()
    environment.update({
        "PAYMENTS_ENABLED": "true", "STRIPE_API_KEY": "sk_live_replace_me_with_real_secret",
        "STRIPE_WEBHOOK_SECRET": "whsec_replace_me",
        "STORAGE_ENABLED": "true", "STORAGE_BACKEND": "s3",
        "S3_ENDPOINT_URL": "https://s3.example.invalid", "S3_BUCKET": "replace-me",
        "S3_ACCESS_KEY_ID": "replace-me", "S3_SECRET_ACCESS_KEY": "replace-me",
        "EMAIL_ENABLED": "true", "EMAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.example.invalid",
        "SMTP_FROM_EMAIL": "no-reply@ordo.cloud", "SMTP_USERNAME": "replace-me", "SMTP_PASSWORD": "replace-me",
    })
    result = validate_runtime_configuration(environment)
    invalid = {row["name"] for row in result["checks"] if row["status"] == "invalid"}
    assert {"payments", "storage", "email"} <= invalid


def test_enabled_providers_accept_complete_production_shaped_configuration():
    environment = production_environment()
    environment.update({
        "PAYMENTS_ENABLED": "true", "STRIPE_API_KEY": "sk_live_" + "a" * 48,
        "STRIPE_WEBHOOK_SECRET": "whsec_" + "b" * 32,
        "STORAGE_ENABLED": "true", "STORAGE_BACKEND": "s3", "S3_ENDPOINT_URL": "",
        "S3_BUCKET": "ordo-production-files", "S3_REGION": "eu-central-1",
        "S3_ACCESS_KEY_ID": "A" * 20, "S3_SECRET_ACCESS_KEY": "c" * 40,
        "EMAIL_ENABLED": "true", "EMAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.ordo.cloud",
        "SMTP_FROM_EMAIL": "no-reply@ordo.cloud", "SMTP_USERNAME": "mailer-user",
        "SMTP_PASSWORD": "d" * 24,
    })
    result = validate_runtime_configuration(environment)
    provider_statuses = {
        row["name"]: row["status"] for row in result["checks"]
        if row["name"] in {"payments", "storage", "email"}
    }
    assert provider_statuses == {"payments": "configured", "storage": "configured", "email": "configured"}


def test_staging_payments_require_test_mode_credentials():
    environment = production_environment()
    environment.update({
        "APP_ENV": "staging", "APP_URL": "https://ordo-staging-app.onrender.com",
        "CORS_ORIGINS": "https://ordo-staging-app.onrender.com", "DB_NAME": "ordo_staging",
        "PAYMENTS_ENABLED": "true", "STRIPE_API_KEY": "sk_test_" + "a" * 48,
        "STRIPE_WEBHOOK_SECRET": "whsec_" + "b" * 32,
    })
    assert validate_runtime_configuration(environment)["ready"] is True
    environment["STRIPE_API_KEY"] = "sk_live_" + "a" * 48
    result = validate_runtime_configuration(environment)
    assert next(row for row in result["checks"] if row["name"] == "payments")["status"] == "invalid"


def test_enabled_capabilities_require_complete_provider_configuration():
    environment = production_environment()
    environment.update({
        "PAYMENTS_ENABLED": "true", "STRIPE_API_KEY": "sk_test_wrong", "STRIPE_WEBHOOK_SECRET": "",
        "STORAGE_ENABLED": "true", "STORAGE_BACKEND": "local",
        "EMAIL_ENABLED": "true", "EMAIL_BACKEND": "smtp", "SMTP_HOST": "", "SMTP_FROM_EMAIL": "",
        "BACKGROUND_JOBS_ENABLED": "true", "WORKER_SERVICE_ENABLED": "false",
    })
    result = validate_runtime_configuration(environment)
    invalid = {row["name"] for row in result["checks"] if row["status"] == "invalid"}
    assert {"payments", "storage", "email", "worker"} <= invalid


def test_complete_validator_includes_database_and_exact_schema(monkeypatch):
    environment = production_environment()
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    database = CapabilityDatabase("package13_complete_validator")
    for migration in get_migrations():
        database.raw.schema_migrations.insert_one({
            "version": migration.version, "status": "completed", "checksum": migration.checksum,
        })
    report = run(build_report(database))
    assert report["status"] == "READY"
    assert report["runtime"]["capabilities"]["schema"]["expectedVersion"] == 13
    assert report["runtime"]["capabilities"]["schema"]["appliedVersion"] == 13


def test_pagination_bounds_and_offset_are_enforced_without_unbounded_reads():
    database = AggregateDatabase("package13_pagination")
    access = scoped(database)
    for index in range(10):
        run(access.orders.insert_one({"id": f"order-{index:02d}", "companyId": "company-a", "createdAt": f"2026-09-{index + 1:02d}"}))

    rows = run(bounded_list(access.orders.find({}).sort("id", 1), limit=3, offset=4))
    assert [row["id"] for row in rows] == ["order-04", "order-05", "order-06"]
    for invalid in ((0, 0), (501, 0), (10, -1), (True, 0)):
        with pytest.raises(HTTPException) as exc:
            validate_page(*invalid)
        assert exc.value.status_code == 422


def test_company_list_uses_one_tenant_aggregation_instead_of_order_n_plus_one():
    database = AggregateDatabase("package13_company_n_plus_one")
    access = scoped(database)
    for index in range(4):
        run(access.companies.insert_one({"id": f"company-{index}", "name": f"Customer {index}", "active": True}))
        run(access.orders.insert_one({"id": f"order-{index}", "companyId": f"company-{index}", "createdAt": "2026-09-20T10:00:00+00:00"}))

    rows = run(companies.get_companies(
        {"id": "admin", "role": "admin", "_tenant_context": access.context}, access,
    ))
    assert len(rows) == 4 and all(row["daysSinceLastOrder"] is not None for row in rows)
    assert database.calls.get(("orders", "aggregate")) == 1
    assert database.calls.get(("orders", "find"), 0) == 0


def test_company_page_and_shop_catalog_remain_bounded_at_synthetic_scale():
    database = AggregateDatabase("package13_synthetic_scale")
    access = scoped(database)
    database.raw.companies.insert_many([
        {"tenantId": "tenant-a", "id": f"company-{index:04d}", "name": f"Customer {index:04d}", "active": True}
        for index in range(1_000)
    ])
    database.raw.orders.insert_many([
        {"tenantId": "tenant-a", "id": f"order-{index:04d}", "companyId": f"company-{index:04d}", "createdAt": "2026-09-20T10:00:00+00:00"}
        for index in range(1_000)
    ])
    rows = run(companies.get_companies(
        {"id": "admin", "role": "admin", "_tenant_context": access.context}, access,
        limit=500, offset=500,
    ))
    assert len(rows) == 500
    assert database.calls.get(("orders", "aggregate")) == 1

    database.raw.products.insert_many([
        {
            "tenantId": "tenant-a", "id": f"product-{index:03d}", "name": f"Product {index:03d}",
            "active": True, "b2cAvailable": True, "b2cPrice": 10.0, "b2cPriceMinor": 1000,
            "currency": "EUR", "taxRate": 19, "b2cTiers": [],
        }
        for index in range(150)
    ])
    product_reads_before = database.calls.get(("products", "find"), 0)
    catalog = run(shop.shop_products(access, limit=100, offset=0))
    assert len(catalog) == 100
    assert database.calls.get(("products", "find"), 0) - product_reads_before == 1


def test_tenant_aggregation_is_read_only_and_cannot_lookup_or_write_other_collections():
    database = AggregateDatabase("package13_aggregate_guard")
    access = scoped(database)
    run(access.orders.insert_one({"id": "own"}))
    run(scoped(database, "tenant-b").orders.insert_one({"id": "foreign"}))
    rows = run(access.orders.aggregate([{"$group": {"_id": "$tenantId", "count": {"$sum": 1}}}]).to_list(10))
    assert rows == [{"count": 1, "_id": "tenant-a"}]
    for stage in ({"$lookup": {"from": "orders"}}, {"$out": "orders"}, {"$merge": "orders"}):
        with pytest.raises(TenantScopeViolation):
            access.orders.aggregate([stage])


def test_bounded_order_list_remains_cross_tenant_isolated():
    database = AggregateDatabase("package13_order_isolation")
    own = scoped(database, role="customer", actor="customer", company_id="company-a")
    foreign = scoped(database, "tenant-b")
    run(own.companies.insert_one({"id": "company-a", "name": "A"}))
    run(foreign.companies.insert_one({"id": "company-a", "name": "B"}))
    run(own.orders.insert_one({"id": "own", "companyId": "company-a", "createdAt": "2026-09-20"}))
    run(foreign.orders.insert_one({"id": "foreign", "companyId": "company-a", "createdAt": "2026-09-21"}))
    rows = run(orders.get_orders(
        {"id": "customer", "role": "customer", "companyId": "company-a", "_tenant_context": own.context},
        own, limit=10, offset=0,
    ))
    assert [row["id"] for row in rows] == ["own"]


class RateDatabase:
    def __init__(self):
        self.raw = mongomock.MongoClient(tz_aware=True).package13_rate
        self.auth_rate_limits = AsyncCollection(self.raw.auth_rate_limits)


async def call_middleware(middleware, *, method="GET", path="/api/shop/products", body=b"", headers=None):
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware({
        "type": "http", "method": method, "path": path,
        "headers": headers or [], "client": ("203.0.113.4", 1234),
    }, receive, send)
    return messages


def test_runtime_middleware_adds_security_headers_and_rejects_large_body():
    called = False

    async def app(_scope, receive, send):
        nonlocal called
        called = True
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    middleware = RuntimeSecurityMiddleware(app, database=RateDatabase())
    messages = asyncio.run(call_middleware(middleware, path="/api/health/live"))
    headers = dict(next(row for row in messages if row["type"] == "http.response.start")["headers"])
    assert called is True
    assert headers[b"x-content-type-options"] == b"nosniff"
    assert headers[b"x-frame-options"] == b"DENY"
    assert b"frame-ancestors" in headers[b"content-security-policy"]

    called = False
    messages = asyncio.run(call_middleware(
        middleware, method="POST", path="/api/orders", headers=[(b"content-length", str(3 * 1024 * 1024).encode())],
    ))
    assert called is False
    assert next(row for row in messages if row["type"] == "http.response.start")["status"] == 413

    called = False
    messages = asyncio.run(call_middleware(
        middleware, method="POST", path="/api/upload", body=b"x" * (3 * 1024 * 1024),
    ))
    assert called is True
    assert next(row for row in messages if row["type"] == "http.response.start")["status"] == 200


def test_runtime_rate_limit_is_shared_and_fails_closed(monkeypatch):
    async def app(_scope, receive, send):
        await receive()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    monkeypatch.setattr("app.runtime_security.ABUSE_RULES", (AbuseRule("GET", "/api/shop/products", 2, 60),))
    middleware = RuntimeSecurityMiddleware(app, database=RateDatabase())
    statuses = []
    for _ in range(3):
        messages = asyncio.run(call_middleware(middleware))
        statuses.append(next(row for row in messages if row["type"] == "http.response.start")["status"])
    assert statuses == [200, 200, 429]


def test_migration_13_is_additive_tenant_first_and_registered_after_12():
    database = mongomock.MongoClient().ordo_test_package13_migration
    plan = migration13.inspect(database)
    result = migration13.apply(database, type("Context", (), {"checkpoint": lambda self: None})())
    assert plan.expected_changes["documentsChanged"] == 0
    assert result == {"indexesEnsured": len(migration13.INDEXES), "documentsChanged": 0}
    assert get_migrations()[-1].version == 13 and get_migrations()[-1].depends_on == (12,)
    assert all(keys[0][0] == "tenantId" for collection, keys, _name, _options in migration13.INDEXES if collection != "auth_rate_limits")
    assert all(database[name].count_documents({}) == 0 for name in database.list_collection_names())
