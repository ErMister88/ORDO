"""Companies / customers."""
import secrets
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..audit_service import tenant_audit
from ..customer_activity import record_customer_activity
from ..deps import (
    active_staff_membership,
    current_user,
    require_roles,
    tenant_business_access,
    visible_company_ids,
)
from ..models import CompanyAssignmentIn, CompanyCreateIn, CompanyStatusIn, CompanyUpdateIn
from ..tenant_access import TenantBusinessAccess


@api_router.get("/companies")
async def get_companies(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    companies = await access.companies.find({"id": {"$in": ids}}).to_list(1000)
    companies = [strip_id(c) for c in companies]
    now = datetime.now(timezone.utc)
    for c in companies:
        last = await access.orders.find({"companyId": c["id"]}).sort("createdAt", -1).to_list(1)
        overdue = False
        days_since = None
        if last:
            last_dt = datetime.fromisoformat(last[0]["createdAt"]).replace(tzinfo=timezone.utc)
            days_since = (now - last_dt).days
            overdue = days_since > c.get("orderCycleDays", 30)
        else:
            overdue = True
        c.setdefault("status", "Aktiv" if c.get("active", True) else "Inaktiv")
        c["overdue"] = overdue
        c["daysSinceLastOrder"] = days_since
    return companies


@api_router.post("/companies")
async def create_company(
    body: CompanyCreateIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Kundenname ist erforderlich")
    if user["role"] == "sales":
        assigned_sales_rep_id = user["id"]
    else:
        assigned_sales_rep_id = body.assignedSalesRepId
        if assigned_sales_rep_id:
            membership = await active_staff_membership(access, assigned_sales_rep_id)
            if not membership or membership.get("role") != "sales":
                raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    now = datetime.now(timezone.utc).isoformat()
    company = {
        "id": "c-" + secrets.token_hex(6),
        "name": name,
        "city": body.city.strip(),
        "email": body.email.strip().lower(),
        "phone": body.phone.strip(),
        "vatId": body.vatId.strip(),
        "status": body.status,
        "assignedSalesRepId": assigned_sales_rep_id,
        "orderCycleDays": body.orderCycleDays,
        "active": True,
        "monthlyKg": 0,
        "createdAt": now,
        "createdBy": user["id"],
    }
    await access.companies.insert_one(company)
    await record_customer_activity(
        access, company_id=company["id"], actor=user, activity_type="customer_created",
        title="Kunde angelegt", internal=True,
    )
    await tenant_audit(access, user, "company.create", company["id"], {"name": name})
    return strip_id(company)


@api_router.get("/companies/{company_id}")
async def get_company(
    company_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    c = await access.companies.find_one({"id": company_id})
    if not c:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    ids = await visible_company_ids(user, access)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return strip_id(c)


@api_router.put("/companies/{company_id}")
async def update_company(
    company_id: str,
    body: CompanyUpdateIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    c = await access.companies.find_one({"id": company_id})
    if not c:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if body.assignedSalesRepId:
        membership = await active_staff_membership(access, body.assignedSalesRepId)
        if not membership or membership.get("role") != "sales":
            raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    update = body.model_dump(exclude_none=True)
    await access.companies.update_one({"id": company_id}, {"$set": update})
    updated = await access.companies.find_one({"id": company_id})
    await tenant_audit(access, user, "company.update", company_id, {"name": body.name})
    return strip_id(updated)


@api_router.put("/companies/{company_id}/assignment")
async def assign_company_sales_rep(
    company_id: str,
    body: CompanyAssignmentIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if body.assignedSalesRepId:
        membership = await active_staff_membership(access, body.assignedSalesRepId)
        if not membership or membership.get("role") != "sales":
            raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    await access.companies.update_one(
        {"id": company_id}, {"$set": {"assignedSalesRepId": body.assignedSalesRepId}}
    )
    await record_customer_activity(
        access, company_id=company_id, actor=user, activity_type="assignment_changed",
        title="Vertriebszuordnung geändert", internal=True,
        reference={"assignedSalesRepId": body.assignedSalesRepId},
    )
    await tenant_audit(access, user, "company.assignment", company_id, {"assignedSalesRepId": body.assignedSalesRepId})
    return {"ok": True, "assignedSalesRepId": body.assignedSalesRepId}


@api_router.put("/companies/{company_id}/status")
async def update_company_status(
    company_id: str,
    body: CompanyStatusIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if company_id not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if not await access.companies.find_one({"id": company_id}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    await access.companies.update_one({"id": company_id}, {"$set": {"status": body.status}})
    await record_customer_activity(
        access, company_id=company_id, actor=user, activity_type="status_changed",
        title=f"Kundenstatus: {body.status}", internal=True,
    )
    await tenant_audit(access, user, "company.status", company_id, {"status": body.status})
    return {"ok": True, "status": body.status}


@api_router.get("/staff/sales")
async def list_sales_staff(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    memberships = await access.tenant_memberships.find({"role": "sales", "status": "active"}).to_list(1000)
    # Identities are global and intentionally resolved only after tenant-scoped membership selection.
    from ..core import db
    identities = {
        row["id"]: row
        for row in await db.users.find({"id": {"$in": [m["userId"] for m in memberships]}}).to_list(1000)
    }
    return [
        {"id": m["userId"], "name": identities.get(m["userId"], {}).get("name", "")}
        for m in memberships if m.get("userId") in identities
    ]


@api_router.get("/companies/{company_id}/prices")
async def get_company_prices(
    company_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await access.companies.find_one({"id": company_id}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    ids = await visible_company_ids(user, access)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    prices = await access.customer_prices.find({"companyId": company_id}).to_list(1000)
    result = []
    for price in prices:
        row = strip_id(price)
        if user.get("role") != "admin":
            row.pop("internalNote", None)
        result.append(row)
    return result
