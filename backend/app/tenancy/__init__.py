"""Tenant domain primitives for ORDO's staged multi-tenant transition."""

from .config import TenancyConfigurationError, TenancyMode, TenancySettings
from .domain import SS_TENANT_ID, Tenant, TenantContext, TenantResolutionSource, TenantStatus
from .resolver import (
    SingleTenantResolver,
    TenantDirectory,
    TenantResolutionError,
    TenantResolutionSubject,
    TenantResolver,
)

__all__ = [
    "SS_TENANT_ID",
    "SingleTenantResolver",
    "Tenant",
    "TenantContext",
    "TenantDirectory",
    "TenantResolutionError",
    "TenantResolutionSource",
    "TenantResolutionSubject",
    "TenantResolver",
    "TenantStatus",
    "TenancyConfigurationError",
    "TenancyMode",
    "TenancySettings",
]
