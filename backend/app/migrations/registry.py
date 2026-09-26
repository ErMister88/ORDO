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
from .versions.v0010_payment_integrity import MIGRATION as V0010_PAYMENT_INTEGRITY
from .versions.v0011_operational_reliability import MIGRATION as V0011_OPERATIONAL_RELIABILITY
from .versions.v0012_storage_communication_worker import MIGRATION as V0012_STORAGE_COMMUNICATION_WORKER
from .versions.v0013_production_hardening import MIGRATION as V0013_PRODUCTION_HARDENING


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
    replace(V0010_PAYMENT_INTEGRITY, depends_on=(9,)),
    replace(V0011_OPERATIONAL_RELIABILITY, depends_on=(10,)),
    replace(V0012_STORAGE_COMMUNICATION_WORKER, depends_on=(11,)),
    replace(V0013_PRODUCTION_HARDENING, depends_on=(12,)),
)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
