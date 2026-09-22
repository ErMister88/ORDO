"""Explicit persistence boundaries for tenant and global audit events."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Mapping

from .core import db

if TYPE_CHECKING:
    from .tenant_access import TenantBusinessAccess


def _audit_document(
    user: Mapping[str, Any] | None,
    action: str,
    entity: str,
    meta: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "at": datetime.now(timezone.utc).isoformat(),
        "userId": (user or {}).get("id"),
        "userEmail": (user or {}).get("email"),
        "role": (user or {}).get("role"),
        "action": action,
        "entity": entity,
        "meta": dict(meta or {}),
    }


async def tenant_audit(
    access: TenantBusinessAccess,
    user: Mapping[str, Any] | None,
    action: str,
    entity: str = "",
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Write a best-effort event owned by the server-resolved tenant."""

    collection = access.audit_log
    try:
        await collection.insert_one(_audit_document(user, action, entity, meta))
    except Exception:
        pass


async def global_audit(
    user: Mapping[str, Any] | None,
    action: str,
    entity: str = "",
    meta: Mapping[str, Any] | None = None,
) -> None:
    """Write a best-effort global identity or system event without tenant ownership."""

    try:
        await db.audit_log.insert_one(_audit_document(user, action, entity, meta))
    except Exception:
        pass
