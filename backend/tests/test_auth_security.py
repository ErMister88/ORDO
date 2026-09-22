from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import os

import jwt
import mongomock
import pytest
from fastapi import HTTPException
from fastapi.security import OAuth2PasswordRequestForm

os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_auth_security_import"
os.environ["JWT_SECRET"] = "test-only-auth-security-secret-at-least-32-bytes"
os.environ["APP_ENV"] = "test"

from app import auth_security, deps
from app.auth_security import (
    AuthRateLimitExceeded,
    MongoAuthRateLimiter,
    RateLimitKey,
    decode_access_token,
    issue_shop_token,
    issue_tenant_token,
    rotate_credentials,
)
from app.core import hash_pw, validate_jwt_configuration, verify_pw
from app.models import ChangePwIn, ResetPwIn, ShopLoginIn
from app.routers import auth, shop, users
from app.tenant_access import TenantBusinessAccess
from app.tenancy import TenantContext, TenantResolutionSource


TEST_SECRET = "test-only-auth-security-secret-at-least-32-bytes"
TENANT_A = "tnt_auth_a"
TENANT_B = "tnt_auth_b"
auth_security.JWT_SECRET = TEST_SECRET


@pytest.fixture(autouse=True)
def _stable_token_secret(monkeypatch):
    monkeypatch.setattr(auth_security, "JWT_SECRET", TEST_SECRET)


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

    async def find_one_and_update(self, *args, **kwargs):
        return self._collection.find_one_and_update(*args, **kwargs)

    async def delete_one(self, *args, **kwargs):
        return self._collection.delete_one(*args, **kwargs)

    async def delete_many(self, *args, **kwargs):
        return self._collection.delete_many(*args, **kwargs)

    async def count_documents(self, *args, **kwargs):
        return self._collection.count_documents(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name: str):
        assert name not in {"ordo_staging", "ordo_production"}
        self.raw = mongomock.MongoClient(tz_aware=True)[name]

    def __getitem__(self, name):
        return AsyncCollection(self.raw[name])

    def __getattr__(self, name):
        return self[name]


class FakeRequest:
    class Client:
        host = "203.0.113.10"

    client = Client()


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
    role: str = "admin",
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


def identity_document(
    user_id: str,
    *,
    email: str | None = None,
    role: str = "admin",
    password: str = "correct-password",
    active: bool = True,
    auth_version: int | None = 0,
    must_change: bool = False,
) -> dict:
    document = {
        "id": user_id,
        "email": email or f"{user_id}@example.test",
        "name": user_id,
        "role": role,
        "active": active,
        "hashed_password": hash_pw(password),
        "must_change_password": must_change,
    }
    if auth_version is not None:
        document["authVersion"] = auth_version
    return document


def context(
    user_id: str = "user",
    *,
    tenant_id: str = TENANT_A,
    membership_id: str = "mbr-user",
    role: str = "admin",
    company_id: str | None = None,
) -> TenantContext:
    if role == "customer" and company_id is None:
        company_id = f"company-{user_id}"
    return TenantContext(
        tenant_id=tenant_id,
        actor_user_id=user_id,
        membership_id=membership_id,
        role=role,
        company_id=company_id,
        resolution_source=TenantResolutionSource.MEMBERSHIP,
    )


def configure_database(monkeypatch, database: AsyncDatabase) -> None:
    for module in (deps, auth, shop, users):
        monkeypatch.setattr(module, "db", database)


def seed_tenant_user(
    database: AsyncDatabase,
    *,
    user_id: str = "user",
    role: str = "admin",
    active: bool = True,
    must_change: bool = False,
) -> tuple[dict, TenantContext]:
    identity = identity_document(
        user_id,
        role=role,
        active=active,
        must_change=must_change,
    )
    tenant_context = context(user_id, role=role)
    database.raw.users.insert_one(identity)
    database.raw.tenants.insert_one(tenant_document(TENANT_A))
    database.raw.tenant_memberships.insert_one(
        membership_document(
            "mbr-user",
            TENANT_A,
            user_id,
            role,
            company_id=tenant_context.company_id,
        )
    )
    return identity, tenant_context


