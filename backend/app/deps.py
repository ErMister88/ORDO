"""Authentication, membership authorization, and tenant visibility dependencies."""
from __future__ import annotations

from typing import Annotated, List

import jwt
from fastapi import Depends, HTTPException, status

from .core import JWT_ALGORITHM, JWT_SECRET, Role, db, oauth2_scheme
from .tenant_access import TenantBusinessAccess
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
    """Return only the global identity; no tenant authorization is implied."""

    err = _authentication_error()
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not isinstance(uid, str) or not uid:
            raise err
    except HTTPException:
        raise
    except Exception:
        raise err
    user = await db.users.find_one({"id": uid})
    if not user:
        raise err
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
    principal = dict(identity)
    principal.pop("_token_payload", None)
    principal["role"] = context.role
    principal["companyId"] = context.company_id
    principal["salesRepId"] = None
    principal["_tenant_context"] = context
    return principal


async def current_user(
    identity: Annotated[dict, Depends(authenticated_identity)],
) -> dict:
    """Return a tenant-authorized principal whose role comes from membership."""

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
    return await _resolve_public_tenant_context()


async def current_tenant_context(
    user: Annotated[dict, Depends(current_user)],
) -> TenantContext:
    context = user.get("_tenant_context")
    if not isinstance(context, TenantContext):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Keine aktive Tenant-Mitgliedschaft",
        )
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
        companies = await access.companies.find({"active": True}).to_list(1000)
        return [company["id"] for company in companies]
    if context.role == "sales":
        companies = await access.companies.find(
            {"assignedSalesRepId": context.actor_user_id, "active": True}
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
