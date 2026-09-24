"""Tenant-scoped customer CRM activities and follow-up tasks."""
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException

from ..audit_service import tenant_audit
from ..core import api_router, strip_id
from ..customer_activity import record_customer_activity
from ..deps import active_staff_membership, require_roles, tenant_business_access, visible_company_ids
from ..models import CustomerActivityIn, CustomerTaskIn, CustomerTaskUpdateIn
from ..tenant_access import TenantBusinessAccess


async def _require_visible_company(user: dict, access: TenantBusinessAccess, company_id: str) -> dict:
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if company_id not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return company


@api_router.get("/companies/{company_id}/activities")
async def list_customer_activities(company_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    await _require_visible_company(user, access, company_id)
    rows = await access.customer_activities.find({"companyId": company_id}).sort("occurredAt", -1).to_list(500)
    return [strip_id(row) for row in rows]


@api_router.post("/companies/{company_id}/activities")
async def create_customer_activity(company_id: str, body: CustomerActivityIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    await _require_visible_company(user, access, company_id)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Titel ist erforderlich")
    occurred_at = body.occurredAt
    if occurred_at is not None and occurred_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="Zeitpunkt benötigt eine Zeitzone")
    row = await record_customer_activity(
        access, company_id=company_id, actor=user, activity_type=body.type,
        title=body.title, note=body.note, internal=body.internal,
        occurred_at=occurred_at.astimezone(timezone.utc).isoformat() if occurred_at else None,
    )
    await tenant_audit(access, user, "customer.activity.create", row["id"], {"companyId": company_id})
    return strip_id(row)


@api_router.get("/companies/{company_id}/tasks")
async def list_customer_tasks(company_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    await _require_visible_company(user, access, company_id)
    rows = await access.customer_tasks.find({"companyId": company_id}).sort("dueAt", 1).to_list(500)
    return [strip_id(row) for row in rows]


@api_router.post("/companies/{company_id}/tasks")
async def create_customer_task(company_id: str, body: CustomerTaskIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    await _require_visible_company(user, access, company_id)
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="Titel ist erforderlich")
    if body.dueAt.tzinfo is None:
        raise HTTPException(status_code=400, detail="Fälligkeit benötigt eine Zeitzone")
    assignee = body.assignedUserId or user["id"]
    if user.get("role") == "sales" and assignee != user["id"]:
        raise HTTPException(status_code=403, detail="Vertrieb darf Aufgaben nur sich selbst zuweisen")
    if not await active_staff_membership(access, assignee):
        raise HTTPException(status_code=400, detail="Zuständiger Benutzer ist nicht verfügbar")
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "id": "task-" + secrets.token_hex(8), "companyId": company_id,
        "title": body.title.strip(), "note": body.note.strip(),
        "assignedUserId": assignee, "dueAt": body.dueAt.astimezone(timezone.utc).isoformat(),
        "status": "open", "createdBy": user["id"], "createdAt": now, "updatedAt": now,
    }
    await access.customer_tasks.insert_one(row)
    await tenant_audit(access, user, "customer.task.create", row["id"], {"companyId": company_id})
    return strip_id(row)


@api_router.put("/customer-tasks/{task_id}")
async def update_customer_task(task_id: str, body: CustomerTaskUpdateIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    task = await access.customer_tasks.find_one({"id": task_id})
    if not task:
        raise HTTPException(status_code=404, detail="Aufgabe nicht gefunden")
    await _require_visible_company(user, access, task.get("companyId"))
    await access.customer_tasks.update_one({"id": task_id}, {"$set": {
        "status": body.status, "updatedAt": datetime.now(timezone.utc).isoformat(), "updatedBy": user["id"],
    }})
    await tenant_audit(access, user, "customer.task.update", task_id, {"status": body.status})
    return {"ok": True, "status": body.status}
