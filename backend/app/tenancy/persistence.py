"""Canonical mapping between the tenant domain and its MongoDB document."""

from __future__ import annotations

from datetime import datetime, timedelta

from .domain import Tenant


TENANT_SCHEMA_VERSION = 1


def tenant_to_document(
    tenant: Tenant,
    *,
    created_at: datetime,
    updated_at: datetime,
) -> dict:
    """Serialize a tenant using the one persisted field-name contract."""

    if not isinstance(created_at, datetime) or created_at.tzinfo is None:
        raise ValueError("created_at must be a timezone-aware datetime")
    if not isinstance(updated_at, datetime) or updated_at.tzinfo is None:
        raise ValueError("updated_at must be a timezone-aware datetime")
    if created_at.utcoffset() != timedelta(0) or updated_at.utcoffset() != timedelta(0):
        raise ValueError("tenant timestamps must use UTC")
    if updated_at < created_at:
        raise ValueError("updated_at must not precede created_at")
    return {
        "id": tenant.tenant_id,
        "slug": tenant.slug,
        "displayName": tenant.display_name,
        "legalName": tenant.legal_name,
        "status": tenant.status.value,
        "defaultCurrency": tenant.default_currency,
        "defaultLocale": tenant.default_locale,
        "timezone": tenant.timezone,
        "schemaVersion": TENANT_SCHEMA_VERSION,
        "createdAt": created_at,
        "updatedAt": updated_at,
    }
