"""Tenant-scoped financial operations APIs for terms, receivables and commission."""

from __future__ import annotations

from datetime import datetime, timezone
import secrets
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException, Query

from ..accounting import AccountingProviderError, request_accounting_retry
from ..audit_service import tenant_audit
from ..commissions import append_commission_adjustment, create_settlement, mark_settlement_paid
from ..core import api_router, strip_id
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..financial_operations import available_credit, company_terms, receivables_summary, public_approval
from ..models import CompanyFinancialTermsIn, CommissionAdjustmentIn, CommissionAgreementIn, CommissionPayoutIn, CommissionSettlementIn, DecisionIn
from ..pagination import bounded_list
from ..tenant_access import TenantBusinessAccess


async def _visible_company(company_id: str, user: dict, access: TenantBusinessAccess) -> dict:
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if company_id not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return company


def _require_admin_runtime(user: dict) -> None:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Keine Berechtigung")


def _validate_agreement_period(body: CommissionAgreementIn) -> None:
    if body.validFrom and body.validUntil and body.validFrom >= body.validUntil:
        raise HTTPException(status_code=400, detail="Gültigkeitszeitraum der Provisionsvereinbarung ist ungültig")


def _parse_utc_filter(value: str | None, label: str) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Ungültiger {label}") from exc
    if parsed.tzinfo is None:
        raise HTTPException(status_code=400, detail=f"{label} benötigt eine Zeitzone")
    return parsed.astimezone(timezone.utc)


