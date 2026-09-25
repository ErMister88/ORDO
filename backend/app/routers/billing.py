"""Invoice generation with VAT + collective (monthly) invoice data."""
from fastapi import Depends, Header, HTTPException
from typing import Annotated, Optional
from datetime import datetime, timezone
from pymongo.errors import DuplicateKeyError

from ..core import api_router, strip_id, next_seq
from ..customer_activity import record_customer_activity
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from .invoices import invoice_references_visible
from .orders import order_references_visible
from ..money import from_minor, require_minor, tax_minor
from ..snapshots import SNAPSHOT_VERSION, redact_internal_snapshot_fields
from ..idempotency import IdempotencyService


async def _build_lines(access: TenantBusinessAccess, items):
    """Return (lineItems, net_total, tax_breakdown, tax_total, gross)."""
    lines = []
    net_total_minor = 0
    tax_breakdown_minor = {}
    currency = access.context.default_currency
    for it in items:
        if it.get("snapshotVersion") != SNAPSHOT_VERSION or it.get("currency") != currency:
            raise HTTPException(
                status_code=409,
                detail="Bestellung besitzt keinen verlässlichen historischen Positions-Snapshot",
            )
        rate = int(it["taxRate"])
        net_minor = require_minor(it["lineTotalMinor"])
        unit_price_minor = require_minor(it["unitPriceMinor"])
        item_tax_minor = tax_minor(net_minor, rate)
        net_total_minor += net_minor
        tax_breakdown_minor[str(rate)] = tax_breakdown_minor.get(str(rate), 0) + item_tax_minor
        lines.append({
            "snapshotVersion": SNAPSHOT_VERSION,
            "productId": it["productId"],
            "sku": it.get("sku"),
            "name": it.get("productName") or it["productId"],
            "description": it.get("description", ""),
            "unit": it.get("unit", "kg"),
            "qty": it["qty"],
            "price": it["price"],
            "unitPriceMinor": unit_price_minor,
            "net": from_minor(net_minor),
            "netMinor": net_minor,
            "taxRate": rate,
            "taxMinor": item_tax_minor,
            "grossMinor": net_minor + item_tax_minor,
            "currency": currency,
            "priceSource": it.get("priceSource"),
            "costMinor": it.get("costMinor"),
        })
    tax_total_minor = sum(tax_breakdown_minor.values())
    gross_minor = net_total_minor + tax_total_minor
    return (
        lines,
        from_minor(net_total_minor),
        {rate: from_minor(value) for rate, value in tax_breakdown_minor.items()},
        from_minor(tax_total_minor),
        from_minor(gross_minor),
        net_total_minor,
        tax_breakdown_minor,
        tax_total_minor,
        gross_minor,
        currency,
    )


async def create_invoice_record(
    access: TenantBusinessAccess,
    order: dict,
    user: dict,
    *,
    operation_id: str | None = None,
) -> dict:
    """Create the immutable invoice snapshot for an already-authorized order."""
    existing = await access.invoices.find_one({"orderId": order["id"]})
    if existing:
        await access.orders.update_one(
            {"id": order["id"]}, {"$set": {"invoiceId": existing["id"]}}
        )
        return existing
    lines, net, breakdown, tax_total, gross, net_minor, breakdown_minor, tax_minor_total, gross_minor, currency = await _build_lines(access, order["items"])
    now = datetime.now(timezone.utc)
    seq = await next_seq("invoice")
    inv_no = f"RE-{now.year}-{seq:04d}"
    payment_term_days = order.get("paymentTermDays")
    due_date = None
    if isinstance(payment_term_days, int) and not isinstance(payment_term_days, bool) and payment_term_days >= 0:
        from datetime import timedelta
        due_date = (now + timedelta(days=payment_term_days)).date().isoformat()
    invoice = {
        "id": inv_no, "companyId": order["companyId"], "orderId": order["id"],
        "date": now.date().isoformat(), "dueDate": due_date,
        "paymentMethod": order.get("paymentMethod", "bank_transfer"),
        "lineItems": lines, "snapshotVersion": 1, "currency": currency,
        "net": net, "netMinor": net_minor, "taxBreakdown": breakdown,
        "taxBreakdownMinor": breakdown_minor, "taxTotal": tax_total,
        "taxTotalMinor": tax_minor_total, "amount": gross, "amountMinor": gross_minor,
        "paidAmountMinor": 0, "companySnapshot": order.get("companySnapshot"),
        "billingAddressSnapshot": order.get("billingAddressSnapshot"),
        "deliveryAddressSnapshot": order.get("deliveryAddressSnapshot"),
        "salesAttribution": order.get("salesAttribution"), "status": "Offen",
        "createdBy": user["id"], "createdAt": now.isoformat(),
    }
    if operation_id:
        invoice["operationId"] = operation_id
    if not await invoice_references_visible(access, invoice):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    try:
        await access.invoices.insert_one(invoice)
    except DuplicateKeyError:
        existing = await access.invoices.find_one({"orderId": order["id"]})
        if not existing:
            raise
        invoice = existing
    await access.orders.update_one(
        {"id": order["id"]}, {"$set": {"invoiceId": invoice["id"]}}
    )
    if invoice["id"] == inv_no:
        await record_customer_activity(
            access, company_id=order["companyId"], actor=user, activity_type="invoice_created",
            title=f"Rechnung {inv_no} erstellt", internal=False,
            reference={"type": "invoice", "id": inv_no},
        )
    return invoice


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
    invoice = await create_invoice_record(access, o, user)
    return strip_id(redact_internal_snapshot_fields(invoice))


@api_router.post("/orders/{order_id}/invoice")
async def create_invoice_for_order_endpoint(
    order_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access,
        actor_id=user["id"],
        operation="invoice.create_for_order",
        key=idempotency_key or "",
        payload={"orderId": order_id},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        order = await access.orders.find_one({"id": order_id})
        if not order or not await order_references_visible(access, order):
            raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
        if order["companyId"] not in await visible_company_ids(user, access):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        invoice = await create_invoice_record(
            access, order, user, operation_id=claim.record_id
        )
        response = strip_id(redact_internal_snapshot_fields(invoice))
        await service.complete(
            claim, response, {"orderId": order_id, "invoiceId": invoice["id"]}
        )
        return response
    except Exception as exc:
        await service.fail(claim, error_code="invoice_workflow_failed", exception=exc)
        raise


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
    lines, net, breakdown, tax_total, gross, net_minor, breakdown_minor, tax_minor_total, gross_minor, currency = await _build_lines(access, all_items)
    return {
        "company": strip_id(company) if company else None,
        "year": year,
        "month": month,
        "orders": picked,
        "lineItems": [
            {key: value for key, value in line.items() if key != "costMinor"}
            for line in lines
        ],
        "snapshotVersion": 1,
        "currency": currency,
        "net": net,
        "netMinor": net_minor,
        "taxBreakdown": breakdown,
        "taxBreakdownMinor": breakdown_minor,
        "taxTotal": tax_total,
        "taxTotalMinor": tax_minor_total,
        "amount": gross,
        "amountMinor": gross_minor,
    }
