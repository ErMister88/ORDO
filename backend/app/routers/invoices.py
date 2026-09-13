"""Contracts + invoices."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, db, strip_id, audit
from ..deps import current_user, require_roles, visible_company_ids


@api_router.get("/contracts")
async def get_contracts(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    rows = await db.contracts.find({"companyId": {"$in": ids}}).to_list(1000)
    return [strip_id(r) for r in rows]


@api_router.get("/invoices")
async def get_invoices(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    rows = await db.invoices.find({"companyId": {"$in": ids}}).sort("date", -1).to_list(1000)
    return [strip_id(r) for r in rows]


@api_router.put("/invoices/{invoice_id}/pay")
async def mark_invoice_paid(invoice_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    inv = await db.invoices.find_one({"id": invoice_id})
    if not inv:
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    ids = await visible_company_ids(user)
    if inv["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {"status": "Bezahlt", "paidAt": datetime.now(timezone.utc).isoformat()}},
    )
    await audit(user, "invoice.paid", invoice_id, {})
    return {"ok": True, "status": "Bezahlt"}
