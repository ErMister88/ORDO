"""Dashboard KPIs."""
from fastapi import Depends
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..deps import current_user, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from ..money import amount_minor, from_minor, line_total_minor, to_minor


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
    contracts = await access.contracts.find({"companyId": {"$in": ids}}).to_list(1000)
    shop_orders = (
        await access.shop_orders.find({}).sort("createdAt", -1).to_list(1000)
        if user["role"] == "admin" else []
    )

    def order_total(o):
        if isinstance(o.get("netTotalMinor"), int):
            return from_minor(o["netTotalMinor"])
        return from_minor(sum(line_total_minor(to_minor(i["price"]), i["qty"]) for i in o["items"]))

    now = datetime.now(timezone.utc)
    this_month = [o for o in orders if datetime.fromisoformat(o["createdAt"]).month == now.month
                  and datetime.fromisoformat(o["createdAt"]).year == now.year]
    revenue_month = sum(order_total(o) for o in this_month)
    total_kg = sum(c.get("monthlyKg", 0) for c in companies)
    open_offers = [o for o in offers if o["status"] in ("Freigabe nötig", "Freigegeben", "Versendet")]
    approvals = [o for o in offers if o["status"] == "Freigabe nötig"]
    open_invoices = [i for i in invoices if i["status"] != "Bezahlt"]
    open_invoices_sum = sum(
        from_minor(amount_minor(i, "amount", expected_currency=i.get("currency", access.context.default_currency)))
        for i in open_invoices
    )

    company_names = {company["id"]: company.get("name", company["id"]) for company in companies}
    last_order_by_company = {}
    for order in orders:
        company_id = order.get("companyId")
        if company_id and order.get("createdAt") and (
            company_id not in last_order_by_company
            or order["createdAt"] > last_order_by_company[company_id]["createdAt"]
        ):
            last_order_by_company[company_id] = order

    followups = []
    for c in companies:
        last = last_order_by_company.get(c["id"])
        if last:
            last_dt = datetime.fromisoformat(last["createdAt"])
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
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

    activity = []
    for order in orders:
        activity.append({
            "type": "order", "id": order.get("id"), "companyId": order.get("companyId"),
            "companyName": company_names.get(order.get("companyId"), ""),
            "status": order.get("status", ""), "at": order.get("createdAt"),
        })
    for offer in offers:
        activity.append({
            "type": "offer", "id": offer.get("id"), "companyId": offer.get("companyId"),
            "companyName": company_names.get(offer.get("companyId"), ""),
            "status": offer.get("status", ""), "at": offer.get("createdAt"),
        })
    for invoice in invoices:
        activity.append({
            "type": "invoice", "id": invoice.get("id"), "companyId": invoice.get("companyId"),
            "companyName": company_names.get(invoice.get("companyId"), ""),
            "status": invoice.get("status", ""),
            "at": invoice.get("createdAt") or invoice.get("date"),
        })
    activity = sorted(activity, key=lambda row: row.get("at") or "", reverse=True)[:6]

    return {
        "role": user["role"],
        "revenueMonth": revenue_month,
        "activeCustomers": len(companies),
        "totalKg": total_kg,
        "openOffers": len(open_offers),
        "pendingApprovals": len(approvals),
        "openInvoices": open_invoices_sum,
        "ordersCount": len(orders),
        "activeContracts": len(contracts),
        "machinesInField": sum(1 for contract in contracts if contract.get("machine")),
        "shopOrders": len(shop_orders) if user["role"] == "admin" else None,
        "recentActivity": activity,
        "followups": sorted(followups, key=lambda x: -(x["days"] or 999)),
    }
