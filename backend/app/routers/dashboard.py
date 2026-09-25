"""Dashboard KPIs."""
from fastapi import Depends, HTTPException
from typing import Annotated, Optional
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from ..money import amount_minor, from_minor, line_total_minor, to_minor


@api_router.get("/dashboard")
async def dashboard(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    period: str = "month",
    start: Optional[str] = None,
    end: Optional[str] = None,
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

    def parse_boundary(value: str, field: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"Ungültiger {field}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    now = datetime.now(timezone.utc)
    if period == "month":
        period_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    elif period == "quarter":
        first_month = ((now.month - 1) // 3) * 3 + 1
        period_start = datetime(now.year, first_month, 1, tzinfo=timezone.utc)
    elif period == "year":
        period_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    elif period == "custom" and start and end:
        period_start = parse_boundary(start, "Startzeitraum")
    else:
        raise HTTPException(status_code=400, detail="Ungültiger Auswertungszeitraum")
    period_end = parse_boundary(end, "Endzeitraum") if period == "custom" and end else now
    if period_start > period_end:
        raise HTTPException(status_code=400, detail="Startzeitraum muss vor dem Endzeitraum liegen")

    def in_period(row):
        value = row.get("createdAt") or row.get("date")
        if not isinstance(value, str):
            return False
        try:
            point = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if point.tzinfo is None:
                point = point.replace(tzinfo=timezone.utc)
        except ValueError:
            return False
        return period_start <= point.astimezone(timezone.utc) <= period_end

    period_orders = [order for order in orders if in_period(order)]

    def order_total(o):
        if isinstance(o.get("netTotalMinor"), int):
            return from_minor(o["netTotalMinor"])
        return from_minor(sum(line_total_minor(to_minor(i["price"]), i["qty"]) for i in o["items"]))

    this_month = [o for o in orders if datetime.fromisoformat(o["createdAt"]).month == now.month
                  and datetime.fromisoformat(o["createdAt"]).year == now.year]
    revenue_month = sum(order_total(o) for o in this_month)
    total_kg = sum(c.get("monthlyKg", 0) for c in companies)
    open_offers = [o for o in offers if o["status"] in ("Freigabe nötig", "Freigegeben", "Versendet")]
    approvals = [o for o in offers if o["status"] == "Freigabe nötig"]
    open_invoices = [i for i in invoices if i["status"] not in ("Bezahlt", "Storniert")]
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

    customer_metrics = []
    for company in companies:
        company_orders = [order for order in period_orders if order.get("companyId") == company["id"]]
        last_order = last_order_by_company.get(company["id"])
        customer_metrics.append({
            "companyId": company["id"], "name": company.get("name", ""),
            "revenue": round(sum(order_total(order) for order in company_orders), 2),
            "orders": len(company_orders),
            "quantity": round(sum(float(item.get("qty", 0)) for order in company_orders for item in order.get("items", [])), 3),
            "lastPurchaseAt": last_order.get("createdAt") if last_order else None,
        })
    customer_metrics.sort(key=lambda row: (-row["revenue"], row["name"]))

    top_sales_reps = None
    if user["role"] == "admin":
        sales_rows = {}
        for order in period_orders:
            attribution = order.get("salesAttribution") or {}
            sales_id = attribution.get("salesRepId") or (
                attribution.get("actorUserId") if attribution.get("actorRole") == "sales" else None
            )
            if not sales_id:
                continue
            row = sales_rows.setdefault(sales_id, {
                "userId": sales_id, "name": attribution.get("salesRepName") or (
                    attribution.get("actorName") if attribution.get("actorUserId") == sales_id else None
                ) or sales_id,
                "revenue": 0.0, "orders": 0, "quantity": 0.0,
                "margin": 0.0, "marginDataComplete": True,
            })
            row["revenue"] += order_total(order)
            row["orders"] += 1
            for item in order.get("items", []):
                row["quantity"] += float(item.get("qty", 0))
                if isinstance(item.get("costMinor"), int) and isinstance(item.get("lineTotalMinor"), int):
                    row["margin"] += from_minor(item["lineTotalMinor"] - line_total_minor(item["costMinor"], item["qty"]))
                else:
                    row["marginDataComplete"] = False
        assigned_counts = {}
        new_counts = {}
        for company in companies:
            rep_id = company.get("assignedSalesRepId")
            if rep_id and company.get("active", True):
                assigned_counts[rep_id] = assigned_counts.get(rep_id, 0) + 1
            if rep_id and in_period(company):
                new_counts[rep_id] = new_counts.get(rep_id, 0) + 1
        for rep_id, row in sales_rows.items():
            row["activeCustomers"] = assigned_counts.get(rep_id, 0)
            row["newCustomers"] = new_counts.get(rep_id, 0)
            row["revenue"] = round(row["revenue"], 2)
            row["quantity"] = round(row["quantity"], 3)
            row["margin"] = round(row["margin"], 2) if row["marginDataComplete"] else None
        top_sales_reps = sorted(sales_rows.values(), key=lambda row: (-row["revenue"], row["name"]))[:5]

    alerts = []
    for invoice in invoices:
        if invoice.get("status") not in ("Bezahlt", "Storniert") and invoice.get("dueDate") and invoice["dueDate"] < now.date().isoformat():
            alerts.append({"type": "invoice_overdue", "id": invoice.get("id"), "companyId": invoice.get("companyId"), "dueDate": invoice.get("dueDate")})
    tasks = await access.customer_tasks.find({"companyId": {"$in": ids}, "status": "open", "dueAt": {"$lte": now.isoformat()}}).sort("dueAt", 1).to_list(100)
    alerts.extend({"type": "task_due", "id": task.get("id"), "companyId": task.get("companyId"), "dueAt": task.get("dueAt"), "title": task.get("title")} for task in tasks)

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
        "period": {"type": period, "start": period_start.isoformat(), "end": period_end.isoformat()},
        "topCustomers": customer_metrics[:10],
        "topSalesReps": top_sales_reps,
        "managementAlerts": alerts,
    }


def _within_period(row: dict, start: datetime, end: datetime) -> bool:
    value = row.get("createdAt") or row.get("date")
    if not isinstance(value, str):
        return False
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    if point.tzinfo is None:
        point = point.replace(tzinfo=timezone.utc)
    return start <= point.astimezone(timezone.utc) <= end


def _order_value(order: dict) -> float:
    if isinstance(order.get("netTotalMinor"), int):
        return from_minor(order["netTotalMinor"])
    return from_minor(sum(line_total_minor(to_minor(item["price"]), item["qty"]) for item in order.get("items", [])))


def _margin(order: dict) -> tuple[float | None, bool]:
    value = 0.0
    for item in order.get("items", []):
        if not isinstance(item.get("costMinor"), int) or not isinstance(item.get("lineTotalMinor"), int):
            return None, False
        value += from_minor(item["lineTotalMinor"] - line_total_minor(item["costMinor"], item["qty"]))
    return value, True


def _attributed_sales_rep_id(order: dict) -> str | None:
    attribution = order.get("salesAttribution") or {}
    return attribution.get("salesRepId") or (
        attribution.get("actorUserId") if attribution.get("actorRole") == "sales" else None
    )


@api_router.get("/dashboard/sales/{sales_rep_id}")
async def sales_rep_detail(
    sales_rep_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
):
    summary = await dashboard(user, access, period=period, start=start, end=end)
    rep = next((row for row in summary.get("topSalesReps") or [] if row["userId"] == sales_rep_id), None)
    assigned = await access.companies.find({"assignedSalesRepId": sales_rep_id}).to_list(1000)
    if not rep and not assigned:
        raise HTTPException(status_code=404, detail="Vertriebler nicht gefunden")
    start_at = datetime.fromisoformat(summary["period"]["start"])
    end_at = datetime.fromisoformat(summary["period"]["end"])
    company_ids = [company["id"] for company in assigned]
    orders = await access.orders.find({"companyId": {"$in": company_ids}}).to_list(5000)
    period_orders = [
        order for order in orders
        if _within_period(order, start_at, end_at)
        and _attributed_sales_rep_id(order) == sales_rep_id
    ]
    customers = []
    for company in assigned:
        rows = [order for order in period_orders if order.get("companyId") == company["id"]]
        margins = [_margin(order) for order in rows]
        margin_complete = all(complete for _value, complete in margins)
        customers.append({
            "companyId": company["id"], "name": company.get("name", ""),
            "revenue": round(sum(_order_value(order) for order in rows), 2),
            "orders": len(rows),
            "quantity": round(sum(float(item.get("qty", 0)) for order in rows for item in order.get("items", [])), 3),
            "lastOrderAt": max((order.get("createdAt") or "" for order in rows), default=None),
            "margin": round(sum(value or 0 for value, _complete in margins), 2) if margin_complete else None,
            "marginDataComplete": margin_complete,
        })
    customers.sort(key=lambda row: (-row["revenue"], row["name"]))
    rep = rep or {
        "userId": sales_rep_id, "name": sales_rep_id, "revenue": 0,
        "orders": 0, "quantity": 0, "margin": None, "marginDataComplete": False,
        "activeCustomers": sum(1 for company in assigned if company.get("active", True)),
        "newCustomers": 0,
    }
    return {"period": summary["period"], "salesRep": rep, "customers": customers}


@api_router.get("/dashboard/customers/{company_id}")
async def customer_dashboard_detail(
    company_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
):
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    summary = await dashboard(user, access, period=period, start=start, end=end)
    start_at = datetime.fromisoformat(summary["period"]["start"])
    end_at = datetime.fromisoformat(summary["period"]["end"])
    orders = [row for row in await access.orders.find({"companyId": company_id}).to_list(5000)
              if _within_period(row, start_at, end_at)]
    invoices = [row for row in await access.invoices.find({"companyId": company_id}).to_list(2000)
                if _within_period(row, start_at, end_at)]
    products: dict[str, dict] = {}
    for order in orders:
        for item in order.get("items", []):
            product_id = item.get("productId")
            row = products.setdefault(product_id, {
                "productId": product_id, "name": item.get("productName") or product_id,
                "quantity": 0.0, "revenue": 0.0,
            })
            row["quantity"] += float(item.get("qty", 0))
            row["revenue"] += from_minor(item.get("lineTotalMinor", 0))
    margins = [_margin(order) for order in orders]
    margin_complete = all(complete for _value, complete in margins)
    return {
        "period": summary["period"],
        "company": {"id": company["id"], "name": company.get("name", ""),
                    "assignedSalesRepId": company.get("assignedSalesRepId")},
        "revenue": round(sum(_order_value(order) for order in orders), 2),
        "orders": len(orders), "invoices": len(invoices),
        "quantity": round(sum(float(item.get("qty", 0)) for order in orders for item in order.get("items", [])), 3),
        "margin": round(sum(value or 0 for value, _complete in margins), 2) if margin_complete else None,
        "marginDataComplete": margin_complete,
        "products": sorted(products.values(), key=lambda row: (-row["revenue"], row["name"])),
    }
