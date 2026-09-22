"""Ordered migration registry."""

from __future__ import annotations

from .models import Migration
from .versions.v0001_baseline import MIGRATION as V0001_BASELINE
from .versions.v0002_tenant_schema_expansion import MIGRATION as V0002_TENANT_SCHEMA_EXPANSION
from .versions.v0003_tenant_memberships import MIGRATION as V0003_TENANT_MEMBERSHIPS


_MIGRATIONS: tuple[Migration, ...] = (
    V0001_BASELINE,
    V0002_TENANT_SCHEMA_EXPANSION,
    V0003_TENANT_MEMBERSHIPS,
)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
