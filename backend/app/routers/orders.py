"""Orders."""
from html import escape
from fastapi import Depends, Header, HTTPException, Query
from typing import Annotated, Optional
from datetime import datetime, timedelta, timezone

from ..core import api_router, strip_id, next_seq, logger, ORDER_STATUS_FLOW
from ..audit_service import tenant_audit
from ..customer_activity import record_customer_activity
from ..customer_master import company_snapshot, resolve_address_snapshot
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import OrderCreate, OrderStatusIn, OrderItemIn  # noqa: F401
from ..emailer import send_email, email_shell, company_recipient
from ..money import from_minor
from ..observability import report_operational_failure
from ..pricing_engine import PricingEngine, PricingError
from ..snapshots import items_total_minor, redact_internal_snapshot_fields
from ..tenant_access import TenantBusinessAccess
from ..idempotency import IdempotencyClaim, IdempotencyService
from ..pagination import bounded_list
from ..financial_operations import FinancialConfigurationError, evaluate_b2b_order, public_approval, validate_b2b_payment_method, company_terms
from ..commissions import CommissionAmbiguous, create_pending_commission, validate_commission_for_order


def _public_order(order: dict, user: dict) -> dict:
    public = strip_id(redact_internal_snapshot_fields(order))
    if isinstance(public.get("financialApproval"), dict):
        public["financialApproval"] = public_approval(
            public["financialApproval"],
            admin=user.get("role") == "admin",
            customer=user.get("role") == "customer",
        )
    return public


async def order_references_visible(access: TenantBusinessAccess, order: dict) -> bool:
    company_id = order.get("companyId")
    if not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in order.get("items", []):
        if item.get("snapshotVersion") == 1 and item.get("currency") == order.get("currency"):
            continue
        product_id = item.get("productId")
        if not isinstance(product_id, str) or not await access.products.find_one({"id": product_id}):
            return False
    for field, collection in (
        ("fromOffer", access.offers),
        ("fromSubscription", access.subscriptions),
    ):
        reference_id = order.get(field)
        if reference_id is None:
            continue
        if not isinstance(reference_id, str):
            return False
        source = await collection.find_one({"id": reference_id})
        if not source or source.get("companyId") != company_id:
            return False
    return True


