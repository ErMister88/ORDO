"""Invoice generation with VAT + collective (monthly) invoice data."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id, next_seq
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from .invoices import invoice_references_visible
from .orders import order_references_visible


async def _build_lines(access: TenantBusinessAccess, items):
    """Return (lineItems, net_total, tax_breakdown, tax_total, gross)."""
    lines = []
    net_total = 0.0
    tax_breakdown = {}
    for it in items:
        p = await access.products.find_one({"id": it["productId"]})
        rate = int((p or {}).get("taxRate", 7))
        net = round(it["price"] * it["qty"], 2)
        tax = round(net * rate / 100, 2)
        net_total += net
        tax_breakdown[str(rate)] = round(tax_breakdown.get(str(rate), 0.0) + tax, 2)
        lines.append({
            "productId": it["productId"],
            "name": f"{p['brand']} {p['name']}" if p else it["productId"],
            "unit": (p or {}).get("unit", "kg"),
            "qty": it["qty"],
            "price": it["price"],
            "net": net,
            "taxRate": rate,
        })
    tax_total = round(sum(tax_breakdown.values()), 2)
    gross = round(net_total + tax_total, 2)
    return lines, round(net_total, 2), tax_breakdown, tax_total, gross


@api_router.post("/orders/{order_id}/invoice")
async def create_invoice_for_order(
    order_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o.get("invoiceId"):
        raise HTTPException(status_code=400, detail="Für diese Bestellung existiert bereits eine Rechnung")
    lines, net, breakdown, tax_total, gross = await _build_lines(access, o["items"])
    now = datetime.now(timezone.utc)
    seq = await next_seq("invoice")
    inv_no = f"RE-{now.year}-{seq:04d}"
    invoice = {
        "id": inv_no,
        "companyId": o["companyId"],
        "orderId": order_id,
        "date": now.date().isoformat(),
        "lineItems": lines,
        "net": net,
        "taxBreakdown": breakdown,
        "taxTotal": tax_total,
        "amount": gross,
        "status": "Offen",
        "createdAt": now.isoformat(),
    }
    if not await invoice_references_visible(access, invoice):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    await access.invoices.insert_one(invoice)
    await access.orders.update_one({"id": order_id}, {"$set": {"invoiceId": inv_no}})
    return strip_id(invoice)


@api_router.get("/companies/{company_id}/collective-invoice")
async def collective_invoice(company_id: str, year: int, month: int,
                             user: Annotated[dict, Depends(require_roles("admin", "sales"))],
                             access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    ids = await visible_company_ids(user, access)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    orders = await access.orders.find({"companyId": company_id}).to_list(5000)
    picked = []
    all_items = []
    for o in orders:
        if not await order_references_visible(access, o):
            continue
        if o.get("status") == "Storniert":
            continue
        dt = datetime.fromisoformat(o["createdAt"])
        if dt.year == year and dt.month == month:
            picked.append({"id": o["id"], "date": dt.date().isoformat()})
            all_items.extend(o["items"])
    lines, net, breakdown, tax_total, gross = await _build_lines(access, all_items)
    return {
        "company": strip_id(company) if company else None,
        "year": year,
        "month": month,
        "orders": picked,
        "lineItems": lines,
        "net": net,
        "taxBreakdown": breakdown,
        "taxTotal": tax_total,
        "amount": gross,
    }
