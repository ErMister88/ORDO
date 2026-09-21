"""Tenant domain values that do not depend on MongoDB or HTTP."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# This identifier is persisted as an immutable business-system identifier. It
# must never be derived from S&S's name, slug, domain, or legal form.
SS_TENANT_ID = "tnt_ss_0001"


class TenantStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class TenantResolutionSource(str, Enum):
    SINGLE_TENANT_CONFIGURATION = "single_tenant_configuration"
    MEMBERSHIP = "membership"


@dataclass(frozen=True, slots=True)
class Tenant:
    """A business operating ORDO, distinct from its B2B customer companies."""

    tenant_id: str
    slug: str
    display_name: str
    status: TenantStatus
    legal_name: str | None = None
    default_currency: str = "EUR"
    default_locale: str = "de-DE"
    timezone: str = "Europe/Berlin"

    def __post_init__(self) -> None:
        if not isinstance(self.status, TenantStatus):
            raise ValueError("Tenant status must be a TenantStatus")
        for field_name in ("tenant_id", "slug", "display_name"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Tenant {field_name} must be a non-empty string")
            if value != value.strip():
                raise ValueError(f"Tenant {field_name} must not contain surrounding whitespace")
        if self.legal_name is not None and not self.legal_name.strip():
            raise ValueError("Tenant legal_name must be null or a non-empty string")


SS_TENANT = Tenant(
    tenant_id=SS_TENANT_ID,
    slug="ss-coffee-and-more",
    display_name="S&S coffee and more",
    legal_name=None,
    status=TenantStatus.ACTIVE,
    default_currency="EUR",
    default_locale="de-DE",
    timezone="Europe/Berlin",
)


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Server-resolved tenant scope carried through one request or job.

    ``membership_id`` and ``role`` intentionally remain optional. Work package
    4 can populate them from a validated membership without changing the
    context contract or adding tenant ownership to the global user identity.
    """

    tenant_id: str
    resolution_source: TenantResolutionSource
    actor_user_id: str | None = None
    membership_id: str | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        if not self.tenant_id or self.tenant_id != self.tenant_id.strip():
            raise ValueError("TenantContext tenant_id must be a non-empty normalized string")
        if not isinstance(self.resolution_source, TenantResolutionSource):
            raise ValueError("TenantContext resolution_source must be a TenantResolutionSource")
        for field_name in ("actor_user_id", "membership_id", "role"):
            value = getattr(self, field_name)
            if value is not None and (not value or value != value.strip()):
                raise ValueError(f"TenantContext {field_name} must be null or a normalized string")
