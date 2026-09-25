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
from .versions.v0007_customer_commerce_foundation import MIGRATION as V0007_CUSTOMER_COMMERCE_FOUNDATION
from .versions.v0008_product_review_round2 import MIGRATION as V0008_PRODUCT_REVIEW_ROUND2
from .versions.v0009_transaction_safety_config import MIGRATION as V0009_TRANSACTION_SAFETY_CONFIG


_MIGRATIONS: tuple[Migration, ...] = (
    V0001_BASELINE,
    replace(V0002_TENANT_SCHEMA_EXPANSION, depends_on=(1,)),
    V0006_LEGACY_TENANT_BRIDGE,
    replace(V0003_TENANT_MEMBERSHIPS, depends_on=(6,)),
    replace(V0004_MONEY_EXPANSION, depends_on=(3,)),
    replace(V0005_PRICING_EXPANSION, depends_on=(4,)),
    replace(V0007_CUSTOMER_COMMERCE_FOUNDATION, depends_on=(5,)),
    replace(V0008_PRODUCT_REVIEW_ROUND2, depends_on=(7,)),
    replace(V0009_TRANSACTION_SAFETY_CONFIG, depends_on=(8,)),
)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
