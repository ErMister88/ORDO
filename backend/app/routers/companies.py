"""Companies / customers."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, db, strip_id
from ..deps import current_user, require_roles, visible_company_ids
from ..models import CompanyUpdateIn


@api_router.get("/companies")
async def get_companies(user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
    companies = await db.companies.find({"id": {"$in": ids}}).to_list(1000)
    companies = [strip_id(c) for c in companies]
    now = datetime.now(timezone.utc)
    for c in companies:
        last = await db.orders.find({"companyId": c["id"]}).sort("createdAt", -1).to_list(1)
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
async def get_company(company_id: str, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    c = await db.companies.find_one({"id": company_id})
    if not c:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    return strip_id(c)


@api_router.put("/companies/{company_id}")
async def update_company(company_id: str, body: CompanyUpdateIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    c = await db.companies.find_one({"id": company_id})
    if not c:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    await db.companies.update_one({"id": company_id}, {"$set": body.model_dump()})
    updated = await db.companies.find_one({"id": company_id})
    return strip_id(updated)


@api_router.get("/companies/{company_id}/prices")
async def get_company_prices(company_id: str, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    prices = await db.customer_prices.find({"companyId": company_id}).to_list(1000)
    return [strip_id(p) for p in prices]
