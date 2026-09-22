"""Stripe (test mode) — pay an outstanding invoice via hosted Checkout."""
import os
import stripe
from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool
from typing import Annotated

from ..core import api_router, logger
from ..deps import current_user, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from .invoices import invoice_references_visible
from ..money import MoneyError, amount_minor, currency_code

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = (os.environ.get("APP_URL") or "https://ordo-connect.preview.emergentagent.com").rstrip("/")
SUCCESS_URL = f"{APP_URL}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}"
CANCEL_URL = f"{APP_URL}/?payment=cancelled"


@api_router.post("/invoices/{invoice_id}/checkout")
async def create_checkout(
    invoice_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    inv = await access.invoices.find_one({"id": invoice_id})
    if not inv or not await invoice_references_visible(access, inv):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if inv["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if inv.get("status") == "Bezahlt":
        raise HTTPException(status_code=409, detail="Rechnung ist bereits bezahlt")
    try:
        currency = currency_code(inv.get("currency") or access.context.default_currency)
        amount_cents = amount_minor(inv, "amount", expected_currency=currency)
    except MoneyError as exc:
        raise HTTPException(status_code=409, detail="Rechnung besitzt keinen gültigen Zahlungsbetrag") from exc
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Ungültiger Rechnungsbetrag")

    def _create():
        return stripe.checkout.Session.create(
            mode="payment",
            currency=currency.lower(),
            locale="de",
            line_items=[{
                "price_data": {
                    "currency": currency.lower(),
                    "unit_amount": amount_cents,
                    "product_data": {"name": f"Rechnung {invoice_id}"},
                },
                "quantity": 1,
            }],
            client_reference_id=invoice_id,
            metadata={"invoiceId": invoice_id},
            success_url=SUCCESS_URL,
            cancel_url=CANCEL_URL,
        )

    try:
        session = await run_in_threadpool(_create)
    except Exception as e:
        logger.warning(f"Stripe Checkout fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail="Zahlung konnte nicht gestartet werden")
    await access.invoices.update_one({"id": invoice_id}, {"$set": {"stripeSessionId": session.id}})
    return {"url": session.url, "sessionId": session.id}


@api_router.get("/invoices/{invoice_id}/payment-status")
async def payment_status(
    invoice_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    inv = await access.invoices.find_one({"id": invoice_id})
    if not inv or not await invoice_references_visible(access, inv):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if inv["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if inv.get("status") == "Bezahlt":
        return {"status": "Bezahlt", "paidAt": inv.get("paidAt")}
    session_id = inv.get("stripeSessionId")
    if session_id:
        try:
            session = await run_in_threadpool(stripe.checkout.Session.retrieve, session_id)
        except Exception as e:
            logger.warning(f"Stripe Status-Abruf fehlgeschlagen: {e}")
            session = None
        if session and session.get("payment_status") in ("paid", "no_payment_required"):
            from datetime import datetime, timezone
            await access.invoices.update_one(
                {"id": invoice_id, "status": {"$ne": "Bezahlt"}},
                {"$set": {"status": "Bezahlt", "paidAt": datetime.now(timezone.utc).isoformat()}},
            )
            return {"status": "Bezahlt"}
    return {"status": inv.get("status", "Offen")}
