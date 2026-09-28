"""Append-only commission agreements, ledger events and locked settlements."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import secrets
from typing import Any, Mapping, Sequence

from pymongo.errors import DuplicateKeyError

from .money import currency_code, require_minor
from .tenant_access import TenantBusinessAccess


class CommissionAmbiguous(RuntimeError):
    pass


async def _active_sales_membership(
    access: TenantBusinessAccess, sales_rep_id: str,
) -> bool:
    return bool(await access.tenant_memberships.find_one({
        "userId": sales_rep_id, "role": "sales", "active": True,
    }))


def _active_at(agreement: Mapping[str, Any], at: datetime) -> bool:
    def parse(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        return None
    start, end = parse(agreement.get("validFrom")), parse(agreement.get("validUntil"))
    return agreement.get("active", True) and (start is None or start <= at) and (end is None or at <= end)


async def resolve_agreement(
    access: TenantBusinessAccess,
    *,
    sales_rep_id: str,
    company_id: str | None,
    product_id: str | None,
    currency: str,
    at: datetime,
) -> Mapping[str, Any] | None:
    code = currency_code(currency)
    rows = await access.commission_agreements.find({
        "salesRepId": sales_rep_id, "currency": code, "active": True,
        "$and": [
            {"$or": [{"companyId": None}, {"companyId": {"$exists": False}}, {"companyId": company_id}]},
            {"$or": [{"productId": None}, {"productId": {"$exists": False}}, {"productId": product_id}]},
        ],
    }).to_list(100)
    matches = [row for row in rows if _active_at(row, at)]
    if len(matches) > 1:
        raise CommissionAmbiguous("Multiple commission agreements match; no priority rule is configured")
    return matches[0] if matches else None


async def create_pending_commission(
    access: TenantBusinessAccess,
    *,
    order: Mapping[str, Any],
) -> list[dict[str, Any]]:
    attribution = order.get("salesAttribution") or {}
    sales_rep_id = attribution.get("salesRepId")
    if not isinstance(sales_rep_id, str) or not sales_rep_id:
        return []
    if not await _active_sales_membership(access, sales_rep_id):
        return []
    currency = currency_code(order.get("currency") or access.context.default_currency)
    now = datetime.now(timezone.utc)
    created: list[dict[str, Any]] = []
    for line_index, item in enumerate(order.get("items") or []):
        product_id = item.get("productId")
        agreement = await resolve_agreement(
            access, sales_rep_id=sales_rep_id, company_id=order.get("companyId"),
            product_id=product_id, currency=currency, at=now,
        )
        if agreement is None:
            continue
        if agreement.get("commissionType") != "PER_KG":
            raise CommissionAmbiguous("Unsupported commission agreement type")
        rate_minor = agreement.get("rateMinor")
        if isinstance(rate_minor, bool) or not isinstance(rate_minor, int) or rate_minor <= 0:
            raise CommissionAmbiguous("Commission agreement rate is invalid")
        if str(item.get("unit") or "").strip().casefold() != "kg":
            raise CommissionAmbiguous("PER_KG commission requires an immutable kilogram quantity snapshot")
        try:
            quantity = Decimal(str(item.get("qty")))
        except (InvalidOperation, ValueError) as exc:
            raise CommissionAmbiguous("Commission quantity is invalid") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise CommissionAmbiguous("Commission quantity is invalid")
        amount_minor = int((quantity * Decimal(rate_minor)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        require_minor(amount_minor)
        event_key = f"commission:order:{order['id']}:pending:{line_index}:{agreement['id']}:{product_id}"
        row = {
            "id": "com_" + secrets.token_hex(10), "eventKey": event_key,
            "status": "PENDING", "salesRepId": sales_rep_id,
            "companyId": order.get("companyId"), "orderId": order["id"],
            "productId": product_id, "lineIndex": line_index, "currency": currency,
            "amountMinor": amount_minor, "quantity": float(quantity),
            "agreementSnapshot": {
                "agreementId": agreement["id"], "commissionType": "PER_KG",
                "rateMinor": rate_minor, "currency": currency,
                "companyId": agreement.get("companyId"), "productId": agreement.get("productId"),
            },
            "salesAttributionSnapshot": dict(attribution),
            "createdAt": now,
        }
        try:
            await access.commission_entries.insert_one(row)
            created.append(row)
        except DuplicateKeyError:
            existing = await access.commission_entries.find_one({"eventKey": event_key})
            if existing:
                created.append(existing)
            else:
                raise
    return created


async def validate_commission_for_order(access: TenantBusinessAccess, order: Mapping[str, Any]) -> None:
    """Fail before order confirmation when agreement resolution is ambiguous."""
    attribution = order.get("salesAttribution") or {}
    sales_rep_id = attribution.get("salesRepId")
    if not isinstance(sales_rep_id, str) or not sales_rep_id:
        return
    if not await _active_sales_membership(access, sales_rep_id):
        return
    currency = currency_code(order.get("currency") or access.context.default_currency)
    now = datetime.now(timezone.utc)
    for item in order.get("items") or []:
        agreement = await resolve_agreement(
            access, sales_rep_id=sales_rep_id, company_id=order.get("companyId"),
            product_id=item.get("productId"), currency=currency, at=now,
        )
        if agreement is None:
            continue
        if agreement.get("commissionType") != "PER_KG":
            raise CommissionAmbiguous("Unsupported commission agreement type")
        rate_minor = agreement.get("rateMinor")
        if isinstance(rate_minor, bool) or not isinstance(rate_minor, int) or rate_minor <= 0:
            raise CommissionAmbiguous("Commission agreement rate is invalid")
        if str(item.get("unit") or "").strip().casefold() != "kg":
            raise CommissionAmbiguous("PER_KG commission requires an immutable kilogram quantity snapshot")
        try:
            quantity = Decimal(str(item.get("qty")))
        except (InvalidOperation, ValueError) as exc:
            raise CommissionAmbiguous("Commission quantity is invalid") from exc
        if not quantity.is_finite() or quantity <= 0:
            raise CommissionAmbiguous("Commission quantity is invalid")


async def earn_commission_for_payment(
    access: TenantBusinessAccess,
    *,
    order_id: str,
    invoice_id: str,
    payment_id: str,
    payment_amount_minor: int,
    paid_total_minor: int,
    document_total_minor: int,
) -> list[dict[str, Any]]:
    if (
        document_total_minor <= 0
        or payment_amount_minor <= 0
        or not payment_amount_minor <= paid_total_minor <= document_total_minor
    ):
        raise ValueError("Payment allocation is invalid")
    pending = await access.commission_entries.find({"orderId": order_id, "status": "PENDING"}).to_list(1000)
    created: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    for source in pending:
        previous_paid = paid_total_minor - payment_amount_minor
        target_after = int((
            Decimal(source["amountMinor"]) * Decimal(paid_total_minor) / Decimal(document_total_minor)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        target_before = int((
            Decimal(source["amountMinor"]) * Decimal(previous_paid) / Decimal(document_total_minor)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        # Derive this event's share only from its immutable payment interval.
        # Concurrent payment handlers therefore cannot over-allocate by reading
        # an incomplete set of earlier commission rows.
        delta = target_after - target_before
        if delta == 0:
            continue
        status = "EARNED" if delta > 0 else "ADJUSTED"
        event_key = f"commission:payment:{payment_id}:{source['id']}"
        row = {
            "id": "com_" + secrets.token_hex(10), "eventKey": event_key,
            "status": status, "sourceEntryId": source["id"],
            "salesRepId": source["salesRepId"], "companyId": source.get("companyId"),
            "orderId": order_id, "productId": source.get("productId"),
            "invoiceId": invoice_id, "paymentId": payment_id, "currency": source["currency"],
            "amountMinor": delta, "agreementSnapshot": source.get("agreementSnapshot"),
            "salesAttributionSnapshot": source.get("salesAttributionSnapshot"),
            "earnedAt": now, "createdAt": now,
        }
        source_quantity = source.get("quantity")
        if isinstance(source_quantity, (int, float)) and not isinstance(source_quantity, bool):
            quantity_delta = (
                Decimal(str(source_quantity)) * Decimal(payment_amount_minor) / Decimal(document_total_minor)
            ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
            row["quantity"] = float(quantity_delta)
        try:
            await access.commission_entries.insert_one(row)
            created.append(row)
        except DuplicateKeyError:
            continue
    return created


async def append_commission_adjustment(
    access: TenantBusinessAccess,
    *,
    source_entry_id: str,
    event_key: str,
    amount_minor: int,
    reason: str,
    actor_id: str,
    kind: str = "adjustment",
    reference: str | None = None,
) -> dict[str, Any]:
    source = await access.commission_entries.find_one({"id": source_entry_id})
    if not source or source.get("status") != "EARNED":
        raise ValueError("Commission source is not adjustable")
    if amount_minor >= 0:
        raise ValueError("Commission adjustment must reduce earned commission")
    prior = await access.commission_entries.find({
        "sourceEntryId": source_entry_id,
        "status": {"$in": ["ADJUSTED", "REVERSED"]},
    }).to_list(10_000)
    remaining = source["amountMinor"] + sum(row["amountMinor"] for row in prior)
    if remaining <= 0 or -amount_minor > remaining:
        raise ValueError("Commission adjustment exceeds remaining earned commission")
    row = {
        "id": "com_" + secrets.token_hex(10), "eventKey": event_key,
        "status": "REVERSED" if -amount_minor == remaining else "ADJUSTED",
        "sourceEntryId": source_entry_id, "salesRepId": source["salesRepId"],
        "companyId": source.get("companyId"), "orderId": source.get("orderId"),
        "productId": source.get("productId"), "currency": source["currency"],
        "amountMinor": amount_minor, "reason": reason, "createdBy": actor_id,
        "adjustmentKind": kind, "adjustmentReference": reference,
        "agreementSnapshot": source.get("agreementSnapshot"),
        "salesAttributionSnapshot": source.get("salesAttributionSnapshot"),
        "createdAt": datetime.now(timezone.utc),
    }
    source_quantity = source.get("quantity")
    if isinstance(source_quantity, (int, float)) and not isinstance(source_quantity, bool):
        quantity_delta = (
            Decimal(str(source_quantity)) * Decimal(amount_minor) / Decimal(source["amountMinor"])
        ).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        row["quantity"] = float(quantity_delta)
    try:
        await access.commission_entries.insert_one(row)
        return row
    except DuplicateKeyError:
        existing = await access.commission_entries.find_one({"eventKey": event_key})
        if not existing:
            raise
        return existing


async def create_settlement(
    access: TenantBusinessAccess,
    *,
    sales_rep_id: str,
    currency: str,
    actor_id: str,
    idempotency_key: str,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    if not idempotency_key:
        raise ValueError("Settlement idempotency key is required")
    code = currency_code(currency)
    if period_start.tzinfo is None or period_end.tzinfo is None or period_start >= period_end:
        raise ValueError("Settlement period is invalid")
    period_start = period_start.astimezone(timezone.utc)
    period_end = period_end.astimezone(timezone.utc)

    def same_request(row: Mapping[str, Any]) -> bool:
        return (
            row.get("salesRepId") == sales_rep_id
            and row.get("currency") == code
            and row.get("periodStart") == period_start
            and row.get("periodEnd") == period_end
        )

    existing = await access.commission_settlements.find_one({"idempotencyKey": idempotency_key})
    if existing:
        if not same_request(existing):
            raise ValueError("Settlement idempotency key was reused with different input")
        return existing
    lock_key = f"{sales_rep_id}:{code}"
    active = await access.commission_settlements.find_one({"settlementLockKey": lock_key})
    if active:
        if active.get("periodStart") == period_start and active.get("periodEnd") == period_end:
            return active
        raise ValueError("Another settlement for this salesperson and currency is still locked")
    events = await access.commission_entries.find({
        "salesRepId": sales_rep_id, "currency": code,
        "status": {"$in": ["EARNED", "ADJUSTED", "REVERSED"]},
        "createdAt": {"$gte": period_start, "$lt": period_end},
    }).sort("createdAt", 1).to_list(10_000)
    prior = await access.commission_settlements.find({
        "salesRepId": sales_rep_id, "currency": code, "status": {"$in": ["LOCKED", "PAID_OUT"]},
    }).to_list(10_000)
    settled_ids = {entry_id for settlement in prior for entry_id in settlement.get("entryIds", [])}
    selected = [row for row in events if row["id"] not in settled_ids]
    total = sum(row["amountMinor"] for row in selected)
    if not selected or total <= 0:
        raise ValueError("No positive unsettled commission is available")
    row = {
        "id": "sett_" + secrets.token_hex(10), "idempotencyKey": idempotency_key,
        "settlementLockKey": lock_key,
        "salesRepId": sales_rep_id, "currency": code, "status": "LOCKED",
        "periodStart": period_start, "periodEnd": period_end,
        "entryIds": [entry["id"] for entry in selected], "amountMinor": total,
        "createdBy": actor_id, "createdAt": datetime.now(timezone.utc),
    }
    try:
        await access.commission_settlements.insert_one(row)
        return row
    except DuplicateKeyError:
        existing = await access.commission_settlements.find_one({"idempotencyKey": idempotency_key})
        if existing:
            if not same_request(existing):
                raise ValueError("Settlement idempotency key was reused with different input")
            return existing
        active = await access.commission_settlements.find_one({"settlementLockKey": lock_key})
        if active and same_request(active):
            return active
        if active:
            raise ValueError("Another settlement for this salesperson and currency is still locked")
        raise


async def mark_settlement_paid(
    access: TenantBusinessAccess,
    *,
    settlement_id: str,
    reference: str,
    actor_id: str,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    result = await access.commission_settlements.update_one(
        {"id": settlement_id, "status": "LOCKED"},
        {"$set": {"status": "PAID_OUT", "payoutReference": reference, "paidAt": now, "paidBy": actor_id}, "$unset": {"settlementLockKey": ""}},
    )
    if result.matched_count != 1:
        existing = await access.commission_settlements.find_one({"id": settlement_id})
        if existing and existing.get("status") == "PAID_OUT" and existing.get("payoutReference") == reference:
            return existing
        raise ValueError("Settlement cannot be paid out")
    return await access.commission_settlements.find_one({"id": settlement_id})
