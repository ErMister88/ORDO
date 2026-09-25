"""Pay an outstanding invoice via provider-hosted Checkout."""
from fastapi import Depends, Header, HTTPException
from typing import Annotated, Optional

from ..core import api_router
from ..deps import current_user, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from .invoices import invoice_references_visible
from ..money import MoneyError, amount_minor, currency_code
from ..idempotency import IdempotencyService
from ..payment_integrity import checkout_return_urls, create_stripe_checkout, PaymentIntegrityError


async def create_checkout(
    invoice_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
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
        invoice_total_minor = amount_minor(inv, "amount", expected_currency=currency)
    except MoneyError as exc:
        raise HTTPException(status_code=409, detail="Rechnung besitzt keinen gültigen Zahlungsbetrag") from exc
    paid_minor = inv.get("paidAmountMinor", 0)
    if not isinstance(paid_minor, int) or isinstance(paid_minor, bool) or paid_minor < 0:
        raise HTTPException(status_code=409, detail="Rechnung besitzt keinen gültigen Zahlungsstand")
    amount_cents = invoice_total_minor - paid_minor
    if amount_cents <= 0:
        raise HTTPException(status_code=400, detail="Ungültiger Rechnungsbetrag")
    try:
        success_url, cancel_url = checkout_return_urls("invoice", invoice_id)
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=503, detail="Zahlungsdienst ist nicht vollständig konfiguriert") from exc
    return await create_stripe_checkout(
        access,
        resource_type="invoice",
        resource_id=invoice_id,
        operation_id=operation_id or f"direct-invoice-checkout:{invoice_id}",
        expected_amount_minor=amount_cents,
        currency=currency,
        product_name=f"Rechnung {invoice_id}",
        success_url=success_url,
        cancel_url=cancel_url,
    )


@api_router.post("/invoices/{invoice_id}/checkout")
async def create_checkout_endpoint(
    invoice_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    # Authorize before persisting any operational record for a guessed invoice id.
    invoice = await access.invoices.find_one({"id": invoice_id})
    if not invoice or not await invoice_references_visible(access, invoice):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    if invoice["companyId"] not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    service = IdempotencyService(
        access, actor_id=user["id"], operation="invoice.checkout",
        key=idempotency_key or "", payload={"invoiceId": invoice_id},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_checkout(
            invoice_id, user, access,
            operation_id=claim.record_id,
        )
        await service.complete(claim, response, {"invoiceId": invoice_id, "stripeSessionId": response["sessionId"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="invoice_checkout_failed", exception=exc)
        raise


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
    response = {"status": inv.get("status", "Offen")}
    if inv.get("stripeCheckoutState") is not None:
        response["checkoutState"] = inv["stripeCheckoutState"]
    if inv.get("stripePaymentStatus") is not None:
        response["providerStatus"] = inv["stripePaymentStatus"]
    return response
