"""Analytics (margin only for admins)."""
from fastapi import Depends, HTTPException, Query
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from ..money import from_minor, line_total_minor, to_minor


@api_router.get("/analytics")
async def analytics(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    months: Annotated[int, Query(ge=1, le=24)] = 6,
):
    ids = await visible_company_ids(user, access)
    now = datetime.now(timezone.utc)
    buckets = {}
    labels = []
    for i in range(months - 1, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y}-{m:02d}"
        buckets[key] = {"revenueMinor": 0, "kg": 0.0, "marginMinor": 0, "marginComplete": True}
        labels.append(key)

    first_year, first_month = (int(value) for value in labels[0].split("-"))
    period_start = datetime(first_year, first_month, 1, tzinfo=timezone.utc).isoformat()
    orders = await access.orders.find({
        "companyId": {"$in": ids}, "createdAt": {"$gte": period_start},
    }).sort("createdAt", 1).to_list(10001)
    if len(orders) > 10000:
        raise HTTPException(
            status_code=503,
            detail="Auswertung ist für den gewählten Zeitraum zu groß. Bitte Zeitraum eingrenzen.",
        )

    for o in orders:
        dt = datetime.fromisoformat(o["createdAt"])
        key = f"{dt.year}-{dt.month:02d}"
        if key in buckets:
            for it in o["items"]:
                rev_minor = it.get("lineTotalMinor")
                if not isinstance(rev_minor, int):
                    rev_minor = line_total_minor(to_minor(it["price"]), it["qty"])
                buckets[key]["revenueMinor"] += rev_minor
                buckets[key]["kg"] += it["qty"]
                cost_minor = it.get("costMinor")
                if isinstance(cost_minor, int):
                    buckets[key]["marginMinor"] += rev_minor - line_total_minor(cost_minor, it["qty"])
                else:
                    buckets[key]["marginComplete"] = False

    month_names = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    is_admin = user["role"] == "admin"
    series = []
    for key in labels:
        y, m = key.split("-")
        row = {
            "label": month_names[int(m) - 1],
            "revenue": from_minor(buckets[key]["revenueMinor"]),
            "kg": round(buckets[key]["kg"], 1),
        }
        if is_admin:
            row["margin"] = from_minor(buckets[key]["marginMinor"])
            row["marginComplete"] = buckets[key]["marginComplete"]
        series.append(row)

    total_rev = sum(s["revenue"] for s in series)
    result = {
        "series": series,
        "totalRevenue": round(total_rev, 2),
        "showMargin": is_admin,
    }
    if is_admin:
        total_margin = sum(from_minor(buckets[key]["marginMinor"]) for key in labels)
        margin_pct = (total_margin / total_rev * 100) if total_rev else 0
        result["totalMargin"] = round(total_margin, 2)
        result["marginPct"] = round(margin_pct, 1)
        result["marginDataComplete"] = all(buckets[key]["marginComplete"] for key in labels)
    return result
