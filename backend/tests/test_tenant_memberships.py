from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import os

import jwt
import mongomock
import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm

os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_membership_import"
os.environ["JWT_SECRET"] = "test-only-membership-secret-at-least-32-bytes"
os.environ["APP_ENV"] = "test"

from app import core as app_core, deps
from app.core import JWT_ALGORITHM, hash_pw
from app.models import CompanyUpdateIn, CreateUserIn
from app.routers import auth, companies, users
from app.tenant_access import TenantBusinessAccess
from app.tenancy import (
    MembershipTenantResolver,
    MongoMembershipDirectory,
    MongoTenantDirectory,
    TenantContext,
    TenantMembership,
    TenantMembershipStatus,
    TenantResolutionError,
    TenantResolutionSource,
    TenantResolutionSubject,
    TenantRole,
    TenantStatus,
)


TENANT_A = "tnt_test_a"
TENANT_B = "tnt_test_b"
JWT_SECRET = "test-only-membership-secret-at-least-32-bytes"
app_core.JWT_SECRET = JWT_SECRET
deps.JWT_SECRET = JWT_SECRET


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


def tenant_document(tenant_id: str, *, status: str = "active") -> dict:
    return {
        "id": tenant_id,
        "slug": tenant_id,
        "displayName": tenant_id,
        "legalName": None,
        "status": status,
        "defaultCurrency": "EUR",
        "defaultLocale": "de-DE",
        "timezone": "Europe/Berlin",
    }


def membership_document(
    membership_id: str,
    tenant_id: str,
    user_id: str,
    role: str,
    *,
    status: str = "active",
    company_id: str | None = None,
) -> dict:
    return {
        "id": membership_id,
        "tenantId": tenant_id,
        "userId": user_id,
        "role": role,
        "status": status,
        "companyId": company_id,
        "createdAt": "2026-09-22T00:00:00+00:00",
        "updatedAt": "2026-09-22T00:00:00+00:00",
    }


def tenant_access(
    database: AsyncDatabase,
    tenant_id: str,
    user_id: str = "admin-a",
    role: str = "admin",
    company_id: str | None = None,
) -> tuple[TenantBusinessAccess, dict]:
    context = TenantContext(
        tenant_id=tenant_id,
        actor_user_id=user_id,
        membership_id=f"mbr-{tenant_id}-{user_id}",
        role=role,
        company_id=company_id,
        resolution_source=TenantResolutionSource.MEMBERSHIP,
    )
    access = TenantBusinessAccess(database, context)
    principal = {
        "id": user_id,
        "name": user_id,
        "email": f"{user_id}@example.test",
        "role": role,
        "companyId": company_id,
        "_tenant_context": context,
    }
    return access, principal


def resolver(database: AsyncDatabase) -> MembershipTenantResolver:
    return MembershipTenantResolver(
        MongoTenantDirectory(database),
        MongoMembershipDirectory(database),
    )


def resolve(database: AsyncDatabase, subject: TenantResolutionSubject) -> TenantContext:
    return run(resolver(database).resolve(subject))


def test_membership_domain_enforces_role_and_company_invariants():
    customer = TenantMembership(
        membership_id="mbr-a",
        tenant_id=TENANT_A,
        user_id="user",
        role=TenantRole.CUSTOMER,
        status=TenantMembershipStatus.ACTIVE,
        company_id="company",
    )
    assert customer.company_id == "company"
    with pytest.raises(ValueError, match="require a company"):
        TenantMembership(
            membership_id="mbr-b",
            tenant_id=TENANT_A,
            user_id="user",
            role=TenantRole.CUSTOMER,
            status=TenantMembershipStatus.ACTIVE,
        )
    with pytest.raises(ValueError, match="Only customer"):
        TenantMembership(
            membership_id="mbr-c",
            tenant_id=TENANT_A,
            user_id="user",
            role=TenantRole.ADMIN,
            status=TenantMembershipStatus.ACTIVE,
            company_id="company",
        )


def test_tenant_context_rejects_incomplete_or_public_membership_authority():
    with pytest.raises(ValueError, match="requires actor"):
        TenantContext(
            tenant_id=TENANT_A,
            membership_id="mbr-a",
            role="admin",
            resolution_source=TenantResolutionSource.MEMBERSHIP,
        )
    with pytest.raises(ValueError, match="invalid role"):
        TenantContext(
            tenant_id=TENANT_A,
            actor_user_id="user",
            membership_id="mbr-a",
            role="platform_admin",
            resolution_source=TenantResolutionSource.MEMBERSHIP,
        )
    with pytest.raises(ValueError, match="cannot carry"):
        TenantContext(
            tenant_id=TENANT_A,
            membership_id="mbr-forged",
            role="admin",
            resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
        )


