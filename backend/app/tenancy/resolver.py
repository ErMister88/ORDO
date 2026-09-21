"""Fail-closed tenant resolution contracts and the S&S transition resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .config import TenancyMode, TenancySettings
from .domain import Tenant, TenantContext, TenantResolutionSource, TenantStatus


class TenantResolutionError(RuntimeError):
    """Raised when a trustworthy, unique tenant context cannot be resolved."""


@dataclass(frozen=True, slots=True)
class TenantResolutionSubject:
    """A server-authenticated actor, if the operation has one."""

    actor_user_id: str | None = None

    def __post_init__(self) -> None:
        if self.actor_user_id is not None and (
            not self.actor_user_id or self.actor_user_id != self.actor_user_id.strip()
        ):
            raise ValueError("actor_user_id must be null or a normalized string")


class TenantDirectory(Protocol):
    """Read-only tenant source; MongoDB support is added with the schema step."""

    async def list_tenants(self) -> Sequence[Tenant]: ...


class TenantResolver(Protocol):
    """Stable resolver boundary for single-tenant and future membership modes."""

    async def resolve(
        self,
        subject: TenantResolutionSubject | None = None,
    ) -> TenantContext: ...


class SingleTenantResolver:
    """Resolve only the explicitly configured S&S tenant.

    Tenant selection is deliberately absent from the public method signature.
    Request bodies, query parameters, and headers therefore cannot override the
    configured tenant through this resolver.
    """

    def __init__(self, settings: TenancySettings, directory: TenantDirectory) -> None:
        if settings.mode is not TenancyMode.SINGLE:
            raise TenantResolutionError("SingleTenantResolver requires single-tenant mode")
        self._settings = settings
        self._directory = directory

    async def resolve(
        self,
        subject: TenantResolutionSubject | None = None,
    ) -> TenantContext:
        tenants = tuple(await self._directory.list_tenants())
        matches = [tenant for tenant in tenants if tenant.tenant_id == self._settings.default_tenant_id]
        if len(matches) != 1:
            raise TenantResolutionError("Configured tenant does not exist uniquely")

        tenant = matches[0]
        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantResolutionError("Configured tenant is not active")

        active_tenants = [item for item in tenants if item.status is TenantStatus.ACTIVE]
        if len(active_tenants) != 1 or active_tenants[0].tenant_id != tenant.tenant_id:
            raise TenantResolutionError(
                "Single-tenant mode requires exactly one active configured tenant"
            )

        return TenantContext(
            tenant_id=tenant.tenant_id,
            actor_user_id=subject.actor_user_id if subject else None,
            membership_id=None,
            role=None,
            resolution_source=TenantResolutionSource.SINGLE_TENANT_CONFIGURATION,
        )
