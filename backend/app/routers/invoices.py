"""Contracts + invoices."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id, audit
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from .orders import order_references_visible


async def invoice_references_visible(access: TenantBusinessAccess, invoice: dict) -> bool:
    company_id = invoice.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    order_id = invoice.get("orderId")
    if order_id is None:
        return True
    if not isinstance(order_id, str):
        return False
    order = await access.orders.find_one({"id": order_id})
    return bool(
        order
        and order.get("companyId") == company_id
        and await order_references_visible(access, order)
    )


@api_router.get("/contracts")
async def get_contracts(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    rows = await access.contracts.find({"companyId": {"$in": ids}}).to_list(1000)
    visible = []
    for contract in rows:
        company_id = contract.get("companyId")
        product_id = contract.get("productId")
        company = (
            await access.companies.find_one({"id": company_id})
            if isinstance(company_id, str)
            else None
        )
        product = (
            await access.products.find_one({"id": product_id})
            if isinstance(product_id, str)
            else product_id is None
        )
        if company and product:
            visible.append(strip_id(contract))
    return visible


@api_router.get("/invoices")
async def get_invoices(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    rows = await access.invoices.find({"companyId": {"$in": ids}}).sort("date", -1).to_list(1000)
    return [strip_id(r) for r in rows if await invoice_references_visible(access, r)]


@api_router.put("/invoices/{invoice_id}/pay")
async def mark_invoice_paid(
    invoice_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    inv = await access.invoices.find_one({"id": invoice_id})
    if not inv or not await invoice_references_visible(access, inv):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if inv["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await access.invoices.update_one(
        {"id": invoice_id},
        {"$set": {"status": "Bezahlt", "paidAt": datetime.now(timezone.utc).isoformat()}},
    )
    await audit(user, "invoice.paid", invoice_id, {})
    return {"ok": True, "status": "Bezahlt"}
