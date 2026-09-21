"""Ordered migration registry."""

from __future__ import annotations

from .models import Migration
from .versions.v0001_baseline import MIGRATION as V0001_BASELINE
from .versions.v0002_tenant_schema_expansion import MIGRATION as V0002_TENANT_SCHEMA_EXPANSION


_MIGRATIONS: tuple[Migration, ...] = (
    V0001_BASELINE,
    V0002_TENANT_SCHEMA_EXPANSION,
)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