@api_router.get("/companies/{company_id}/financial-terms")
async def get_financial_terms(
    company_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    company = await _visible_company(company_id, user, access)
    terms = company_terms(company, default_currency=access.context.default_currency)
    result = {"paymentTermsDays": terms.payment_terms_days, "creditCurrency": terms.credit_currency}
    if user["role"] == "admin":
        result.update({
            "creditLimitMinor": terms.credit_limit_minor,
            "palletApprovalLimit": float(terms.pallet_approval_limit), "source": terms.source,
        })
    return result


@api_router.put("/companies/{company_id}/financial-terms")
async def update_financial_terms(
    company_id: str,
    body: CompanyFinancialTermsIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    company = await access.companies.find_one({"id": company_id})
    if not company:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    payload = body.model_dump()
    payload["financialTermsUpdatedAt"] = datetime.now(timezone.utc).isoformat()
    payload["financialTermsUpdatedBy"] = user["id"]
    await access.companies.update_one({"id": company_id}, {"$set": payload})
    await tenant_audit(access, user, "company.financial_terms.update", company_id, {
        "paymentTermsDays": body.paymentTermsDays,
        "creditLimitMinor": body.creditLimitMinor,
        "creditCurrency": body.creditCurrency,
        "palletApprovalLimit": body.palletApprovalLimit,
    })
    return {"ok": True, **body.model_dump()}


@api_router.get("/companies/{company_id}/credit")
async def get_company_credit(
    company_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    company = await _visible_company(company_id, user, access)
    terms = company_terms(company, default_currency=access.context.default_currency)
    summary = await receivables_summary(access, company_ids=[company_id], currency=terms.credit_currency)
    result = {
        "currency": terms.credit_currency,
        "paymentTermsDays": terms.payment_terms_days,
        "openMinor": summary["openMinor"], "dueMinor": summary["dueMinor"],
        "overdueMinor": summary["overdueMinor"], "invoices": summary["invoices"],
    }
    if user["role"] == "admin":
        result.update(await available_credit(access, company=company, currency=terms.credit_currency))
        result["palletApprovalLimit"] = float(terms.pallet_approval_limit)
        result["aging"] = summary["aging"]
    return result


@api_router.get("/financial/receivables")
async def list_receivables(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    currency: Annotated[str, Query(pattern="^(EUR|CHF)$")] = "EUR",
):
    ids = await visible_company_ids(user, access)
    return await receivables_summary(access, company_ids=ids, currency=currency)


@api_router.get("/financial/approvals")
async def list_financial_approvals(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    ids = await visible_company_ids(user, access)
    rows = await access.orders.find({
        "companyId": {"$in": ids}, "financialApproval.required": True,
        "financialApproval.status": "required",
    }).sort("createdAt", -1).to_list(500)
    return [{
        "id": row["id"], "companyId": row.get("companyId"), "currency": row.get("currency"),
        "netTotalMinor": row.get("netTotalMinor"),
        "approval": public_approval(row.get("financialApproval") or {}, admin=user["role"] == "admin"),
    } for row in rows]


@api_router.post("/financial/approvals/{order_id}/approve")
async def approve_financial_order(
    order_id: str, body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    order = await access.orders.find_one({"id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    result = await access.orders.update_one(
        {"id": order_id, "financialApproval.status": "required"},
        {"$set": {
            "financialApproval.status": "approved", "financialApproval.decidedBy": user["id"],
            "financialApproval.decidedAt": datetime.now(timezone.utc).isoformat(),
            "financialApproval.note": body.note or "", "status": "Neu",
        }},
    )
    if result.matched_count != 1 and (order.get("financialApproval") or {}).get("status") != "approved":
        raise HTTPException(status_code=409, detail="Freigabe ist nicht mehr offen")
    if result.matched_count == 1:
        await tenant_audit(access, user, "order.financial_approval.approve", order_id, {"note": body.note or ""})
    order = await access.orders.find_one({"id": order_id})
    invoice_id = order.get("invoiceId") if order else None
    if order and order.get("invoiceRequested") and not invoice_id:
        from .billing import create_invoice_record
        invoice = await create_invoice_record(access, order, user)
        invoice_id = invoice["id"]
    return {"ok": True, "status": "approved", "invoiceId": invoice_id}


@api_router.post("/financial/approvals/{order_id}/reject")
async def reject_financial_order(
    order_id: str, body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    result = await access.orders.update_one(
        {"id": order_id, "financialApproval.status": "required"},
        {"$set": {
            "financialApproval.status": "rejected", "financialApproval.decidedBy": user["id"],
            "financialApproval.decidedAt": datetime.now(timezone.utc).isoformat(),
            "financialApproval.note": body.note or "", "status": "Storniert",
        }},
    )
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="Freigabe ist nicht mehr offen")
    await tenant_audit(access, user, "order.financial_approval.reject", order_id, {"note": body.note or ""})
    return {"ok": True, "status": "rejected"}


@api_router.post("/commission/agreements", status_code=201)
async def create_commission_agreement(
    body: CommissionAgreementIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    _validate_agreement_period(body)
    membership = await access.tenant_memberships.find_one({"userId": body.salesRepId, "role": "sales", "active": True})
    if not membership:
        raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    if body.companyId and not await access.companies.find_one({"id": body.companyId}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if body.productId and not await access.products.find_one({"id": body.productId}):
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    row = {"id": "cagr_" + secrets.token_hex(8), **body.model_dump(mode="json"),
           "createdAt": datetime.now(timezone.utc).isoformat(), "createdBy": user["id"]}
    await access.commission_agreements.insert_one(row)
    await tenant_audit(access, user, "commission.agreement.create", row["id"], {
        "salesRepId": body.salesRepId, "currency": body.currency,
    })
    return strip_id(row)


@api_router.put("/commission/agreements/{agreement_id}")
async def update_commission_agreement(
    agreement_id: str,
    body: CommissionAgreementIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    _validate_agreement_period(body)
    existing = await access.commission_agreements.find_one({"id": agreement_id})
    if not existing:
        raise HTTPException(status_code=404, detail="Provisionsvereinbarung nicht gefunden")
    membership = await access.tenant_memberships.find_one({
        "userId": body.salesRepId, "role": "sales", "active": True,
    })
    if not membership:
        raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    if body.companyId and not await access.companies.find_one({"id": body.companyId}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if body.productId and not await access.products.find_one({"id": body.productId}):
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    payload = {
        **body.model_dump(mode="json"),
        "updatedAt": datetime.now(timezone.utc).isoformat(),
        "updatedBy": user["id"],
    }
    result = await access.commission_agreements.update_one({"id": agreement_id}, {"$set": payload})
    if result.matched_count != 1:
        raise HTTPException(status_code=409, detail="Provisionsvereinbarung wurde zwischenzeitlich geändert")
    await tenant_audit(access, user, "commission.agreement.update", agreement_id, {
        "salesRepId": body.salesRepId, "currency": body.currency, "active": body.active,
    })
    return strip_id(await access.commission_agreements.find_one({"id": agreement_id}))


@api_router.get("/commission/agreements")
async def list_commission_agreements(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    query = {} if user["role"] == "admin" else {"salesRepId": user["id"]}
    rows = await access.commission_agreements.find(query).sort("createdAt", -1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.get("/commission/ledger")
async def list_commission_ledger(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    sales_rep_id: Optional[str] = None,
    status: Optional[str] = Query(default=None, pattern="^(PENDING|EARNED|ADJUSTED|REVERSED)$"),
    currency: Optional[str] = Query(default=None, pattern="^(EUR|CHF)$"),
    start: Optional[str] = None,
    end: Optional[str] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 500,
):
    if user["role"] == "sales":
        if sales_rep_id and sales_rep_id != user["id"]:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        sales_rep_id = user["id"]
    start_at, end_at = _parse_utc_filter(start, "Startzeitpunkt"), _parse_utc_filter(end, "Endzeitpunkt")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=400, detail="Startzeitpunkt muss vor Endzeitpunkt liegen")
    query = {"salesRepId": sales_rep_id} if sales_rep_id else {}
    if status:
        query["status"] = status
    if currency:
        query["currency"] = currency
    if start_at or end_at:
        query["createdAt"] = {
            **({"$gte": start_at} if start_at else {}),
            **({"$lt": end_at} if end_at else {}),
        }
    rows = await bounded_list(access.commission_entries.find(query).sort("createdAt", -1), limit=limit)
    return [strip_id(row) for row in rows]


@api_router.post("/commission/adjustments", status_code=201)
async def create_commission_adjustment(
    body: CommissionAdjustmentIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    _require_admin_runtime(user)
    if not idempotency_key or len(idempotency_key) > 128:
        raise HTTPException(status_code=400, detail="Idempotency-Key ist erforderlich")
    try:
        row = await append_commission_adjustment(
            access,
            source_entry_id=body.sourceEntryId,
            event_key=f"commission:adjustment:{idempotency_key}",
            amount_minor=-body.amountMinor,
            reason=body.reason,
            actor_id=user["id"],
            kind=body.kind,
            reference=body.reference,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await tenant_audit(access, user, "commission.adjustment", row["id"], {
        "sourceEntryId": body.sourceEntryId,
        "amountMinor": body.amountMinor,
        "currency": row["currency"],
        "kind": body.kind,
        "reference": body.reference,
    })
    return strip_id(row)


@api_router.post("/commission/settlements", status_code=201)
async def create_commission_settlement(
    body: CommissionSettlementIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    _require_admin_runtime(user)
    membership = await access.tenant_memberships.find_one({"userId": body.salesRepId, "role": "sales", "active": True})
    if not membership:
        raise HTTPException(status_code=400, detail="Vertrieb ist für diesen Tenant nicht verfügbar")
    try:
        row = await create_settlement(
            access, sales_rep_id=body.salesRepId, currency=body.currency,
            actor_id=user["id"], idempotency_key=idempotency_key or "",
            period_start=body.periodStart, period_end=body.periodEnd,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await tenant_audit(access, user, "commission.settlement.create", row["id"], {
        "salesRepId": body.salesRepId, "currency": body.currency,
    })
    return strip_id(row)


@api_router.get("/commission/settlements")
async def list_commission_settlements(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    start: Optional[str] = None,
    end: Optional[str] = None,
):
    start_at, end_at = _parse_utc_filter(start, "Startzeitpunkt"), _parse_utc_filter(end, "Endzeitpunkt")
    if start_at and end_at and start_at >= end_at:
        raise HTTPException(status_code=400, detail="Startzeitpunkt muss vor Endzeitpunkt liegen")
    query = {} if user["role"] == "admin" else {"salesRepId": user["id"]}
    if start_at or end_at:
        query["periodStart"] = {
            **({"$gte": start_at} if start_at else {}),
            **({"$lt": end_at} if end_at else {}),
        }
    rows = await access.commission_settlements.find(query).sort("createdAt", -1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.post("/commission/settlements/{settlement_id}/payout")
async def pay_commission_settlement(
    settlement_id: str, body: CommissionPayoutIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    try:
        row = await mark_settlement_paid(
            access, settlement_id=settlement_id, reference=body.reference.strip(), actor_id=user["id"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await tenant_audit(access, user, "commission.settlement.payout", settlement_id, {"reference": body.reference})
    return strip_id(row)


@api_router.get("/accounting/syncs")
async def list_accounting_syncs(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    rows = await access.accounting_syncs.find({}).sort("updatedAt", -1).to_list(1000)
    return [strip_id(row) for row in rows]


@api_router.post("/accounting/syncs/{sync_id}/retry")
async def retry_accounting_sync(
    sync_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    _require_admin_runtime(user)
    row = await access.accounting_syncs.find_one({"id": sync_id})
    if not row:
        raise HTTPException(status_code=404, detail="Accounting-Synchronisierung nicht gefunden")
    try:
        retried = await request_accounting_retry(access, sync_id=sync_id, actor_id=user["id"])
    except AccountingProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await tenant_audit(access, user, "accounting.sync.retry", sync_id, {
        "resourceType": row.get("resourceType"), "resourceId": row.get("resourceId"),
    })
    return strip_id(retried)
