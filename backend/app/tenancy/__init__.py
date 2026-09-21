"""Tenant domain primitives for ORDO's staged multi-tenant transition."""

from .config import TenancyConfigurationError, TenancyMode, TenancySettings
from .domain import (
    SS_TENANT,
    SS_TENANT_ID,
    Tenant,
    TenantContext,
    TenantResolutionSource,
    TenantStatus,
)
from .persistence import TENANT_SCHEMA_VERSION, tenant_to_document
from .mongo import MongoTenantDirectory
from .resolver import (
    SingleTenantResolver,
    TenantDirectory,
    TenantResolutionError,
    TenantResolutionSubject,
    TenantResolver,
)

__all__ = [
    "SS_TENANT_ID",
    "SS_TENANT",
    "MongoTenantDirectory",
    "SingleTenantResolver",
    "Tenant",
    "TenantContext",
    "TenantDirectory",
    "TenantResolutionError",
    "TenantResolutionSource",
    "TenantResolutionSubject",
    "TenantResolver",
    "TenantStatus",
    "TENANT_SCHEMA_VERSION",
    "TenancyConfigurationError",
    "TenancyMode",
    "TenancySettings",
    "tenant_to_document",
]
