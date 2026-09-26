"""Small tenant-scoped in-app notification foundation."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Mapping

from .tenant_access import TenantBusinessAccess


async def create_notification(
    access: TenantBusinessAccess,
    *,
    recipient_user_id: str,
    notification_type: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    title_key: str,
    message_key: str,
    parameters: Mapping[str, str | int | float] | None = None,
) -> dict:
    now = datetime.now(timezone.utc)
    document = {
        "id": "ntf_" + secrets.token_hex(12),
        "recipientUserId": recipient_user_id,
        "type": notification_type,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "titleKey": title_key,
        "messageKey": message_key,
        "parameters": dict(parameters or {}),
        "createdAt": now,
        "readAt": None,
        "status": "active",
    }
    await access.notifications.insert_one(document)
    return document