def test_single_active_membership_resolves_complete_context():
    database = AsyncDatabase("membership_single")
    database.raw.tenants.insert_one(tenant_document(TENANT_A))
    database.raw.tenant_memberships.insert_one(
        membership_document("mbr-a", TENANT_A, "user", "customer", company_id="c1")
    )

    context = resolve(database, TenantResolutionSubject(actor_user_id="user"))

    assert context == TenantContext(
        tenant_id=TENANT_A,
        actor_user_id="user",
        membership_id="mbr-a",
        role="customer",
        company_id="c1",
        resolution_source=TenantResolutionSource.MEMBERSHIP,
    )


def test_multi_membership_requires_explicit_validated_tenant_selection():
    database = AsyncDatabase("membership_multiple")
    database.raw.tenants.insert_many([
        tenant_document(TENANT_A),
        tenant_document(TENANT_B),
    ])
    database.raw.tenant_memberships.insert_many([
        membership_document("mbr-a", TENANT_A, "user", "admin"),
        membership_document("mbr-b", TENANT_B, "user", "sales"),
    ])

    with pytest.raises(TenantResolutionError, match="ambiguous"):
        resolve(database, TenantResolutionSubject(actor_user_id="user"))
    context = resolve(database, TenantResolutionSubject(
        actor_user_id="user",
        requested_tenant_id=TENANT_B,
    ))
    assert (context.tenant_id, context.membership_id, context.role) == (
        TENANT_B,
        "mbr-b",
        "sales",
    )


@pytest.mark.parametrize("case", [
    "no_membership",
    "inactive_membership",
    "inactive_tenant",
    "unknown_tenant",
    "foreign_tenant",
    "foreign_membership_id",
    "duplicate_membership",
])
def test_membership_resolution_fails_closed(case):
    database = AsyncDatabase(f"membership_fail_{case}")
    database.raw.tenants.insert_one(
        tenant_document(TENANT_A, status="inactive" if case == "inactive_tenant" else "active")
    )
    if case not in {"no_membership", "unknown_tenant"}:
        database.raw.tenant_memberships.insert_one(membership_document(
            "mbr-a",
            TENANT_A,
            "user",
            "admin",
            status="inactive" if case == "inactive_membership" else "active",
        ))
    if case == "duplicate_membership":
        database.raw.tenant_memberships.insert_one(
            membership_document("mbr-a-duplicate", TENANT_A, "user", "sales")
        )

    subject = TenantResolutionSubject(actor_user_id="user")
    if case == "unknown_tenant":
        database.raw.tenant_memberships.insert_one(
            membership_document("mbr-x", "tnt_unknown", "user", "admin")
        )
    elif case == "foreign_tenant":
        subject = TenantResolutionSubject(
            actor_user_id="user", requested_tenant_id=TENANT_B
        )
    elif case == "foreign_membership_id":
        subject = TenantResolutionSubject(
            actor_user_id="user", requested_membership_id="mbr-forged"
        )

    with pytest.raises(TenantResolutionError):
        resolve(database, subject)


def test_directory_rejects_membership_returned_for_another_user():
    class BadDirectory:
        async def list_memberships_for_user(self, _user_id):
            return (TenantMembership(
                membership_id="mbr-bad",
                tenant_id=TENANT_A,
                user_id="other",
                role=TenantRole.ADMIN,
                status=TenantMembershipStatus.ACTIVE,
            ),)

    class TenantDirectory:
        async def list_tenants(self):
            return ()

    with pytest.raises(TenantResolutionError, match="another actor"):
        run(MembershipTenantResolver(TenantDirectory(), BadDirectory()).resolve(
            TenantResolutionSubject(actor_user_id="user")
        ))