@api_router.get("/orders")
async def get_orders(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    ids = await visible_company_ids(user, access)
    orders = await bounded_list(
        access.orders.find({"companyId": {"$in": ids}}).sort([("createdAt", -1), ("id", -1)]),
        limit=limit, offset=offset,
    )
    return [_public_order(o, user) for o in orders]


async def _resolve_unit_price(
    access: TenantBusinessAccess,
    company_id: str,
    product: dict,
    qty: float,
) -> float:
    """Compatibility helper backed by the central authoritative B2B engine."""
    minor, _source = await _resolve_unit_money(access, company_id, product, qty)
    return from_minor(minor)


async def _resolve_unit_money(
    access: TenantBusinessAccess,
    company_id: str,
    product: dict,
    qty: float,
) -> tuple[int, str]:
    """Compatibility helper backed by the central authoritative B2B engine."""
    _stored_product, quote = await PricingEngine(access).quote_b2b(
        company_id, product["id"], qty
    )
    return quote.final_unit_price_minor, quote.price_source


async def _evaluate_stored_order(
    access: TenantBusinessAccess, order: dict, company: dict,
) -> dict:
    order_total_minor = order.get("netTotalMinor")
    if isinstance(order_total_minor, bool) or not isinstance(order_total_minor, int) or order_total_minor < 0:
        raise FinancialConfigurationError("Order financial snapshot is invalid")
    item_products = []
    for item in order.get("items") or []:
        product_id = item.get("productId")
        product = await access.products.find_one({"id": product_id}) if isinstance(product_id, str) else None
        item_products.append({
            "product": product or {"id": product_id, "unit": item.get("unit")},
            "qty": item.get("qty"),
        })
    terms_snapshot = order.get("paymentTermsSnapshot") or {}
    return await evaluate_b2b_order(
        access, company=company, item_products=item_products,
        order_total_minor=order_total_minor,
        currency=order.get("currency", access.context.default_currency),
        payment_terms_days=terms_snapshot.get("paymentTermsDays", order.get("paymentTermDays")),
        order_already_in_exposure=True,
    )


async def create_order(
    body: OrderCreate,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
    workflow: tuple[IdempotencyService, IdempotencyClaim] | None = None,
):
    ids = await visible_company_ids(user, access)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if not body.items:
        raise HTTPException(status_code=400, detail="Bestellung enthält keine Artikel")
    validate_b2b_payment_method(body.paymentMethod)
    if user.get("role") == "customer" and body.createInvoice:
        raise HTTPException(status_code=403, detail="Rechnungserstellung ist nur für Mitarbeiter verfügbar")
    if body.paymentTermDays is not None and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Zahlungsziel darf nur durch Administratoren festgelegt werden")
    items = []
    currency = access.context.default_currency
    company = await access.companies.find_one({"id": body.companyId})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if operation_id:
        existing = await access.orders.find_one({"operationId": operation_id})
        if existing:
            response = _public_order(existing, user)
            if workflow:
                await workflow[0].checkpoint(
                    workflow[1], "order_recovered", {"orderId": existing["id"]}
                )
            if body.createInvoice:
                from .billing import create_invoice_record
                invoice = await create_invoice_record(
                    access, existing, user, operation_id=operation_id
                )
                response["invoice"] = strip_id(redact_internal_snapshot_fields(invoice))
                response["invoiceId"] = invoice["id"]
            return response
    item_products = []
    for it in body.items:
        if it.qty <= 0:
            raise HTTPException(status_code=400, detail="Ungültige Menge")
        try:
            prod, quote = await PricingEngine(access).quote_b2b(
                body.companyId, it.productId, it.qty
            )
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        items.append(quote.snapshot(prod))
        item_products.append({"product": prod, "qty": it.qty})
    terms = company_terms(company, default_currency=currency)
    effective_payment_terms = body.paymentTermDays if body.paymentTermDays is not None else terms.payment_terms_days
    evaluation = await evaluate_b2b_order(
        access, company=company, item_products=item_products,
        order_total_minor=items_total_minor(items, currency=currency), currency=currency,
        payment_terms_days=effective_payment_terms,
    )
    now = datetime.now(timezone.utc)
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Freigabe nötig" if evaluation["required"] else "Neu",
        "items": items,
        "currency": currency,
        "netTotalMinor": items_total_minor(items, currency=currency),
        "snapshotVersion": 1,
        "paymentMethod": body.paymentMethod,
        "invoiceRequested": body.createInvoice,
        "paymentTermDays": effective_payment_terms,
        "paymentTermsSnapshot": evaluation["termsSnapshot"],
        "financialApproval": evaluation,
        "companySnapshot": company_snapshot(company),
        "billingAddressSnapshot": await resolve_address_snapshot(
            access, body.companyId, body.billingAddressId, preferred_type="billing"
        ),
        "deliveryAddressSnapshot": await resolve_address_snapshot(
            access, body.companyId, body.deliveryAddressId, preferred_type="shipping"
        ),
        "salesAttribution": {
            "actorUserId": user["id"], "actorName": user.get("name", ""),
            "actorRole": user.get("role"), "membershipId": access.context.membership_id,
            "salesRepId": company.get("assignedSalesRepId"),
            "salesRepName": company.get("assignedSalesRepName", ""),
        },
        "createdAt": now.isoformat(),
    }
    if operation_id:
        order["operationId"] = operation_id
    await access.orders.insert_one(order)
    if workflow:
        await workflow[0].checkpoint(workflow[1], "order_created", {"orderId": order_no})
    await record_customer_activity(
        access, company_id=body.companyId, actor=user, activity_type="order_created",
        title=f"Bestellung {order_no} erstellt", internal=False,
        reference={"type": "order", "id": order_no},
    )
    response = _public_order(order, user)
    if body.createInvoice and not evaluation["required"]:
        from .billing import create_invoice_record
        invoice = await create_invoice_record(access, order, user)
        response["invoice"] = strip_id(redact_internal_snapshot_fields(invoice))
        response["invoiceId"] = invoice["id"]
    elif body.createInvoice and evaluation["required"]:
        response["invoiceDeferredForApproval"] = True
    return response


