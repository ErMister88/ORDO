"""Ordered migration registry."""

from __future__ import annotations

from .models import Migration
from .versions.v0001_baseline import MIGRATION as V0001_BASELINE


_MIGRATIONS: tuple[Migration, ...] = (V0001_BASELINE,)


def get_migrations() -> tuple[Migration, ...]:
    return _MIGRATIONS
