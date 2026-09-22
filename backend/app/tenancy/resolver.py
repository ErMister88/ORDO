"""Fail-closed tenant resolution contracts and the S&S transition resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from .config import TenancyMode, TenancySettings
from .domain import (
    Tenant,
    TenantContext,
    TenantMembership,
    TenantMembershipStatus,
    TenantResolutionSource,
    TenantStatus,
)


class TenantResolutionError(RuntimeError):
    """Raised when a trustworthy, unique tenant context cannot be resolved."""


@dataclass(frozen=True, slots=True)
class TenantResolutionSubject:
    """A server-authenticated actor, if the operation has one."""

    actor_user_id: str | None = None
    requested_tenant_id: str | None = None
    requested_membership_id: str | None = None

    def __post_init__(self) -> None:
        for field_name in (
            "actor_user_id",
            "requested_tenant_id",
            "requested_membership_id",
        ):
            value = getattr(self, field_name)
            if value is not None and (not value or value != value.strip()):
                raise ValueError(f"{field_name} must be null or a normalized string")


class TenantDirectory(Protocol):
    """Read-only tenant source; MongoDB support is added with the schema step."""

    async def list_tenants(self) -> Sequence[Tenant]: ...


class MembershipDirectory(Protocol):
    async def list_memberships_for_user(
        self,
        user_id: str,
    ) -> Sequence[TenantMembership]: ...


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


class MembershipTenantResolver:
    """Resolve a tenant only through an active, server-read membership."""

    def __init__(
        self,
        tenant_directory: TenantDirectory,
        membership_directory: MembershipDirectory,
    ) -> None:
        self._tenant_directory = tenant_directory
        self._membership_directory = membership_directory

    async def resolve(
        self,
        subject: TenantResolutionSubject | None = None,
    ) -> TenantContext:
        if subject is None or subject.actor_user_id is None:
            raise TenantResolutionError("Membership resolution requires an authenticated actor")

        memberships = tuple(
            await self._membership_directory.list_memberships_for_user(
                subject.actor_user_id
            )
        )
        seen_ids: set[str] = set()
        seen_tenants: set[str] = set()
        for membership in memberships:
            if membership.user_id != subject.actor_user_id:
                raise TenantResolutionError("Membership directory returned another actor")
            if membership.membership_id in seen_ids:
                raise TenantResolutionError("Membership id is not unique")
            if membership.tenant_id in seen_tenants:
                raise TenantResolutionError("User has duplicate memberships for one tenant")
            seen_ids.add(membership.membership_id)
            seen_tenants.add(membership.tenant_id)

        candidates = list(memberships)
        if subject.requested_tenant_id is not None:
            candidates = [
                membership
                for membership in candidates
                if membership.tenant_id == subject.requested_tenant_id
            ]
        if subject.requested_membership_id is not None:
            candidates = [
                membership
                for membership in candidates
                if membership.membership_id == subject.requested_membership_id
            ]
        if (
            subject.requested_tenant_id is None
            and subject.requested_membership_id is None
        ):
            candidates = [
                membership
                for membership in candidates
                if membership.status is TenantMembershipStatus.ACTIVE
            ]

        if len(candidates) != 1:
            raise TenantResolutionError(
                "Tenant membership is missing, ambiguous, or does not match the request"
            )
        membership = candidates[0]
        if membership.status is not TenantMembershipStatus.ACTIVE:
            raise TenantResolutionError("Tenant membership is not active")

        tenants = tuple(await self._tenant_directory.list_tenants())
        matching_tenants = [
            tenant for tenant in tenants if tenant.tenant_id == membership.tenant_id
        ]
        if len(matching_tenants) != 1:
            raise TenantResolutionError("Membership tenant does not exist uniquely")
        tenant = matching_tenants[0]
        if tenant.status is not TenantStatus.ACTIVE:
            raise TenantResolutionError("Membership tenant is not active")

        return TenantContext(
            tenant_id=membership.tenant_id,
            actor_user_id=membership.user_id,
            membership_id=membership.membership_id,
            role=membership.role.value,
            company_id=membership.company_id,
            resolution_source=TenantResolutionSource.MEMBERSHIP,
        )
