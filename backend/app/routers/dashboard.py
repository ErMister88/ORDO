"""Dashboard KPIs."""
from fastapi import Depends
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..deps import current_user, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess


@api_router.get("/dashboard")
async def dashboard(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    companies = await access.companies.find({"id": {"$in": ids}}).to_list(1000)
    orders = await access.orders.find({"companyId": {"$in": ids}}).to_list(5000)
    offers = await access.offers.find({"companyId": {"$in": ids}}).to_list(1000)
    invoices = await access.invoices.find({"companyId": {"$in": ids}}).to_list(1000)

    def order_total(o):
        return sum(i["price"] * i["qty"] for i in o["items"])

    now = datetime.now(timezone.utc)
    this_month = [o for o in orders if datetime.fromisoformat(o["createdAt"]).month == now.month
                  and datetime.fromisoformat(o["createdAt"]).year == now.year]
    revenue_month = sum(order_total(o) for o in this_month)
    total_kg = sum(c.get("monthlyKg", 0) for c in companies)
    open_offers = [o for o in offers if o["status"] in ("Freigabe nötig", "Freigegeben", "Versendet")]
    approvals = [o for o in offers if o["status"] == "Freigabe nötig"]
    open_invoices = [i for i in invoices if i["status"] != "Bezahlt"]
    open_invoices_sum = sum(i["amount"] for i in open_invoices)

    followups = []
    for c in companies:
        last = await access.orders.find({"companyId": c["id"]}).sort("createdAt", -1).to_list(1)
        if last:
            last_dt = datetime.fromisoformat(last[0]["createdAt"]).replace(tzinfo=timezone.utc)
            days = (now - last_dt).days
            if days > c.get("orderCycleDays", 30):
                followups.append({"companyId": c["id"], "name": c["name"], "days": days})
        else:
            followups.append({"companyId": c["id"], "name": c["name"], "days": None})

    if user["role"] == "customer":
        c = companies[0] if companies else None
        contract = await access.contracts.find_one({"companyId": c["id"]}) if c else None
        return {
            "role": "customer",
            "companyName": c["name"] if c else "",
            "monthlyKg": c.get("monthlyKg", 0) if c else 0,
            "minQtyMonth": contract.get("minQtyMonth", 0) if contract else 0,
            "openInvoices": open_invoices_sum,
            "openInvoicesCount": len(open_invoices),
            "contract": strip_id(contract) if contract else None,
            "ordersCount": len(orders),
        }

    return {
        "role": user["role"],
        "revenueMonth": revenue_month,
        "activeCustomers": len(companies),
        "totalKg": total_kg,
        "openOffers": len(open_offers),
        "pendingApprovals": len(approvals),
        "openInvoices": open_invoices_sum,
        "ordersCount": len(orders),
        "followups": sorted(followups, key=lambda x: -(x["days"] or 999)),
    }
