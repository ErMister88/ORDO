"""Audit log (admin read-only)."""
from typing import Annotated
from fastapi import Depends

from ..core import api_router, db, strip_id
from ..deps import require_roles


@api_router.get("/audit")
async def get_audit(user: Annotated[dict, Depends(require_roles("admin"))]):
    rows = await db.audit_log.find({}).sort("at", -1).to_list(200)
    return [strip_id(r) for r in rows]
