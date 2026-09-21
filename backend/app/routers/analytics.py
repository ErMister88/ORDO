"""Analytics (margin only for admins)."""
from fastapi import Depends
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, db
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess


@api_router.get("/analytics")
async def analytics(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    months: int = 6,
):
    ids = await visible_company_ids(user, access)
    orders = await db.orders.find({"companyId": {"$in": ids}}).to_list(10000)
    products = await access.products.find().to_list(1000)
    cost_map = {p["id"]: p["cost"] for p in products}

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
        buckets[key] = {"revenue": 0.0, "kg": 0.0, "margin": 0.0}
        labels.append(key)

    for o in orders:
        dt = datetime.fromisoformat(o["createdAt"])
        key = f"{dt.year}-{dt.month:02d}"
        if key in buckets:
            for it in o["items"]:
                rev = it["price"] * it["qty"]
                buckets[key]["revenue"] += rev
                buckets[key]["kg"] += it["qty"]
                buckets[key]["margin"] += (it["price"] - cost_map.get(it["productId"], 0)) * it["qty"]

    month_names = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    is_admin = user["role"] == "admin"
    series = []
    for key in labels:
        y, m = key.split("-")
        row = {
            "label": month_names[int(m) - 1],
            "revenue": round(buckets[key]["revenue"], 2),
            "kg": round(buckets[key]["kg"], 1),
        }
        if is_admin:
            row["margin"] = round(buckets[key]["margin"], 2)
        series.append(row)

    total_rev = sum(s["revenue"] for s in series)
    result = {
        "series": series,
        "totalRevenue": round(total_rev, 2),
        "showMargin": is_admin,
    }
    if is_admin:
        total_margin = sum(buckets[key]["margin"] for key in labels)
        margin_pct = (total_margin / total_rev * 100) if total_rev else 0
        result["totalMargin"] = round(total_margin, 2)
        result["marginPct"] = round(margin_pct, 1)
    return result
