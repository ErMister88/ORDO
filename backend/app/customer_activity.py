"""Small customer timeline service shared by commercial workflows."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .tenant_access import TenantBusinessAccess


async def record_customer_activity(
    access: TenantBusinessAccess,
    *,
    company_id: str,
    actor: dict,
    activity_type: str,
    title: str,
    note: str = "",
    internal: bool = True,
    occurred_at: str | None = None,
    reference: dict | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": "act-" + secrets.token_hex(8),
        "companyId": company_id,
        "type": activity_type,
        "title": title.strip(),
        "note": note.strip(),
        "internal": bool(internal),
        "occurredAt": occurred_at or now,
        "createdAt": now,
        "createdBy": actor.get("id"),
        "createdByName": actor.get("name", ""),
        "reference": reference or None,
    }
    await access.customer_activities.insert_one(row)
    return row
