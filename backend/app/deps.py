"""Auth + visibility dependencies."""
import jwt
from fastapi import Depends, HTTPException, status
from typing import Annotated, List

from .core import db, oauth2_scheme, JWT_SECRET, JWT_ALGORITHM, Role
from .tenant_access import TenantBusinessAccess
from .tenancy import (
    MongoTenantDirectory,
    SingleTenantResolver,
    TenantContext,
    TenantResolutionError,
    TenantResolutionSubject,
    TenancyConfigurationError,
    TenancySettings,
)


async def current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige oder abgelaufene Anmeldung",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            raise err
    except Exception:
        raise err
    user = await db.users.find_one({"id": uid})
    if not user:
        raise err
    return user


def require_roles(*allowed: Role):
    async def dep(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user

    return dep


async def _resolve_tenant_context(actor_user_id: str | None = None) -> TenantContext:
    try:
        resolver = SingleTenantResolver(
            TenancySettings.from_environment(),
            MongoTenantDirectory(db),
        )
        subject = TenantResolutionSubject(actor_user_id=actor_user_id)
        return await resolver.resolve(subject)
    except (TenantResolutionError, TenancyConfigurationError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tenant-Kontext nicht verfügbar",
        ) from exc


async def public_tenant_context() -> TenantContext:
    return await _resolve_tenant_context()


async def current_tenant_context(
    user: Annotated[dict, Depends(current_user)],
) -> TenantContext:
    return await _resolve_tenant_context(user["id"])


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
    if access is None:
        access = TenantBusinessAccess(
            db,
            await _resolve_tenant_context(user.get("id")),
        )
    if user["role"] == "admin":
        companies = await access.companies.find({"active": True}).to_list(1000)
        return [c["id"] for c in companies]
    if user["role"] == "sales":
        companies = await access.companies.find(
            {"assignedSalesRepId": user["id"], "active": True}
        ).to_list(1000)
        return [c["id"] for c in companies]
    company_id = user.get("companyId")
    if not isinstance(company_id, str) or not company_id:
        return []
    company = await access.companies.find_one({"id": company_id})
    return [company_id] if company else []
