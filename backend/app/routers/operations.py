"""Administrative visibility for unresolved commercial workflows."""

from typing import Annotated, Literal

from fastapi import Depends

from ..core import api_router, strip_id
from ..deps import require_roles, tenant_business_access
from ..tenant_access import TenantBusinessAccess


@api_router.get("/operations")
async def list_commercial_operations(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    status: Literal["processing", "failed_retryable", "failed_terminal", "completed"] | None = None,
):
    query = {"status": status} if status else {"status": {"$ne": "completed"}}
    rows = await access.idempotency_records.find(query).sort("updatedAt", -1).to_list(500)
    allowed = {
        "id", "operation", "actorId", "status", "workflowState", "attempts",
        "resourceRefs", "events", "createdAt", "updatedAt", "completedAt",
        "lastErrorCode",
    }
    return [
        {key: value for key, value in strip_id(row).items() if key in allowed}
        for row in rows
    ]