def resign(token: str, **changes) -> str:
    payload = jwt.decode(token, options={"verify_signature": False})
    payload.update(changes)
    return jwt.encode(payload, TEST_SECRET, algorithm="HS256")


def test_token_profiles_reject_type_audience_scope_and_cross_profile_use():
    tenant_identity = identity_document("tenant-user")
    shop_identity = identity_document("shop-user", role="shopuser")
    tenant_token = issue_tenant_token(
        tenant_identity,
        context("tenant-user", membership_id="mbr-tenant"),
    )
    shop_token = issue_shop_token(shop_identity)

    assert decode_access_token(tenant_token, "tenant")["token_type"] == "tenant"
    assert decode_access_token(shop_token, "shop")["token_type"] == "shop"
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(shop_token, "tenant")
    with pytest.raises(jwt.PyJWTError):
        decode_access_token(tenant_token, "shop")

    for changed in (
        {"token_type": "shop"},
        {"aud": "ordo-shop"},
        {"aud": ["ordo-tenant", "attacker"]},
        {"scope": "shop:access"},
        {"iss": "attacker"},
    ):
        with pytest.raises(jwt.PyJWTError):
            decode_access_token(resign(tenant_token, **changed), "tenant")


def test_production_jwt_configuration_fails_closed():
    validate_jwt_configuration("test", "short", "HS256", 1)
    validate_jwt_configuration(" staging ", "x" * 32, "HS256", 720)
    with pytest.raises(RuntimeError, match="at least 32"):
        validate_jwt_configuration("PRODUCTION", "short", "HS256", 720)
    with pytest.raises(RuntimeError, match="HS256"):
        validate_jwt_configuration("production", "x" * 32, "HS512", 720)
    with pytest.raises(RuntimeError, match="positive"):
        validate_jwt_configuration("production", "x" * 32, "HS256", 0)


def test_expired_and_future_issued_tokens_are_rejected():
    token = issue_tenant_token(identity_document("user"), context())
    expired = resign(token, exp=datetime.now(timezone.utc) - timedelta(seconds=1))
    future = resign(token, iat=datetime.now(timezone.utc) + timedelta(hours=1))

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired, "tenant")
    with pytest.raises(jwt.InvalidTokenError):
        decode_access_token(future, "tenant")


def test_shop_token_cannot_authenticate_tenant_endpoint(monkeypatch):
    database = AsyncDatabase("auth_shop_confusion")
    shop_identity = identity_document("shop", role="shopuser")
    database.raw.users.insert_one(shop_identity)
    configure_database(monkeypatch, database)

    with pytest.raises(HTTPException) as rejected:
        run(deps.authenticated_identity(issue_shop_token(shop_identity)))

    assert rejected.value.status_code == 401


def test_legacy_identity_version_zero_rotates_atomically_and_invalidates_old_token(monkeypatch):
    database = AsyncDatabase("auth_legacy_rotation")
    identity = identity_document("user", auth_version=None)
    database.raw.users.insert_one(identity)
    configure_database(monkeypatch, database)
    token = issue_tenant_token(identity, context())

    assert run(rotate_credentials(
        database,
        identity,
        hashed_password=hash_pw("new-password"),
        must_change_password=False,
    )) is True
    current = database.raw.users.find_one({"id": "user"})
    assert current["authVersion"] == 1
    assert verify_pw("new-password", current["hashed_password"])

    with pytest.raises(HTTPException) as rejected:
        run(deps.authenticated_identity(token))
    assert rejected.value.status_code == 401


