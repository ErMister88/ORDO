"""Contracts, invoices and tenant-scoped payment records."""
import secrets
from fastapi import Depends, Header, HTTPException, Query
from typing import Annotated, Optional
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..audit_service import tenant_audit
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..tenant_access import TenantBusinessAccess
from ..models import InvoiceStatusIn, PaymentRecordIn
from ..money import amount_minor, from_minor, to_minor
from .orders import order_references_visible
from ..snapshots import redact_internal_snapshot_fields
from ..idempotency import IdempotencyService
from ..pagination import bounded_list


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


async def _visible_invoice_ids(access: TenantBusinessAccess, invoices: list[dict]) -> set[str]:
    """Validate one invoice page with bounded bulk reads instead of per-row queries."""

    company_ids = {row.get("companyId") for row in invoices if isinstance(row.get("companyId"), str)}
    existing_companies = {
        row["id"] for row in await access.companies.find({"id": {"$in": list(company_ids)}}).to_list(len(company_ids))
    } if company_ids else set()
    order_ids = {row.get("orderId") for row in invoices if isinstance(row.get("orderId"), str)}
    order_rows = await access.orders.find({"id": {"$in": list(order_ids)}}).to_list(len(order_ids)) if order_ids else []
    orders_by_id = {row["id"]: row for row in order_rows}
    product_ids: set[str] = set()
    offer_ids: set[str] = set()
    subscription_ids: set[str] = set()
    for order in order_rows:
        for item in order.get("items", []):
            if item.get("snapshotVersion") == 1 and item.get("currency") == order.get("currency"):
                continue
            if isinstance(item.get("productId"), str):
                product_ids.add(item["productId"])
        if isinstance(order.get("fromOffer"), str):
            offer_ids.add(order["fromOffer"])
        if isinstance(order.get("fromSubscription"), str):
            subscription_ids.add(order["fromSubscription"])
    products = {
        row["id"] for row in await access.products.find({"id": {"$in": list(product_ids)}}).to_list(len(product_ids))
    } if product_ids else set()
    offers = {
        row["id"]: row for row in await access.offers.find({"id": {"$in": list(offer_ids)}}).to_list(len(offer_ids))
    } if offer_ids else {}
    subscriptions = {
        row["id"]: row for row in await access.subscriptions.find({"id": {"$in": list(subscription_ids)}}).to_list(len(subscription_ids))
    } if subscription_ids else {}

    visible: set[str] = set()
    for invoice in invoices:
        invoice_id = invoice.get("id")
        company_id = invoice.get("companyId")
        if not isinstance(invoice_id, str) or company_id not in existing_companies:
            continue
        order_id = invoice.get("orderId")
        if order_id is None:
            visible.add(invoice_id)
            continue
        order = orders_by_id.get(order_id) if isinstance(order_id, str) else None
        if not order or order.get("companyId") != company_id:
            continue
        if any(
            not isinstance(item.get("productId"), str) or item.get("productId") not in products
            for item in order.get("items", [])
            if not (item.get("snapshotVersion") == 1 and item.get("currency") == order.get("currency"))
        ):
            continue
        offer_id = order.get("fromOffer")
        if offer_id is not None and (
            not isinstance(offer_id, str) or offers.get(offer_id, {}).get("companyId") != company_id
        ):
            continue
        subscription_id = order.get("fromSubscription")
        if subscription_id is not None and (
            not isinstance(subscription_id, str)
            or subscriptions.get(subscription_id, {}).get("companyId") != company_id
        ):
            continue
        visible.add(invoice_id)
    return visible