@api_router.post("/orders")
async def create_order_endpoint(
    body: OrderCreate,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access,
        actor_id=user["id"],
        operation="order.create",
        key=idempotency_key or "",
        payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_order(
            body, user, access, operation_id=claim.record_id, workflow=(service, claim)
        )
        refs = {"orderId": response["id"]}
        if response.get("invoiceId"):
            refs["invoiceId"] = response["invoiceId"]
        await service.complete(claim, response, refs)
        return response
    except Exception as exc:
        await service.fail(claim, error_code="order_workflow_failed", exception=exc)
        raise


@api_router.get("/orders/{order_id}")
async def get_order(
    order_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return _public_order(o, user)


@api_router.put("/orders/{order_id}/status")
async def set_order_status(
    order_id: str,
    body: OrderStatusIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.status not in ORDER_STATUS_FLOW:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    approval = o.get("financialApproval") or {}
    if body.status == "Bestätigt" and approval.get("status") != "approved":
        company = await access.companies.find_one({"id": o["companyId"]})
        if not company:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        try:
            evaluation = await _evaluate_stored_order(access, o, company)
        except FinancialConfigurationError as exc:
            raise HTTPException(
                status_code=409, detail="Bestellung besitzt keinen verlässlichen Finanz-Snapshot",
            ) from exc
        if evaluation["required"]:
            await access.orders.update_one({"id": order_id}, {"$set": {
                "status": "Freigabe nötig",
                "financialApproval": evaluation,
                "paymentTermsSnapshot": evaluation["termsSnapshot"],
                "paymentTermDays": evaluation["termsSnapshot"].get("paymentTermsDays"),
            }})
            raise HTTPException(status_code=409, detail="Bestellung benötigt zuerst eine Finanzfreigabe")
        approval = evaluation
    if body.status == "Bestätigt":
        try:
            await validate_commission_for_order(access, o)
        except CommissionAmbiguous as exc:
            raise HTTPException(status_code=409, detail="Provisionsvereinbarung ist nicht eindeutig konfiguriert") from exc
    update = {"status": body.status}
    if body.status == "Bestätigt" and approval.get("status") == "not_required":
        update.update({
            "financialApproval": approval,
            "paymentTermsSnapshot": approval["termsSnapshot"],
            "paymentTermDays": approval["termsSnapshot"].get("paymentTermsDays"),
        })
    if body.status == "Versendet" and not o.get("trackingNumber"):
        now = datetime.now(timezone.utc)
        eta = now + timedelta(days=2)
        update["trackingNumber"] = f"SS{now.strftime('%y%m%d')}{o['id'].split('-')[-1]}"
        update["shippedAt"] = now.isoformat()
        update["estimatedDelivery"] = eta.date().isoformat()
    await access.orders.update_one({"id": order_id}, {"$set": update})
    if body.status == "Bestätigt":
        await create_pending_commission(access, order={**o, **update})
    await tenant_audit(access, user, "order.status", order_id, {"status": body.status})
    if body.status == "Versendet":
        try:
            email, cname = await company_recipient(access, o["companyId"])
            if email:
                tracking = update.get("trackingNumber") or o.get("trackingNumber") or "-"
                eta = update.get("estimatedDelivery") or o.get("estimatedDelivery")
                eta_row = (
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Voraussichtliche Lieferung</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(eta))}</td></tr>"
                    if eta else ""
                )
                inner = (
                    f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(cname)},<br>"
                    f"Ihre Bestellung <strong>{escape(o['id'])}</strong> wurde versendet und ist unterwegs.</p>"
                    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Sendungsnummer</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(tracking))}</td></tr>"
                    f"{eta_row}"
                    "</table>"
                )
                html = email_shell("Bestellung versendet", "Ihre Lieferung ist auf dem Weg.", inner)
                await send_email(
                    access=access, to=email, subject=f"Bestellung {o['id']} ist unterwegs", html=html,
                    idempotency_key=f"order:{o['id']}:shipped",
                    template_key="order.shipped", resource_type="order", resource_id=o["id"],
                )
        except Exception:
            await report_operational_failure(
                access, logger, operation="order.shipment_email", category="email_delivery",
            )
    return {"ok": True, "status": body.status}


@api_router.put("/orders/{order_id}/cancel")
async def cancel_order(
    order_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.orders.find_one({"id": order_id})
    if not o or not await order_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o["status"] != "Neu":
        raise HTTPException(
            status_code=400,
            detail="Bestellung wird bereits bearbeitet und kann nicht mehr storniert werden.",
        )
    await access.orders.update_one(
        {"id": order_id},
        {"$set": {"status": "Storniert", "cancelledAt": datetime.now(timezone.utc).isoformat(), "cancelledBy": user["id"]}},
    )
    return {"ok": True, "status": "Storniert"}