def test_parallel_credential_rotations_have_exactly_one_winner():
    database = AsyncDatabase("auth_parallel_rotation")
    identity = identity_document("user")
    database.raw.users.insert_one(identity)

    async def rotate_both():
        return await asyncio.gather(
            rotate_credentials(
                database,
                identity,
                hashed_password=hash_pw("new-password-a"),
                must_change_password=False,
            ),
            rotate_credentials(
                database,
                identity,
                hashed_password=hash_pw("new-password-b"),
                must_change_password=False,
            ),
        )

    results = run(rotate_both())
    assert sorted(results) == [False, True]
    assert database.raw.users.find_one({"id": "user"})["authVersion"] == 1


def test_identity_and_membership_state_are_revalidated_for_every_request(monkeypatch):
    database = AsyncDatabase("auth_state_revalidation")
    identity, tenant_context = seed_tenant_user(database, role="sales")
    configure_database(monkeypatch, database)
    token = issue_tenant_token(identity, tenant_context)

    database.raw.tenant_memberships.update_one(
        {"id": "mbr-user"}, {"$set": {"role": "admin"}}
    )
    principal = run(deps.authenticated_tenant_principal(
        run(deps.authenticated_identity(token))
    ))
    assert principal["role"] == "admin"

    database.raw.tenant_memberships.update_one(
        {"id": "mbr-user"}, {"$set": {"status": "inactive"}}
    )
    with pytest.raises(HTTPException) as inactive_membership:
        run(deps.authenticated_tenant_principal(run(deps.authenticated_identity(token))))
    assert inactive_membership.value.status_code == 403

    database.raw.tenant_memberships.delete_one({"id": "mbr-user"})
    with pytest.raises(HTTPException) as removed_membership:
        run(deps.authenticated_tenant_principal(run(deps.authenticated_identity(token))))
    assert removed_membership.value.status_code == 403

    database.raw.users.update_one({"id": "user"}, {"$set": {"active": False}})
    with pytest.raises(HTTPException) as inactive_identity:
        run(deps.authenticated_identity(token))
    assert inactive_identity.value.status_code == 401


@pytest.mark.parametrize("unsafe_active", [False, None, 0, "true"])
def test_explicit_malformed_or_inactive_identity_state_fails_closed(
    monkeypatch,
    unsafe_active,
):
    database = AsyncDatabase(f"auth_bad_active_{unsafe_active!s}")
    identity, tenant_context = seed_tenant_user(database)
    database.raw.users.update_one(
        {"id": "user"}, {"$set": {"active": unsafe_active}}
    )
    configure_database(monkeypatch, database)

    with pytest.raises(HTTPException) as rejected:
        run(deps.authenticated_identity(issue_tenant_token(identity, tenant_context)))
    assert rejected.value.status_code == 401


def test_malformed_password_change_state_is_restricted():
    principal = {"id": "user", "must_change_password": 0}
    with pytest.raises(HTTPException) as blocked:
        run(deps.current_user(principal))
    assert blocked.value.status_code == 403


def test_token_cannot_switch_tenant_or_membership(monkeypatch):
    database = AsyncDatabase("auth_tenant_switch")
    identity, tenant_context = seed_tenant_user(database)
    configure_database(monkeypatch, database)
    token = issue_tenant_token(identity, tenant_context)

    for forged in (
        resign(token, tenant_id=TENANT_B),
        resign(token, membership_id="mbr-foreign"),
    ):
        with pytest.raises(HTTPException) as rejected:
            run(deps.authenticated_tenant_principal(
                run(deps.authenticated_identity(forged))
            ))
        assert rejected.value.status_code == 403


