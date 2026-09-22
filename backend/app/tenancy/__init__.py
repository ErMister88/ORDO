"""Tenant domain primitives for ORDO's staged multi-tenant transition."""

from .config import TenancyConfigurationError, TenancyMode, TenancySettings
from .domain import (
    SS_TENANT,
    SS_TENANT_ID,
    Tenant,
    TenantContext,
    TenantMembership,
    TenantMembershipStatus,
    TenantResolutionSource,
    TenantRole,
    TenantStatus,
)
from .persistence import TENANT_SCHEMA_VERSION, tenant_to_document
from .mongo import MongoMembershipDirectory, MongoTenantDirectory
from .resolver import (
    SingleTenantResolver,
    MembershipDirectory,
    MembershipTenantResolver,
    TenantDirectory,
    TenantResolutionError,
    TenantResolutionSubject,
    TenantResolver,
)

__all__ = [
    "SS_TENANT_ID",
    "SS_TENANT",
    "MongoTenantDirectory",
    "MongoMembershipDirectory",
    "MembershipDirectory",
    "MembershipTenantResolver",
    "SingleTenantResolver",
    "Tenant",
    "TenantContext",
    "TenantMembership",
    "TenantMembershipStatus",
    "TenantDirectory",
    "TenantResolutionError",
    "TenantResolutionSource",
    "TenantResolutionSubject",
    "TenantResolver",
    "TenantRole",
    "TenantStatus",
    "TENANT_SCHEMA_VERSION",
    "TenancyConfigurationError",
    "TenancyMode",
    "TenancySettings",
    "tenant_to_document",
]
