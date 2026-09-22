"""Companies / customers."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..audit_service import tenant_audit
from ..deps import (
    active_staff_membership,
    current_user,
    require_roles,
    tenant_business_access,
    visible_company_ids,
)
from ..models import CompanyUpdateIn
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
        c["overdue"] = overdue
        c["daysSinceLastOrder"] = days_since
    return companies


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
    if body.assignedSalesRepId and not await active_staff_membership(
        access,
        body.assignedSalesRepId,
    ):
        raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    await access.companies.update_one({"id": company_id}, {"$set": body.model_dump()})
    updated = await access.companies.find_one({"id": company_id})
    await tenant_audit(access, user, "company.update", company_id, {"name": body.name})
    return strip_id(updated)


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
    return [strip_id(p) for p in prices]