def test_must_change_password_allows_completion_only_and_invalidates_parallel_sessions(monkeypatch):
    database = AsyncDatabase("auth_forced_change")
    identity, tenant_context = seed_tenant_user(database, must_change=True)
    configure_database(monkeypatch, database)
    token_a = issue_tenant_token(identity, tenant_context)
    token_b = issue_tenant_token(identity, tenant_context)
    pre_change = run(deps.authenticated_tenant_principal(
        run(deps.authenticated_identity(token_a))
    ))

    assert pre_change["must_change_password"] is True
    with pytest.raises(HTTPException) as blocked:
        run(deps.current_user(pre_change))
    assert (blocked.value.status_code, blocked.value.detail) == (
        403,
        "Passwortänderung erforderlich",
    )
    with pytest.raises(HTTPException) as shop_blocked:
        run(deps.shop_actor_identity(token_a))
    assert shop_blocked.value.status_code == 403

    result = run(auth.change_password(
        ChangePwIn(currentPassword="correct-password", newPassword="new-password"),
        pre_change,
        FakeRequest(),
    ))
    assert result["ok"] is True
    current = database.raw.users.find_one({"id": "user"})
    assert current["must_change_password"] is False
    assert current["authVersion"] == 1
    for old_token in (token_a, token_b):
        with pytest.raises(HTTPException) as old_session:
            run(deps.authenticated_identity(old_token))
        assert old_session.value.status_code == 401


def test_login_with_temporary_password_returns_restricted_session(monkeypatch):
    database = AsyncDatabase("auth_temporary_login")
    seed_tenant_user(database, must_change=True)
    configure_database(monkeypatch, database)

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(auth, "global_audit", no_audit)
    response = run(auth.login(
        OAuth2PasswordRequestForm(
            username="user@example.test",
            password="correct-password",
        ),
        FakeRequest(),
    ))

    assert response["user"].must_change_password is True
    identity = run(deps.authenticated_identity(response["access_token"]))
    principal = run(deps.authenticated_tenant_principal(identity))
    with pytest.raises(HTTPException) as blocked:
        run(deps.current_user(principal))
    assert blocked.value.status_code == 403


def test_password_recovery_invalidates_old_token(monkeypatch):
    database = AsyncDatabase("auth_password_recovery")
    identity, tenant_context = seed_tenant_user(database)
    configure_database(monkeypatch, database)
    old_token = issue_tenant_token(identity, tenant_context)
    code = "ABCDEFGH"
    database.raw.password_resets.insert_one({
        "userId": "user",
        "codeHash": hashlib.sha256(code.encode()).hexdigest(),
        "expiresAt": datetime.now(timezone.utc) + timedelta(minutes=30),
        "used": False,
    })

    result = run(auth.reset_password(
        ResetPwIn(
            email="user@example.test",
            code=code,
            newPassword="recovered-password",
        ),
        FakeRequest(),
    ))

    assert result["ok"] is True
    current = database.raw.users.find_one({"id": "user"})
    assert current["authVersion"] == 1
    assert verify_pw("recovered-password", current["hashed_password"])
    with pytest.raises(HTTPException) as old_session:
        run(deps.authenticated_identity(old_token))
    assert old_session.value.status_code == 401


def test_admin_password_reset_invalidates_tokens_and_requires_password_change(monkeypatch):
    database = AsyncDatabase("auth_admin_reset")
    identity, tenant_context = seed_tenant_user(database)
    configure_database(monkeypatch, database)
    token = issue_tenant_token(identity, tenant_context)
    access = TenantBusinessAccess(database, context("admin", membership_id="mbr-admin"))
    admin = {"id": "admin", "role": "admin", "_tenant_context": access.context}

    async def no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(users, "global_audit", no_audit)
    monkeypatch.setattr(users, "tenant_audit", no_audit)
    result = run(users.admin_reset_password("user", admin, access))
    current = database.raw.users.find_one({"id": "user"})

    assert verify_pw(result["initialPassword"], current["hashed_password"])
    assert current["must_change_password"] is True
    assert current["authVersion"] == 1
    with pytest.raises(HTTPException) as old_session:
        run(deps.authenticated_identity(token))
    assert old_session.value.status_code == 401


