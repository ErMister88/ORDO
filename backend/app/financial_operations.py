"""Server-authoritative B2B terms, exposure, pallet and receivable calculations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping, Sequence

from fastapi import HTTPException

from .money import amount_minor, currency_code, require_minor
from .tenant_access import TenantBusinessAccess


DEFAULT_CREDIT_LIMIT_MINOR = 1_000_000
DEFAULT_PALLET_APPROVAL_LIMIT = Decimal("1")
COMMITTED_ORDER_STATUSES = frozenset({
    "Neu", "Freigabe nötig", "Bestätigt", "Kommissioniert", "Versendet", "Abgeschlossen",
})
NON_EXPOSURE_INVOICE_STATUSES = frozenset({"Bezahlt", "Storniert"})
MAX_EXPOSURE_SCAN_DOCUMENTS = 10_000
AGING_BUCKETS = (
    ("not_yet_due", None, 0),
    ("overdue_1_7", 1, 7),
    ("overdue_8_30", 8, 30),
    ("overdue_31_60", 31, 60),
    ("overdue_over_60", 61, None),
)


class FinancialConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class CompanyTerms:
    payment_terms_days: int | None
    credit_limit_minor: int
    credit_currency: str
    pallet_approval_limit: Decimal
    source: str

    def snapshot(self) -> dict[str, Any]:
        return {
            "paymentTermsDays": self.payment_terms_days,
            "creditLimitMinor": self.credit_limit_minor,
            "creditCurrency": self.credit_currency,
            "palletApprovalLimit": float(self.pallet_approval_limit),
            "source": self.source,
        }


def company_terms(company: Mapping[str, Any], *, default_currency: str) -> CompanyTerms:
    currency = currency_code(company.get("creditCurrency") or default_currency)
    raw_limit = company.get("creditLimitMinor", DEFAULT_CREDIT_LIMIT_MINOR)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 0:
        raise FinancialConfigurationError("Customer credit limit is invalid")
    payment_days = company.get("paymentTermsDays")
    if payment_days is not None and (
        isinstance(payment_days, bool) or not isinstance(payment_days, int) or not 0 <= payment_days <= 3650
    ):
        raise FinancialConfigurationError("Customer payment terms are invalid")
    try:
        pallet_limit = Decimal(str(company.get("palletApprovalLimit", DEFAULT_PALLET_APPROVAL_LIMIT)))
    except (InvalidOperation, ValueError) as exc:
        raise FinancialConfigurationError("Customer pallet limit is invalid") from exc
    if not pallet_limit.is_finite() or pallet_limit < 0:
        raise FinancialConfigurationError("Customer pallet limit is invalid")
    explicit = any(key in company for key in ("creditLimitMinor", "creditCurrency", "palletApprovalLimit", "paymentTermsDays"))
    return CompanyTerms(payment_days, raw_limit, currency, pallet_limit, "customer" if explicit else "tenant_default")


def receivable_due_status(invoice: Mapping[str, Any], *, today: date | None = None) -> str:
    due = invoice.get("dueDate")
    if not isinstance(due, str) or not due:
        return "OPEN"
    try:
        due_date = date.fromisoformat(due)
    except ValueError as exc:
        raise FinancialConfigurationError("Invoice due date is invalid") from exc
    current = today or datetime.now(timezone.utc).date()
    if due_date < current:
        return "OVERDUE"
    if due_date == current:
        return "DUE"
    return "OPEN"


def receivable_status(invoice: Mapping[str, Any], *, today: date | None = None) -> str:
    stored = invoice.get("status")
    if stored == "Storniert":
        return "CANCELLED"
    currency = currency_code(invoice.get("currency") or "EUR")
    total = amount_minor(invoice, "amount", expected_currency=currency)
    paid = invoice.get("paidAmountMinor", 0)
    if isinstance(paid, bool) or not isinstance(paid, int) or paid < 0 or paid > total:
        raise FinancialConfigurationError("Invoice payment balance is invalid")
    if paid == total:
        return "PAID"
    if paid > 0:
        return "PARTIALLY_PAID"
    return receivable_due_status(invoice, today=today)


def _open_minor(invoice: Mapping[str, Any], currency: str) -> int:
    total = amount_minor(invoice, "amount", expected_currency=currency)
    paid = invoice.get("paidAmountMinor", 0)
    if isinstance(paid, bool) or not isinstance(paid, int) or not 0 <= paid <= total:
        raise FinancialConfigurationError("Invoice payment balance is invalid")
    return total - paid


async def receivables_summary(
    access: TenantBusinessAccess,
    *,
    company_ids: Sequence[str],
    currency: str,
    today: date | None = None,
) -> dict[str, Any]:
    code = currency_code(currency)
    current = today or datetime.now(timezone.utc).date()
    query = {
        "companyId": {"$in": list(company_ids)},
        "currency": code,
        "status": {"$nin": list(NON_EXPOSURE_INVOICE_STATUSES)},
    }
    if await access.invoices.count_documents(query, limit=MAX_EXPOSURE_SCAN_DOCUMENTS + 1) > MAX_EXPOSURE_SCAN_DOCUMENTS:
        raise FinancialConfigurationError("Receivable data set exceeds the safe calculation limit")
    rows = await access.invoices.find(query).to_list(MAX_EXPOSURE_SCAN_DOCUMENTS)
    totals = {"openMinor": 0, "dueMinor": 0, "overdueMinor": 0, "partialMinor": 0}
    aging = {bucket[0]: 0 for bucket in AGING_BUCKETS}
    public_rows: list[dict[str, Any]] = []
    for invoice in rows:
        state = receivable_status(invoice, today=current)
        outstanding = _open_minor(invoice, code)
        if outstanding <= 0 or state in {"PAID", "CANCELLED"}:
            continue
        totals["openMinor"] += outstanding
        due_state = receivable_due_status(invoice, today=current)
        if due_state == "DUE":
            totals["dueMinor"] += outstanding
        elif due_state == "OVERDUE":
            totals["overdueMinor"] += outstanding
        if state == "PARTIALLY_PAID":
            totals["partialMinor"] += outstanding
        due_value = invoice.get("dueDate")
        if isinstance(due_value, str):
            due_date = date.fromisoformat(due_value)
            days = (current - due_date).days
            if days <= 0:
                aging["not_yet_due"] += outstanding
            elif days <= 7:
                aging["overdue_1_7"] += outstanding
            elif days <= 30:
                aging["overdue_8_30"] += outstanding
            elif days <= 60:
                aging["overdue_31_60"] += outstanding
            else:
                aging["overdue_over_60"] += outstanding
        else:
            aging["not_yet_due"] += outstanding
        public_rows.append({
            "id": invoice.get("id"), "companyId": invoice.get("companyId"),
            "currency": code, "status": state, "dueDate": invoice.get("dueDate"),
            "amountMinor": amount_minor(invoice, "amount", expected_currency=code),
            "paidAmountMinor": invoice.get("paidAmountMinor", 0),
            "outstandingMinor": outstanding,
        })
    return {"currency": code, **totals, "aging": aging, "invoices": public_rows}


async def available_credit(
    access: TenantBusinessAccess,
    *,
    company: Mapping[str, Any],
    currency: str,
) -> dict[str, int | str]:
    terms = company_terms(company, default_currency=access.context.default_currency)
    code = currency_code(currency)
    if terms.credit_currency != code:
        raise FinancialConfigurationError("Customer credit currency does not match the order currency")
    receivables = await receivables_summary(access, company_ids=[str(company["id"])], currency=code)
    committed_query = {
        "companyId": company["id"], "currency": code,
        "status": {"$in": list(COMMITTED_ORDER_STATUSES)},
        "invoiceId": {"$exists": False},
    }
    if await access.orders.count_documents(committed_query, limit=MAX_EXPOSURE_SCAN_DOCUMENTS + 1) > MAX_EXPOSURE_SCAN_DOCUMENTS:
        raise FinancialConfigurationError("Committed order data set exceeds the safe calculation limit")
    committed = await access.orders.find(committed_query).to_list(MAX_EXPOSURE_SCAN_DOCUMENTS)
    reserved_minor = 0
    for order in committed:
        value = order.get("netTotalMinor")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise FinancialConfigurationError("Committed order exposure is invalid")
        reserved_minor += value
        require_minor(reserved_minor)
    used = int(receivables["openMinor"]) + reserved_minor
    return {
        "currency": code,
        "creditLimitMinor": terms.credit_limit_minor,
        "receivablesMinor": int(receivables["openMinor"]),
        "reservedMinor": reserved_minor,
        "availableMinor": max(0, terms.credit_limit_minor - used),
    }


def _product_kg_per_pallet(product: Mapping[str, Any]) -> Decimal | None:
    candidates = [product.get("kgPerPallet")]
    kg_per_case = product.get("kgPerCase")
    cases = product.get("casesPerPallet")
    if kg_per_case is not None and cases is not None:
        try:
            candidates.append(Decimal(str(kg_per_case)) * Decimal(str(cases)))
        except (InvalidOperation, ValueError):
            return None
    parsed_candidates: list[Decimal] = []
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            value = Decimal(str(candidate))
        except (InvalidOperation, ValueError):
            continue
        if value.is_finite() and value > 0:
            parsed_candidates.append(value)
    if not parsed_candidates or any(value != parsed_candidates[0] for value in parsed_candidates[1:]):
        return None
    return parsed_candidates[0]


def _positive_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed > 0 else None


def _quantity_per_pallet(product: Mapping[str, Any]) -> tuple[Decimal | None, str | None]:
    unit = str(product.get("unit") or "").strip().casefold()
    if unit in {"kg", "kilogram", "kilogramm"}:
        return _product_kg_per_pallet(product), "kg"
    cases = _positive_decimal(product.get("casesPerPallet"))
    if unit in {"case", "carton", "karton"} and cases:
        return cases, "case"
    units = _positive_decimal(product.get("unitsPerCase"))
    if unit in {"piece", "unit", "stück", "stueck"} and units and cases:
        return units * cases, "piece"
    return None, None


def pallet_exposure(items: Sequence[Mapping[str, Any]]) -> tuple[Decimal | None, list[dict[str, Any]]]:
    total = Decimal("0")
    snapshots: list[dict[str, Any]] = []
    complete = True
    for item in items:
        product = item["product"]
        kg_per_pallet = _product_kg_per_pallet(product)
        quantity_per_pallet, calculation_unit = _quantity_per_pallet(product)
        try:
            quantity = Decimal(str(item["qty"]))
        except (InvalidOperation, ValueError) as exc:
            raise FinancialConfigurationError("Order quantity is invalid") from exc
        configured = quantity_per_pallet is not None
        pallet_value = quantity / quantity_per_pallet if quantity_per_pallet else None
        snapshots.append({
            "productId": product.get("id"), "qty": float(quantity),
            "kgPerPallet": float(kg_per_pallet) if kg_per_pallet else None,
            "quantityPerPallet": float(quantity_per_pallet) if quantity_per_pallet else None,
            "calculationUnit": calculation_unit,
            "palletEquivalent": float(pallet_value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)) if pallet_value is not None else None,
            "configured": configured,
        })
        if pallet_value is None:
            complete = False
        else:
            total += pallet_value
    return (total if complete else None), snapshots


async def evaluate_b2b_order(
    access: TenantBusinessAccess,
    *,
    company: Mapping[str, Any],
    item_products: Sequence[Mapping[str, Any]],
    order_total_minor: int,
    currency: str,
    payment_terms_days: int | None = None,
    order_already_in_exposure: bool = False,
) -> dict[str, Any]:
    terms = company_terms(company, default_currency=access.context.default_currency)
    code = currency_code(currency)
    reasons: list[dict[str, str]] = []
    if company.get("active") is False or company.get("status") == "Gesperrt":
        reasons.append({"code": "CUSTOMER_BLOCKED", "message": "Kunde ist gesperrt"})
    try:
        credit = await available_credit(access, company=company, currency=code)
        exposure_exceeded = (
            int(credit["receivablesMinor"]) + int(credit["reservedMinor"])
            > int(credit["creditLimitMinor"])
        ) if order_already_in_exposure else order_total_minor > int(credit["availableMinor"])
        if exposure_exceeded:
            reasons.append({"code": "AVAILABLE_CREDIT_EXCEEDED", "message": "Verfügbare Kreditlinie reicht nicht aus"})
    except FinancialConfigurationError:
        credit = None
        reasons.append({"code": "CREDIT_CONFIGURATION_INVALID", "message": "Kreditkonfiguration muss geprüft werden"})
    effective_payment_terms = terms.payment_terms_days if payment_terms_days is None else payment_terms_days
    if effective_payment_terms is None:
        reasons.append({"code": "PAYMENT_TERMS_MISSING", "message": "Zahlungsziel muss festgelegt werden"})
    pallets, pallet_lines = pallet_exposure(item_products)
    if pallets is None:
        reasons.append({"code": "PALLET_CONFIGURATION_MISSING", "message": "Palettenkonfiguration muss geprüft werden"})
    elif pallets > terms.pallet_approval_limit:
        reasons.append({"code": "PALLET_LIMIT_EXCEEDED", "message": "Palettenlimit überschritten"})
    receivables = await receivables_summary(access, company_ids=[str(company["id"])], currency=code)
    if int(receivables["overdueMinor"]) > 0:
        reasons.append({"code": "OVERDUE_RECEIVABLES", "message": "Überfällige Forderungen vorhanden"})
    return {
        "required": bool(reasons), "status": "required" if reasons else "not_required",
        "reasons": reasons, "termsSnapshot": {**terms.snapshot(), "paymentTermsDays": effective_payment_terms},
        "palletExposure": float(pallets) if pallets is not None else None,
        "palletLines": pallet_lines,
        "creditSnapshot": credit,
        "evaluatedAt": datetime.now(timezone.utc).isoformat(),
    }


def public_approval(
    evaluation: Mapping[str, Any], *, admin: bool, customer: bool = False,
) -> dict[str, Any]:
    reasons = list(evaluation.get("reasons") or [])
    if customer and reasons:
        reasons = [{"code": "ORDER_REVIEW_REQUIRED", "message": "Bestellung wird geprüft"}]
    result = {
        "required": bool(evaluation.get("required")),
        "status": evaluation.get("status"),
        "reasons": reasons,
        "evaluatedAt": evaluation.get("evaluatedAt"),
    }
    if not customer:
        result["palletExposure"] = evaluation.get("palletExposure")
    if admin:
        result["termsSnapshot"] = evaluation.get("termsSnapshot")
        result["creditSnapshot"] = evaluation.get("creditSnapshot")
        result["palletLines"] = evaluation.get("palletLines")
    return result


def validate_b2b_payment_method(method: str) -> None:
    if method not in {"bank_transfer", "cash"}:
        raise HTTPException(status_code=400, detail="B2B-Bestellungen unterstützen ausschließlich Rechnung oder Barzahlung")
