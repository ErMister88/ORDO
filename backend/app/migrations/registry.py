"""Ordered migration registry."""

from __future__ import annotations

from dataclasses import replace

from .models import Migration
from .versions.v0001_baseline import MIGRATION as V0001_BASELINE
from .versions.v0002_tenant_schema_expansion import MIGRATION as V0002_TENANT_SCHEMA_EXPANSION
from .versions.v0003_tenant_memberships import MIGRATION as V0003_TENANT_MEMBERSHIPS
from .versions.v0004_money_expansion import MIGRATION as V0004_MONEY_EXPANSION
from .versions.v0005_pricing_expansion import MIGRATION as V0005_PRICING_EXPANSION
from .versions.v0006_legacy_tenant_bridge import MIGRATION as V0006_LEGACY_TENANT_BRIDGE


_MIGRATIONS: tuple[Migration, ...] = (
    V0001_BASELINE,
    replace(V0002_TENANT_SCHEMA_EXPANSION, depends_on=(1,)),
    V0006_LEGACY_TENANT_BRIDGE,
    replace(V0003_TENANT_MEMBERSHIPS, depends_on=(6,)),
    replace(V0004_MONEY_EXPANSION, depends_on=(3,)),
    replace(V0005_PRICING_EXPANSION, depends_on=(4,)),
)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