def test_optional_shop_auth_distinguishes_guest_from_invalid_token(monkeypatch):
    database = AsyncDatabase("auth_optional_shop")
    shop_identity = identity_document("shop", role="shopuser")
    database.raw.users.insert_one(shop_identity)
    configure_database(monkeypatch, database)

    assert run(deps.optional_shop_actor_id(None)) is None
    assert run(deps.optional_shop_actor_id(
        f"Bearer {issue_shop_token(shop_identity)}"
    )) == "shop"
    for authorization in ("Bearer invalid", "Basic abc", "Bearer "):
        with pytest.raises(HTTPException) as rejected:
            run(deps.optional_shop_actor_id(authorization))
        assert rejected.value.status_code == 401


def test_b2b_tenant_token_can_use_shop_without_exposing_tenant_authority(monkeypatch):
    database = AsyncDatabase("auth_b2b_shop")
    identity, tenant_context = seed_tenant_user(database, role="customer")
    configure_database(monkeypatch, database)
    token = issue_tenant_token(identity, tenant_context)

    shop_actor = run(deps.shop_actor_identity(token))

    assert shop_actor["id"] == "user"
    assert shop_actor["role"] == "customer"
    assert shop_actor["_tenant_context"].tenant_id == TENANT_A


def test_login_rate_limit_is_shared_between_internal_and_shop_endpoints(monkeypatch):
    database = AsyncDatabase("auth_shared_login_rate")
    configure_database(monkeypatch, database)
    form = OAuth2PasswordRequestForm(username=" Victim@Example.Test ", password="wrong")

    for index in range(auth_security.LOGIN_ACCOUNT_LIMIT):
        with pytest.raises(HTTPException) as failed:
            run(auth.login(
                form,
                FakeRequest(),
                requested_tenant_id=TENANT_A if index % 2 else TENANT_B,
            ))
        assert failed.value.status_code == 401

    with pytest.raises(HTTPException) as limited:
        run(shop.shop_login(
            ShopLoginIn(email="victim@example.test", password="wrong"),
            FakeRequest(),
        ))
    assert limited.value.status_code == 429
    assert "Retry-After" in limited.value.headers
    stored = database.raw.auth_rate_limits.find_one({"keyKind": "account"})
    assert stored is not None
    assert "victim@example.test" not in str(stored)


def test_empty_login_identifier_is_rejected_without_bypassing_or_crashing(monkeypatch):
    database = AsyncDatabase("auth_empty_login")
    configure_database(monkeypatch, database)

    with pytest.raises(HTTPException) as internal:
        run(auth.login(
            OAuth2PasswordRequestForm(username="   ", password="wrong"),
            FakeRequest(),
        ))
    with pytest.raises(HTTPException) as shop_login:
        run(shop.shop_login(
            ShopLoginIn(email="", password="wrong"),
            FakeRequest(),
        ))

    assert internal.value.status_code == 401
    assert shop_login.value.status_code == 401
    assert database.raw.auth_rate_limits.count_documents({"keyKind": "account"}) == 1


def test_rate_limiter_is_shared_atomic_and_fail_closed_at_threshold():
    database = AsyncDatabase("auth_atomic_rate")
    now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)
    key = RateLimitKey("account", "User@Example.Test", 2, 60)
    limiter_a = MongoAuthRateLimiter(database, now=lambda: now)
    limiter_b = MongoAuthRateLimiter(database, now=lambda: now)

    run(limiter_a.record("credential_login", (key,)))
    run(limiter_b.record("credential_login", (key,)))
    with pytest.raises(AuthRateLimitExceeded):
        run(limiter_a.ensure_allowed("credential_login", (key,)))


def test_shop_order_without_owner_or_access_token_is_never_public():
    with pytest.raises(HTTPException) as legacy_order:
        shop._authorize_shop_order({"id": "legacy"}, None, None)
    with pytest.raises(HTTPException) as foreign_owner:
        shop._authorize_shop_order(
            {"id": "owned", "userId": "owner", "token": "secret"},
            None,
            "other",
        )
    assert legacy_order.value.status_code == 403
    assert foreign_owner.value.status_code == 403
