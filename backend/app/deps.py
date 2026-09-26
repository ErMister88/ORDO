"""Authentication, membership authorization, and tenant visibility dependencies."""
from __future__ import annotations

from typing import Annotated, List

import jwt
from fastapi import Depends, Header, HTTPException, status

from .auth_security import (
    AuthStateError,
    decode_access_token,
    decode_actor_token,
    password_change_required,
    validate_identity_token,
)
from .core import Role, db, oauth2_scheme
from .tenant_access import TenantBusinessAccess
from .observability import bind_tenant_context
from .tenancy import (
    MembershipTenantResolver,
    MongoMembershipDirectory,
    MongoTenantDirectory,
    SingleTenantResolver,
    TenantContext,
    TenantResolutionError,
    TenantResolutionSubject,
    TenancyConfigurationError,
    TenancySettings,
)


def _authentication_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige oder abgelaufene Anmeldung",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def authenticated_identity(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict:
    """Validate a tenant token and return its current global identity."""

    try:
        payload = decode_access_token(token, "tenant")
        user = await db.users.find_one({"id": payload["sub"]})
        if not user:
            raise AuthStateError("Identity does not exist")
        validate_identity_token(user, payload, "tenant")
    except (jwt.PyJWTError, AuthStateError, KeyError, TypeError, ValueError) as exc:
        raise _authentication_error() from exc
    identity = dict(user)
    identity["_token_payload"] = payload
    return identity


async def resolve_membership_context(
    actor_user_id: str,
    *,
    requested_tenant_id: str | None = None,
    requested_membership_id: str | None = None,
    database=None,
) -> TenantContext:
    database = database or db
    resolver = MembershipTenantResolver(
        MongoTenantDirectory(database),
        MongoMembershipDirectory(database),
    )
    return await resolver.resolve(
        TenantResolutionSubject(
            actor_user_id=actor_user_id,
            requested_tenant_id=requested_tenant_id,
            requested_membership_id=requested_membership_id,
        )
    )


def membership_principal(identity: dict, context: TenantContext) -> dict:
    bind_tenant_context(context)
    principal = dict(identity)
    principal.pop("_token_payload", None)
    principal["role"] = context.role
    principal["companyId"] = context.company_id
    principal["salesRepId"] = None
    principal["must_change_password"] = password_change_required(identity)
    principal["_tenant_context"] = context
    return principal


async def authenticated_tenant_principal(
    identity: Annotated[dict, Depends(authenticated_identity)],
) -> dict:
    """Resolve current membership without enforcing a pending password change."""

    payload = identity.get("_token_payload") or {}
    requested_tenant_id = payload.get("tenant_id")
    requested_membership_id = payload.get("membership_id")
    if requested_tenant_id is not None and not isinstance(requested_tenant_id, str):
        raise _authentication_error()
    if requested_membership_id is not None and not isinstance(requested_membership_id, str):
        raise _authentication_error()
    try:
        context = await resolve_membership_context(
            identity["id"],
            requested_tenant_id=requested_tenant_id,
            requested_membership_id=requested_membership_id,
        )
    except (TenantResolutionError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine aktive Tenant-Mitgliedschaft",
        ) from exc
    return membership_principal(identity, context)


async def current_user(
    user: Annotated[dict, Depends(authenticated_tenant_principal)],
) -> dict:
    """Return a fully usable tenant principal with current password state."""

    if password_change_required(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Passwortänderung erforderlich",
        )
    return user


async def _shop_actor_from_token(token: str) -> dict:
    try:
        kind, payload = decode_actor_token(token)
        identity = await db.users.find_one({"id": payload["sub"]})
        if not identity:
            raise AuthStateError("Identity does not exist")
        validate_identity_token(identity, payload, kind)
    except (jwt.PyJWTError, AuthStateError, KeyError, TypeError, ValueError) as exc:
        raise _authentication_error() from exc

    result = dict(identity)
    result["_token_payload"] = payload
    if kind == "tenant":
        result = await authenticated_tenant_principal(result)
    if password_change_required(result):
        raise HTTPException(status_code=403, detail="Passwortänderung erforderlich")
    return result