@api_router.get("/contracts")
async def get_contracts(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    ids = await visible_company_ids(user, access)
    rows = await bounded_list(
        access.contracts.find({"companyId": {"$in": ids}}).sort([("start", -1), ("id", -1)]),
        limit=limit, offset=offset,
    )
    company_ids = {row.get("companyId") for row in rows if isinstance(row.get("companyId"), str)}
    product_ids = {row.get("productId") for row in rows if isinstance(row.get("productId"), str)}
    visible_companies = {
        row["id"] for row in await access.companies.find({"id": {"$in": list(company_ids)}}).to_list(len(company_ids))
    } if company_ids else set()
    visible_products = {
        row["id"] for row in await access.products.find({"id": {"$in": list(product_ids)}}).to_list(len(product_ids))
    } if product_ids else set()
    visible = []
    for contract in rows:
        company_id = contract.get("companyId")
        product_id = contract.get("productId")
        if company_id in visible_companies and (product_id is None or product_id in visible_products):
            visible.append(strip_id(contract))
    return visible


@api_router.get("/invoices")
async def get_invoices(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    ids = await visible_company_ids(user, access)
    rows = await bounded_list(
        access.invoices.find({"companyId": {"$in": ids}}).sort([("date", -1), ("id", -1)]),
        limit=limit, offset=offset,
    )
    visible_invoice_ids = await _visible_invoice_ids(access, rows)
    today = datetime.now(timezone.utc).date().isoformat()
    result = []
    for row in rows:
        if row.get("id") not in visible_invoice_ids:
            continue
        public = strip_id(redact_internal_snapshot_fields(row))
        if public.get("status") == "Offen" and public.get("dueDate") and public["dueDate"] < today:
            public["status"] = "Überfällig"
        result.append(public)
    return result


async def _require_manageable_invoice(user: dict, access: TenantBusinessAccess, invoice_id: str) -> dict:
    invoice = await access.invoices.find_one({"id": invoice_id})
    if not invoice or not await invoice_references_visible(access, invoice):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    if invoice["companyId"] not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return invoice


def _existing_payment_response(invoice: dict, operation_id: str | None) -> dict | None:
    if not operation_id:
        return None
    for payment in invoice.get("paymentRecords", []):
        if payment.get("operationId") == operation_id:
            return {
                "ok": True,
                "status": invoice.get("status", "Offen"),
                "paidAmount": from_minor(invoice.get("paidAmountMinor", 0)),
                "paidAmountMinor": invoice.get("paidAmountMinor", 0),
            }
    return None


async def _record_payment(
    user: dict,
    access: TenantBusinessAccess,
    invoice: dict,
    body: PaymentRecordIn,
    *,
    operation_id: str | None = None,
) -> dict:
    recovered = _existing_payment_response(invoice, operation_id)
    if recovered:
        return recovered
    currency = invoice.get("currency", access.context.default_currency)
    amount_minor_value = to_minor(body.amount)
    invoice_total = amount_minor(invoice, "amount", expected_currency=currency)
    already_paid = invoice.get("paidAmountMinor", 0)
    if not isinstance(already_paid, int) or isinstance(already_paid, bool) or already_paid < 0:
        raise HTTPException(status_code=409, detail="Rechnung besitzt einen ungültigen Zahlungsstand")
    if invoice.get("status") in ("Bezahlt", "Storniert"):
        raise HTTPException(status_code=409, detail="Für diese Rechnung kann keine Zahlung erfasst werden")
    if amount_minor_value > invoice_total - already_paid:
        raise HTTPException(status_code=409, detail="Zahlung übersteigt den offenen Rechnungsbetrag")
    paid_at = body.paidAt or datetime.now(timezone.utc)
    if paid_at.tzinfo is None:
        raise HTTPException(status_code=400, detail="Zahlungszeitpunkt benötigt eine Zeitzone")
    new_paid = already_paid + amount_minor_value
    status = "Bezahlt" if new_paid == invoice_total else "Teilweise bezahlt"
    payment = {
        "id": "pay-" + secrets.token_hex(8), "invoiceId": invoice["id"],
        "companyId": invoice["companyId"], "amount": from_minor(amount_minor_value),
        "amountMinor": amount_minor_value, "currency": currency, "method": body.method,
        "reference": body.reference.strip(), "paidAt": paid_at.astimezone(timezone.utc).isoformat(),
        "createdBy": user["id"], "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    if operation_id:
        payment["operationId"] = operation_id
    update = {"paidAmountMinor": new_paid, "status": status, "paymentMethod": body.method}
    if status == "Bezahlt":
        update["paidAt"] = payment["paidAt"]
    expected_state = {
        "id": invoice["id"],
        "status": invoice.get("status", "Offen"),
    }
    if "paidAmountMinor" in invoice:
        expected_state["paidAmountMinor"] = already_paid
    else:
        expected_state["paidAmountMinor"] = {"$exists": False}
    result = await access.invoices.update_one(
        expected_state,
        {"$set": update, "$push": {"paymentRecords": payment}},
    )
    if result.matched_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Rechnung wurde zwischenzeitlich geändert. Bitte Zahlungsstand neu laden.",
        )
    await tenant_audit(access, user, "invoice.payment", invoice["id"], {"paymentId": payment["id"], "status": status})
    return {"ok": True, "status": status, "paidAmount": from_minor(new_paid), "paidAmountMinor": new_paid}


async def mark_invoice_paid(
    invoice_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    inv = await _require_manageable_invoice(user, access, invoice_id)
    total = amount_minor(inv, "amount", expected_currency=inv.get("currency", access.context.default_currency))
    paid = inv.get("paidAmountMinor", 0)
    return await _record_payment(user, access, inv, PaymentRecordIn(
        amount=from_minor(total - paid), method=inv.get("paymentMethod", "other")
        if inv.get("paymentMethod") in ("bank_transfer", "cash", "card", "other") else "other",
    ))


async def record_invoice_payment(
    invoice_id: str,
    body: PaymentRecordIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    invoice = await _require_manageable_invoice(user, access, invoice_id)
    return await _record_payment(user, access, invoice, body)


async def _idempotent_payment(
    *,
    invoice_id: str,
    body: PaymentRecordIn | None,
    full_balance: bool,
    user: dict,
    access: TenantBusinessAccess,
    idempotency_key: str | None,
):
    payload = {"invoiceId": invoice_id, "fullBalance": full_balance}
    if body is not None:
        payload["payment"] = body.model_dump(mode="json")
    service = IdempotencyService(
        access,
        actor_id=user["id"],
        operation="invoice.payment",
        key=idempotency_key or "",
        payload=payload,
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        invoice = await _require_manageable_invoice(user, access, invoice_id)
        recovered = _existing_payment_response(invoice, claim.record_id)
        if recovered:
            response = recovered
        else:
            payment_body = body
            if full_balance:
                total = amount_minor(
                    invoice, "amount",
                    expected_currency=invoice.get("currency", access.context.default_currency),
                )
                paid = invoice.get("paidAmountMinor", 0)
                if paid >= total or invoice.get("status") in ("Bezahlt", "Storniert"):
                    raise HTTPException(status_code=409, detail="Für diese Rechnung kann keine Zahlung erfasst werden")
                payment_body = PaymentRecordIn(
                    amount=from_minor(total - paid),
                    method=invoice.get("paymentMethod", "other")
                    if invoice.get("paymentMethod") in ("bank_transfer", "cash", "card", "other")
                    else "other",
                )
            response = await _record_payment(
                user, access, invoice, payment_body, operation_id=claim.record_id
            )
        await service.complete(claim, response, {"invoiceId": invoice_id})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="invoice_payment_failed", exception=exc)
        raise


@api_router.put("/invoices/{invoice_id}/pay")
async def mark_invoice_paid_endpoint(
    invoice_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    return await _idempotent_payment(
        invoice_id=invoice_id, body=None, full_balance=True, user=user, access=access,
        idempotency_key=idempotency_key,
    )


@api_router.post("/invoices/{invoice_id}/payments")
async def record_invoice_payment_endpoint(
    invoice_id: str,
    body: PaymentRecordIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    return await _idempotent_payment(
        invoice_id=invoice_id, body=body, full_balance=False, user=user, access=access,
        idempotency_key=idempotency_key,
    )


@api_router.get("/invoices/{invoice_id}/payments")
async def list_invoice_payments(
    invoice_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    invoice = await access.invoices.find_one({"id": invoice_id})
    if not invoice or not await invoice_references_visible(access, invoice):
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    if invoice["companyId"] not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    rows = sorted(
        invoice.get("paymentRecords", []),
        key=lambda row: row.get("paidAt", ""),
        reverse=True,
    )
    public_fields = {
        "id", "invoiceId", "companyId", "amount", "amountMinor", "currency",
        "method", "reference", "paidAt", "createdAt",
    }
    return [{key: value for key, value in row.items() if key in public_fields} for row in rows]


@api_router.put("/invoices/{invoice_id}/status")
async def update_invoice_status(
    invoice_id: str,
    body: InvoiceStatusIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    invoice = await _require_manageable_invoice(user, access, invoice_id)
    if body.status in ("Bezahlt", "Teilweise bezahlt"):
        raise HTTPException(status_code=400, detail="Zahlungsstatus muss über eine Zahlung erfasst werden")
    if invoice.get("status") == "Bezahlt":
        raise HTTPException(status_code=409, detail="Bezahlte Rechnung kann nicht direkt geändert werden")
    await access.invoices.update_one({"id": invoice_id}, {"$set": {"status": body.status}})
    await tenant_audit(access, user, "invoice.status", invoice_id, {"status": body.status})
    return {"ok": True, "status": body.status}
