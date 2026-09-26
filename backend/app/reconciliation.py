"""Read-only commercial consistency checks with tenant-scoped result storage."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import secrets
from typing import Any, Awaitable, Callable

from .tenant_access import TenantBusinessAccess


MAX_DOCUMENTS_PER_COLLECTION = 5000
MAX_ISSUES_PER_RUN = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _issue_key(code: str, resource_type: str, resource_id: str) -> str:
    raw = f"{code}:{resource_type}:{resource_id}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def _issue(
    code: str,
    category: str,
    resource_type: str,
    resource_id: str,
    message: str,
) -> dict[str, Any]:
    return {
        "issueKey": _issue_key(code, resource_type, resource_id),
        "code": code,
        "category": category,
        "resourceType": resource_type,
        "resourceId": resource_id,
        "message": message,
        "reviewRequired": category in {"ERROR", "REQUIRES_REVIEW"},
    }


async def run_reconciliation(
    access: TenantBusinessAccess,
    *,
    actor_id: str,
    run_id: str | None = None,
    checkpoint: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Detect inconsistencies without mutating any commercial document."""

    started_at = _now()
    run_id = run_id or "rec_" + secrets.token_hex(10)
    existing = await access.reconciliation_runs.find_one({"id": run_id})
    if existing:
        return existing
    issues: list[dict[str, Any]] = []

    orders = await access.orders.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()
    invoices = await access.invoices.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()
    shop_orders = await access.shop_orders.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()
    machine_requests = await access.machine_requests.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()
    operations = await access.idempotency_records.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()
    payment_events = await access.payment_provider_events.find({}).to_list(MAX_DOCUMENTS_PER_COLLECTION)
    if checkpoint:
        await checkpoint()

    order_by_id = {row.get("id"): row for row in orders if isinstance(row.get("id"), str)}
    invoice_by_id = {row.get("id"): row for row in invoices if isinstance(row.get("id"), str)}
    resources = {
        "invoice": invoice_by_id,
        "shop_order": {row.get("id"): row for row in shop_orders if isinstance(row.get("id"), str)},
        "machine_request": {row.get("id"): row for row in machine_requests if isinstance(row.get("id"), str)},
    }

    for invoice in invoices:
        invoice_id = str(invoice.get("id") or "unknown")
        order_id = invoice.get("orderId")
        if isinstance(order_id, str) and order_id not in order_by_id:
            issues.append(_issue(
                "invoice_order_missing", "ERROR", "invoice", invoice_id,
                "Die Rechnung verweist auf keine vorhandene Bestellung.",
            ))
        records = invoice.get("paymentRecords") or []
        recorded_total = sum(
            row.get("amountMinor", 0)
            for row in records
            if isinstance(row, dict) and isinstance(row.get("amountMinor", 0), int)
        )
        paid_total = invoice.get("paidAmountMinor", 0)
        if isinstance(paid_total, int) and recorded_total != paid_total:
            issues.append(_issue(
                "invoice_payment_sum_mismatch", "REQUIRES_REVIEW", "invoice", invoice_id,
                "Zahlungsstand und gespeicherte Zahlungsbuchungen stimmen nicht überein.",
            ))
        amount_total = invoice.get("amountMinor")
        if invoice.get("status") == "Bezahlt" and isinstance(amount_total, int) and paid_total != amount_total:
            issues.append(_issue(
                "invoice_paid_amount_mismatch", "REQUIRES_REVIEW", "invoice", invoice_id,
                "Eine bezahlte Rechnung besitzt keinen passenden vollständigen Zahlungsstand.",
            ))

    for order in orders:
        order_id = str(order.get("id") or "unknown")
        invoice_id = order.get("invoiceId")
        if isinstance(invoice_id, str):
            invoice = invoice_by_id.get(invoice_id)
            if not invoice or invoice.get("orderId") != order_id:
                issues.append(_issue(
                    "order_invoice_reference_invalid", "ERROR", "order", order_id,
                    "Bestellung und Rechnung sind nicht konsistent miteinander verknüpft.",
                ))

    for operation in operations:
        if operation.get("status") != "completed":
            continue
        refs = operation.get("resourceRefs") or {}
        operation_id = str(operation.get("id") or "unknown")
        for field, collection in (
            ("orderId", order_by_id),
            ("invoiceId", invoice_by_id),
            ("shopOrderId", resources["shop_order"]),
            ("machineRequestId", resources["machine_request"]),
        ):
            resource_id = refs.get(field) if isinstance(refs, dict) else None
            if isinstance(resource_id, str) and resource_id not in collection:
                issues.append(_issue(
                    "idempotency_result_missing", "ERROR", "idempotency_operation", operation_id,
                    "Ein abgeschlossener Vorgang verweist auf ein fehlendes Ergebnis.",
                ))

    successful_events = {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
    }
    for event in payment_events:
        if event.get("status") != "processed" or event.get("eventType") not in successful_events:
            continue
        event_id = str(event.get("id") or "unknown")
        resource_type = event.get("resourceType")
        resource_id = event.get("resourceId")
        resource = resources.get(resource_type, {}).get(resource_id)
        if not resource:
            issues.append(_issue(
                "payment_resource_missing", "ERROR", "payment_event", event_id,
                "Ein verarbeitetes Zahlungsereignis verweist auf keine vorhandene Ressource.",
            ))
        elif resource.get("paymentStatus") != "Bezahlt" and resource.get("status") != "Bezahlt":
            issues.append(_issue(
                "payment_state_mismatch", "REQUIRES_REVIEW", "payment_event", event_id,
                "Zahlungsereignis und wirtschaftlicher Zahlungsstatus stimmen nicht überein.",
            ))

    scan_truncated = any(
        len(rows) >= MAX_DOCUMENTS_PER_COLLECTION
        for rows in (orders, invoices, shop_orders, machine_requests, operations, payment_events)
    )
    if scan_truncated:
        issues.append(_issue(
            "scan_limit_reached", "WARNING", "tenant", access.context.tenant_id,
            "Die Prüfung erreichte ein Abfragelimit und muss in Teilbereichen wiederholt werden.",
        ))
    total_issues = len(issues)
    stored_issues = issues[:MAX_ISSUES_PER_RUN]
    counts = {category: sum(1 for item in issues if item["category"] == category) for category in ("WARNING", "ERROR", "REQUIRES_REVIEW")}
    result_status = "OK" if not issues else "REQUIRES_REVIEW" if counts["REQUIRES_REVIEW"] or counts["ERROR"] else "WARNING"
    document = {
        "id": run_id,
        "status": result_status,
        "actorId": actor_id,
        "startedAt": started_at,
        "finishedAt": _now(),
        "counts": {"OK": 1 if not issues else 0, **counts},
        "issues": stored_issues,
        "issueCount": total_issues,
        "truncated": total_issues > len(stored_issues),
        "scanTruncated": scan_truncated,
        "documentsInspected": {
            "orders": len(orders), "invoices": len(invoices), "shopOrders": len(shop_orders),
            "machineRequests": len(machine_requests), "idempotencyOperations": len(operations),
            "paymentEvents": len(payment_events),
        },
        "retentionClass": "technical_log",
    }
    if checkpoint:
        await checkpoint()
    await access.reconciliation_runs.insert_one(document)
    if checkpoint:
        await checkpoint()
    return document