async def shop_actor_identity(
    token: Annotated[str, Depends(oauth2_scheme)],
) -> dict:
    """Accept a current shop token or a fully valid tenant token for B2C use."""

    return await _shop_actor_from_token(token)


async def optional_shop_actor_id(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """Treat only an absent bearer as guest; malformed/present tokens fail."""

    if authorization is None:
        return None
    scheme, separator, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not separator or not token.strip():
        raise _authentication_error()
    return (await _shop_actor_from_token(token.strip()))["id"]


def require_roles(*allowed: Role):
    async def dep(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user.get("role") not in allowed:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user

    return dep


async def _resolve_public_tenant_context() -> TenantContext:
    try:
        resolver = SingleTenantResolver(
            TenancySettings.from_environment(),
            MongoTenantDirectory(db),
        )
        return await resolver.resolve(TenantResolutionSubject())
    except (TenantResolutionError, TenancyConfigurationError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant-Kontext nicht verfügbar",
        ) from exc


async def _resolve_tenant_context(actor_user_id: str | None = None) -> TenantContext:
    """Compatibility seam for the explicitly configured public tenant only."""

    try:
        resolver = SingleTenantResolver(
            TenancySettings.from_environment(),
            MongoTenantDirectory(db),
        )
        return await resolver.resolve(
            TenantResolutionSubject(actor_user_id=actor_user_id)
        )
    except (TenantResolutionError, TenancyConfigurationError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant-Kontext nicht verfügbar",
        ) from exc


async def public_tenant_context() -> TenantContext:
    context = await _resolve_public_tenant_context()
    bind_tenant_context(context)
    return context


async def current_tenant_context(
    user: Annotated[dict, Depends(current_user)],
) -> TenantContext:
    context = user.get("_tenant_context")
    if not isinstance(context, TenantContext):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine aktive Tenant-Mitgliedschaft",
        )
    bind_tenant_context(context)
    return context


async def public_tenant_business_access(
    context: Annotated[TenantContext, Depends(public_tenant_context)],
) -> TenantBusinessAccess:
    return TenantBusinessAccess(db, context)


async def tenant_business_access(
    context: Annotated[TenantContext, Depends(current_tenant_context)],
) -> TenantBusinessAccess:
    return TenantBusinessAccess(db, context)


async def visible_company_ids(
    user: dict,
    access: TenantBusinessAccess | None = None,
) -> List[str]:
    context = user.get("_tenant_context")
    if access is None:
        if not isinstance(context, TenantContext):
            raise HTTPException(status_code=403, detail="Keine aktive Tenant-Mitgliedschaft")
        access = TenantBusinessAccess(db, context)
    if (
        not isinstance(context, TenantContext)
        or context.tenant_id != access.context.tenant_id
        or context.actor_user_id != user.get("id")
        or context.role != user.get("role")
        or context.company_id != user.get("companyId")
    ):
        raise HTTPException(status_code=403, detail="Keine aktive Tenant-Mitgliedschaft")

    if context.role == "admin":
        companies = await access.companies.find({}).to_list(1000)
        return [company["id"] for company in companies]
    if context.role == "sales":
        companies = await access.companies.find(
            {"assignedSalesRepId": context.actor_user_id, "active": {"$ne": False}}
        ).to_list(1000)
        return [company["id"] for company in companies]
    if context.role != "customer" or context.company_id is None:
        return []
    company = await access.companies.find_one({"id": context.company_id})
    return [context.company_id] if company else []


async def active_staff_membership(
    access: TenantBusinessAccess,
    user_id: str,
) -> dict | None:
    """Resolve an assignment target only inside the current tenant."""

    if not isinstance(user_id, str) or not user_id:
        return None
    return await access.tenant_memberships.find_one({
        "userId": user_id,
        "status": "active",
        "role": {"$in": ["admin", "sales"]},
    })