def test_current_user_ignores_global_role_and_revalidates_signed_claims(monkeypatch):
    database = AsyncDatabase("membership_current_user")
    database.raw.tenants.insert_one(tenant_document(TENANT_A))
    database.raw.users.insert_one({
        "id": "user", "email": "user@example.test", "name": "User",
        "role": "customer", "companyId": "legacy-company",
        "hashed_password": hash_pw("password"),
    })
    database.raw.tenant_memberships.insert_one(
        membership_document("mbr-a", TENANT_A, "user", "admin")
    )
    monkeypatch.setattr(deps, "db", database)

    token = jwt.encode(
        {"sub": "user", "role": "sales", "tenant_id": TENANT_A, "membership_id": "mbr-a"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    identity = run(deps.authenticated_identity(token))
    principal = run(deps.current_user(identity))
    assert principal["role"] == "admin"
    assert principal["companyId"] is None

    forged = jwt.encode(
        {"sub": "user", "tenant_id": TENANT_B, "membership_id": "mbr-a"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    with pytest.raises(HTTPException) as exc:
        run(deps.current_user(run(deps.authenticated_identity(forged))))
    assert exc.value.status_code == 403


def test_unknown_global_identity_is_rejected_before_membership_resolution(monkeypatch):
    database = AsyncDatabase("membership_unknown_identity")
    database.raw.tenants.insert_one(tenant_document(TENANT_A))
    database.raw.tenant_memberships.insert_one(
        membership_document("mbr-ghost", TENANT_A, "ghost", "admin")
    )
    monkeypatch.setattr(deps, "db", database)
    token = jwt.encode(
        {"sub": "ghost", "tenant_id": TENANT_A, "membership_id": "mbr-ghost"},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(HTTPException) as exc:
        run(deps.authenticated_identity(token))

    assert exc.value.status_code == 401


def test_internal_login_selects_valid_membership_and_rejects_ambiguous_user(monkeypatch):
    database = AsyncDatabase("membership_login")
    database.raw.tenants.insert_many([
        tenant_document(TENANT_A), tenant_document(TENANT_B),
    ])
    database.raw.users.insert_one({
        "id": "user", "email": "user@example.test", "name": "User",
        "role": "customer", "companyId": "legacy",
        "hashed_password": hash_pw("password"),
    })
    database.raw.tenant_memberships.insert_many([
        membership_document("mbr-a", TENANT_A, "user", "admin"),
        membership_document("mbr-b", TENANT_B, "user", "sales"),
    ])
    monkeypatch.setattr(auth, "db", database)
    monkeypatch.setattr(deps, "db", database)

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth, "global_audit", no_audit)
    form = OAuth2PasswordRequestForm(username="user@example.test", password="password")
    with pytest.raises(HTTPException) as exc:
        run(auth.login(form))
    assert exc.value.status_code == 403

    response = run(auth.login(form, requested_tenant_id=TENANT_B))
    assert response["user"].role == "sales"
    payload = jwt.decode(response["access_token"], JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["tenant_id"] == TENANT_B
    assert payload["membership_id"] == "mbr-b"


def test_tenant_admin_user_list_hides_foreign_and_legacy_identities(monkeypatch):
    database = AsyncDatabase("membership_user_list")
    access_a, admin_a = tenant_access(database, TENANT_A)
    access_b, _admin_b = tenant_access(database, TENANT_B, user_id="admin-b")
    database.raw.users.insert_many([
        {"id": "admin-a", "email": "a@example.test", "name": "A"},
        {"id": "user-a", "email": "ua@example.test", "name": "UA"},
        {"id": "user-b", "email": "ub@example.test", "name": "UB"},
        {"id": "legacy", "email": "legacy@example.test", "name": "Legacy"},
    ])
    run(access_a.tenant_memberships.insert_one(
        membership_document("mbr-admin-a", TENANT_A, "admin-a", "admin")
    ))
    run(access_a.tenant_memberships.insert_one(
        membership_document("mbr-user-a", TENANT_A, "user-a", "sales")
    ))
    run(access_b.tenant_memberships.insert_one(
        membership_document("mbr-user-b", TENANT_B, "user-b", "admin")
    ))
    monkeypatch.setattr(users, "db", database)

    rows = run(users.list_users(admin_a, access_a))

    assert {row["id"] for row in rows} == {"admin-a", "user-a"}
    assert {row["role"] for row in rows} == {"admin", "sales"}


def test_create_user_writes_global_identity_and_server_owned_membership(monkeypatch):
    database = AsyncDatabase("membership_create_user")
    access_a, admin_a = tenant_access(database, TENANT_A)
    run(access_a.companies.insert_one({"id": "c1", "name": "Company"}))
    monkeypatch.setattr(users, "db", database)

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(users, "global_audit", no_audit)
    monkeypatch.setattr(users, "tenant_audit", no_audit)
    result = run(users.create_user(
        CreateUserIn(
            name="Customer",
            email="customer@example.test",
            role="customer",
            companyId="c1",
        ),
        admin_a,
        access_a,
    ))

    identity = database.raw.users.find_one({"id": result["id"]})
    membership = database.raw.tenant_memberships.find_one({"userId": result["id"]})
    assert "tenantId" not in identity
    assert membership["tenantId"] == TENANT_A
    assert membership["role"] == "customer"
    assert membership["companyId"] == "c1"


def test_create_user_compensates_identity_and_new_company_when_membership_fails(
    monkeypatch,
):
    database = AsyncDatabase("membership_create_user_compensation")
    access_a, admin_a = tenant_access(database, TENANT_A)
    monkeypatch.setattr(users, "db", database)

    async def fail_membership(*_args, **_kwargs):
        raise RuntimeError("membership write failed")

    monkeypatch.setattr(access_a.tenant_memberships, "insert_one", fail_membership)

    with pytest.raises(RuntimeError, match="membership write failed"):
        run(users.create_user(
            CreateUserIn(
                name="Customer",
                email="customer@example.test",
                role="customer",
                newCompany={
                    "name": "New Company",
                    "city": "Berlin",
                    "email": "company@example.test",
                    "phone": "030123",
                },
            ),
            admin_a,
            access_a,
        ))

    assert database.raw.users.count_documents({}) == 0
    assert database.raw.tenant_memberships.count_documents({}) == 0
    assert database.raw.companies.count_documents({"tenantId": TENANT_A}) == 0


def test_tenant_admin_cannot_reset_foreign_or_multi_tenant_identity(monkeypatch):
    database = AsyncDatabase("membership_reset_scope")
    access_a, admin_a = tenant_access(database, TENANT_A)
    access_b, _ = tenant_access(database, TENANT_B, user_id="admin-b")
    database.raw.users.insert_many([
        {"id": "foreign", "email": "foreign@example.test", "hashed_password": hash_pw("password")},
        {"id": "multi", "email": "multi@example.test", "hashed_password": hash_pw("password")},
    ])
    run(access_b.tenant_memberships.insert_one(
        membership_document("mbr-foreign", TENANT_B, "foreign", "customer", company_id="c")
    ))
    run(access_a.tenant_memberships.insert_one(
        membership_document("mbr-multi-a", TENANT_A, "multi", "admin")
    ))
    run(access_b.tenant_memberships.insert_one(
        membership_document("mbr-multi-b", TENANT_B, "multi", "sales")
    ))
    monkeypatch.setattr(users, "db", database)

    with pytest.raises(HTTPException) as foreign:
        run(users.admin_reset_password("foreign", admin_a, access_a))
    assert foreign.value.status_code == 404
    with pytest.raises(HTTPException) as multi:
        run(users.admin_reset_password("multi", admin_a, access_a))
    assert multi.value.status_code == 409

    database.raw.tenant_memberships.update_one(
        {"id": "mbr-multi-b"}, {"$set": {"status": "inactive"}}
    )
    with pytest.raises(HTTPException) as inactive_foreign:
        run(users.admin_reset_password("multi", admin_a, access_a))
    assert inactive_foreign.value.status_code == 409


def test_sales_and_customer_visibility_use_membership_context_not_global_fields():
    database = AsyncDatabase("membership_company_visibility")
    sales_access, sales = tenant_access(
        database, TENANT_A, user_id="shared-user", role="sales"
    )
    other_access, _ = tenant_access(database, TENANT_B)
    run(sales_access.companies.insert_one({
        "id": "same", "active": True, "assignedSalesRepId": "shared-user",
    }))
    run(other_access.companies.insert_one({
        "id": "same", "active": True, "assignedSalesRepId": "shared-user",
    }))
    assert run(deps.visible_company_ids(sales, sales_access)) == ["same"]

    customer_access, customer = tenant_access(
        database, TENANT_A, user_id="customer", role="customer", company_id="same"
    )
    customer["companyId"] = "forged"
    with pytest.raises(HTTPException) as exc:
        run(deps.visible_company_ids(customer, customer_access))
    assert exc.value.status_code == 403


def test_company_sales_assignment_requires_same_tenant_active_staff_membership():
    database = AsyncDatabase("membership_sales_assignment")
    access_a, admin_a = tenant_access(database, TENANT_A)
    access_b, _ = tenant_access(database, TENANT_B)
    run(access_a.companies.insert_one({"id": "c1", "name": "Company"}))
    run(access_b.tenant_memberships.insert_one(
        membership_document("mbr-sales-b", TENANT_B, "sales", "sales")
    ))
    body = CompanyUpdateIn(name="Company", assignedSalesRepId="sales")

    with pytest.raises(HTTPException) as exc:
        run(companies.update_company("c1", body, admin_a, access_a))
    assert (exc.value.status_code, exc.value.detail) == (
        400,
        "Vertrieb ist für diesen Tenant nicht verfügbar",
    )


def test_shop_identity_remains_global_and_has_no_implicit_membership(monkeypatch):
    database = AsyncDatabase("membership_shop_boundary")
    database.raw.users.insert_one({
        "id": "shop", "email": "shop@example.test", "name": "Shop",
        "role": "shopuser", "hashed_password": hash_pw("password"),
    })
    monkeypatch.setattr(deps, "db", database)
    token = jwt.encode({"sub": "shop"}, JWT_SECRET, algorithm=JWT_ALGORITHM)
    identity = run(deps.authenticated_identity(token))
    assert identity["role"] == "shopuser"
    with pytest.raises(HTTPException) as exc:
        run(deps.current_user(identity))
    assert exc.value.status_code == 403
    assert database.raw.tenant_memberships.count_documents({}) == 0
