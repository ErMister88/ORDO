"""Strict environment configuration for ORDO tenancy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from typing import Mapping

from .domain import SS_TENANT_ID


class TenancyConfigurationError(RuntimeError):
    """Raised when tenant isolation cannot be configured unambiguously."""


class TenancyMode(str, Enum):
    SINGLE = "single"


@dataclass(frozen=True, slots=True)
class TenancySettings:
    mode: TenancyMode
    default_tenant_id: str

    def __post_init__(self) -> None:
        if self.mode is not TenancyMode.SINGLE:
            raise TenancyConfigurationError("Only explicit single-tenant mode is supported")
        if self.default_tenant_id != SS_TENANT_ID:
            raise TenancyConfigurationError(
                f"DEFAULT_TENANT_ID must be the immutable S&S tenant id {SS_TENANT_ID!r}"
            )

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "TenancySettings":
        raw_mode = values.get("TENANCY_MODE")
        if raw_mode is None or not raw_mode.strip():
            raise TenancyConfigurationError("TENANCY_MODE is required")
        normalized_mode = raw_mode.strip().lower()
        try:
            mode = TenancyMode(normalized_mode)
        except ValueError as exc:
            raise TenancyConfigurationError(
                f"Unsupported TENANCY_MODE {normalized_mode!r}"
            ) from exc

        raw_tenant_id = values.get("DEFAULT_TENANT_ID")
        if raw_tenant_id is None or not raw_tenant_id.strip():
            raise TenancyConfigurationError("DEFAULT_TENANT_ID is required")
        return cls(mode=mode, default_tenant_id=raw_tenant_id.strip())

    @classmethod
    def from_environment(cls) -> "TenancySettings":
        """Read the process environment only when explicitly called."""

        return cls.from_mapping(os.environ)
