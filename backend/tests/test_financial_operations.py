from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_financial_operations")
os.environ.setdefault("JWT_SECRET", "test-only-financial-operations-secret-32-bytes")
os.environ.setdefault("APP_ENV", "test")

from app.accounting import AccountingContactResult, AccountingInvoiceResult, AccountingProviderError, SevdeskProvider, process_accounting_sync, request_accounting_retry, schedule_accounting_sync
from app.commissions import (
    CommissionAmbiguous, append_commission_adjustment, create_pending_commission, create_settlement,
    earn_commission_for_payment, mark_settlement_paid, resolve_agreement, validate_commission_for_order,
)
from app.financial_operations import (
    DEFAULT_CREDIT_LIMIT_MINOR, available_credit, company_terms,
    evaluate_b2b_order, pallet_exposure, public_approval, receivable_status, receivables_summary,
    validate_b2b_payment_method,
)
from app.migrations.versions import v0016_financial_operations as migration16
from app.reconciliation import run_reconciliation
from app.routers import billing, financial_operations, invoices, orders, payments
from app.models import CompanyFinancialTermsIn, CommissionAgreementIn, CommissionPayoutIn, OrderStatusIn, PaymentRecordIn
from app.worker import schedule_pending_accounting_jobs
from test_customer_commerce_platform import AsyncDatabase, no_audit, principal, run, scoped


def prepare_financial_indexes(database: AsyncDatabase) -> None:
    database.raw.commission_entries.create_index([("tenantId", 1), ("eventKey", 1)], unique=True)
    database.raw.commission_settlements.create_index([("tenantId", 1), ("idempotencyKey", 1)], unique=True)
    database.raw.commission_settlements.create_index(
        [("tenantId", 1), ("settlementLockKey", 1)], unique=True,
        partialFilterExpression={"settlementLockKey": {"$type": "string"}},
    )
    database.raw.accounting_syncs.create_index([("tenantId", 1), ("resourceType", 1), ("resourceId", 1)], unique=True)
    database.raw.background_jobs.create_index([("tenantId", 1), ("jobType", 1), ("idempotencyKey", 1)], unique=True)


def company(company_id="c1", **extra):
    row = {"id": company_id, "name": "Customer", "active": True, "status": "Aktiv"}
    row.update(extra)
    return row


def product(product_id="p1", **extra):
    row = {"id": product_id, "name": "Coffee", "unit": "kg", "kgPerPallet": 600}
    row.update(extra)
    return row


@pytest.mark.parametrize("days", [7, 14, 30, 45])
def test_payment_terms_support_standard_and_custom_values(days):
    terms = company_terms(company(paymentTermsDays=days), default_currency="EUR")
    assert terms.payment_terms_days == days


def test_admin_can_update_customer_financial_terms(monkeypatch):
    database = AsyncDatabase("financial_terms_admin_update")
    access = scoped(database)
    run(access.companies.insert_one(company()))
    monkeypatch.setattr(financial_operations, "tenant_audit", no_audit)

    response = run(financial_operations.update_financial_terms(
        "c1",
        CompanyFinancialTermsIn(
            paymentTermsDays=14, creditLimitMinor=1_000_000,
            creditCurrency="EUR", palletApprovalLimit=1,
        ),
        principal(access), access,
    ))

    assert response["ok"] is True
    stored = database.raw.companies.find_one({"id": "c1"})
    assert stored["paymentTermsDays"] == 14
    assert stored["creditLimitMinor"] == 1_000_000


def test_invoice_due_date_uses_immutable_order_terms_snapshot(monkeypatch):
    database = AsyncDatabase("financial_due_date_snapshot")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.companies.insert_one(company(paymentTermsDays=30)))
    order = {
        "id": "o1", "companyId": "c1", "currency": "EUR", "status": "Neu",
        "paymentTermsSnapshot": {"paymentTermsDays": 14},
        "items": [{
            "snapshotVersion": 1, "productId": "p1", "productName": "Coffee", "unit": "kg",
            "qty": 1, "price": 10, "unitPriceMinor": 1000, "lineTotalMinor": 1000,
            "taxRate": 0, "currency": "EUR", "priceSource": "customer_price",
        }],
    }
    run(access.orders.insert_one(order))

    async def sequence(_name):
        return 1

    monkeypatch.setattr(billing, "next_seq", sequence)
    invoice = run(billing.create_invoice_record(access, order, principal(access)))
    invoice_date = date.fromisoformat(invoice["date"])
    assert date.fromisoformat(invoice["dueDate"]) == invoice_date + timedelta(days=14)
    assert invoice["paymentTermsSnapshot"] == {"paymentTermsDays": 14}


def test_credit_defaults_and_customer_override_are_currency_explicit():
    defaults = company_terms(company(), default_currency="EUR")
    custom = company_terms(company(creditLimitMinor=2_500_000, creditCurrency="CHF"), default_currency="EUR")
    assert defaults.credit_limit_minor == DEFAULT_CREDIT_LIMIT_MINOR
    assert (defaults.credit_currency, custom.credit_currency, custom.credit_limit_minor) == ("EUR", "CHF", 2_500_000)


def test_financial_terms_and_commission_rate_reject_manipulated_values():
    with pytest.raises(ValidationError):
        CompanyFinancialTermsIn(
            paymentTermsDays=-1, creditLimitMinor=-1,
            creditCurrency="EUR", palletApprovalLimit=float("nan"),
        )
    with pytest.raises(ValidationError):
        CommissionAgreementIn(
            salesRepId="sales-1", commissionType="PER_KG", rateMinor=0, currency="EUR",
        )


def test_available_credit_counts_open_receivables_and_confirmed_uninvoiced_orders():
    database = AsyncDatabase("financial_available_credit")
    access = scoped(database)
    run(access.companies.insert_one(company(creditLimitMinor=1_000_000, creditCurrency="EUR")))
    run(access.invoices.insert_one({"id": "i1", "companyId": "c1", "currency": "EUR", "amountMinor": 300_000, "amount": 3000, "paidAmountMinor": 50_000, "status": "Teilweise bezahlt"}))
    run(access.invoices.insert_one({"id": "cancelled", "companyId": "c1", "currency": "EUR", "amountMinor": 900_000, "amount": 9000, "status": "Storniert"}))
    run(access.orders.insert_one({"id": "o1", "companyId": "c1", "currency": "EUR", "netTotalMinor": 100_000, "status": "Bestätigt"}))
    result = run(available_credit(access, company=company(creditLimitMinor=1_000_000, creditCurrency="EUR"), currency="EUR"))
    assert result == {"currency": "EUR", "creditLimitMinor": 1_000_000, "receivablesMinor": 250_000, "reservedMinor": 100_000, "availableMinor": 650_000}


def test_uninvoiced_new_orders_reserve_credit_and_cancelled_orders_do_not():
    database = AsyncDatabase("financial_available_credit_new")
    access = scoped(database)
    customer = company(creditLimitMinor=100_000, creditCurrency="EUR")
    run(access.orders.insert_one({"id": "new", "companyId": "c1", "currency": "EUR", "netTotalMinor": 60_000, "status": "Neu"}))
    run(access.orders.insert_one({"id": "cancelled", "companyId": "c1", "currency": "EUR", "netTotalMinor": 90_000, "status": "Storniert"}))
    result = run(available_credit(access, company=customer, currency="EUR"))
    assert result["reservedMinor"] == 60_000
    assert result["availableMinor"] == 40_000


def test_confirmation_rechecks_combined_credit_exposure_fail_closed():
    database = AsyncDatabase("financial_credit_confirmation")
    access = scoped(database)
    customer = company(creditLimitMinor=100_000, creditCurrency="EUR", paymentTermsDays=14)
    run(access.companies.insert_one(customer))
    for order_id in ("o1", "o2"):
        run(access.orders.insert_one({
            "id": order_id, "companyId": "c1", "currency": "EUR",
            "netTotalMinor": 60_000, "status": "Neu",
            "items": [{"snapshotVersion": 1, "currency": "EUR", "productId": "p1", "unit": "kg", "qty": 10}],
            "financialApproval": {"required": False, "status": "not_required", "reasons": []},
        }))
    with pytest.raises(HTTPException) as rejected:
        run(orders.set_order_status("o1", OrderStatusIn(status="Bestätigt"), principal(access), access))
    assert rejected.value.status_code == 409
    stored = database.raw.orders.find_one({"id": "o1"})
    assert stored["status"] == "Freigabe nötig"
    assert stored["financialApproval"]["status"] == "required"


def test_confirmation_applies_full_financial_check_to_order_without_prior_evaluation(monkeypatch):
    database = AsyncDatabase("financial_offer_order_confirmation")
    access = scoped(database)
    run(access.companies.insert_one(company(
        paymentTermsDays=14, creditLimitMinor=1_000_000,
        creditCurrency="EUR", palletApprovalLimit=1,
    )))
    run(access.products.insert_one(product(kgPerPallet=600)))
    run(access.orders.insert_one({
        "id": "o1", "companyId": "c1", "currency": "EUR",
        "netTotalMinor": 100_000, "status": "Neu",
        "items": [{"productId": "p1", "unit": "kg", "qty": 60}],
    }))
    monkeypatch.setattr(orders, "tenant_audit", no_audit)

    response = run(orders.set_order_status(
        "o1", OrderStatusIn(status="Bestätigt"), principal(access), access,
    ))

    assert response == {"ok": True, "status": "Bestätigt"}
    stored = database.raw.orders.find_one({"id": "o1"})
    assert stored["financialApproval"]["status"] == "not_required"
    assert stored["paymentTermsSnapshot"]["paymentTermsDays"] == 14


def test_confirmation_without_pallet_configuration_requires_approval(monkeypatch):
    database = AsyncDatabase("financial_offer_order_missing_pallet")
    access = scoped(database)
    run(access.companies.insert_one(company(
        paymentTermsDays=14, creditLimitMinor=1_000_000,
        creditCurrency="EUR", palletApprovalLimit=1,
    )))
    run(access.products.insert_one(product(kgPerPallet=None)))
    run(access.orders.insert_one({
        "id": "o1", "companyId": "c1", "currency": "EUR",
        "netTotalMinor": 100_000, "status": "Neu",
        "items": [{"productId": "p1", "unit": "kg", "qty": 60}],
    }))
    monkeypatch.setattr(orders, "tenant_audit", no_audit)

    with pytest.raises(HTTPException) as rejected:
        run(orders.set_order_status(
            "o1", OrderStatusIn(status="Bestätigt"), principal(access), access,
        ))

    assert rejected.value.status_code == 409
    stored = database.raw.orders.find_one({"id": "o1"})
    assert stored["status"] == "Freigabe nötig"
    assert {reason["code"] for reason in stored["financialApproval"]["reasons"]} == {
        "PALLET_CONFIGURATION_MISSING",
    }


def test_credit_never_mixes_eur_and_chf():
    database = AsyncDatabase("financial_currency")
    access = scoped(database)
    with pytest.raises(ValueError, match="currency"):
        run(available_credit(access, company=company(creditLimitMinor=1000, creditCurrency="CHF"), currency="EUR"))


def test_receivable_states_and_aging_are_deterministic():
    today = date(2026, 10, 15)
    assert receivable_status({"currency": "EUR", "amountMinor": 100, "paidAmountMinor": 0, "dueDate": "2026-10-15"}, today=today) == "DUE"
    assert receivable_status({"currency": "EUR", "amountMinor": 100, "paidAmountMinor": 1, "dueDate": "2026-10-01"}, today=today) == "PARTIALLY_PAID"
    assert receivable_status({"currency": "EUR", "amountMinor": 100, "paidAmountMinor": 0, "dueDate": "2026-10-14"}, today=today) == "OVERDUE"
    assert receivable_status({"currency": "EUR", "amountMinor": 100, "paidAmountMinor": 100}, today=today) == "PAID"


def test_partial_payment_remains_part_of_overdue_exposure():
    database = AsyncDatabase("financial_partial_overdue")
    access = scoped(database)
    run(access.invoices.insert_one({
        "id": "i1", "companyId": "c1", "currency": "EUR",
        "amountMinor": 1000, "paidAmountMinor": 250,
        "status": "Teilweise bezahlt", "dueDate": "2026-10-01",
    }))

    summary = run(receivables_summary(
        access, company_ids=["c1"], currency="EUR", today=date(2026, 10, 15),
    ))

    assert summary["openMinor"] == 750
    assert summary["partialMinor"] == 750
    assert summary["overdueMinor"] == 750
    assert summary["aging"]["overdue_8_30"] == 750


def test_pallet_exposure_supports_648kg_and_mixed_skus_without_brand_logic():
    total, lines = pallet_exposure([
        {"product": product("aiello", kgPerPallet=648), "qty": 648},
        {"product": product("gambilongo", kgPerPallet=720), "qty": 360},
    ])
    assert total == 1.5
    assert [line["palletEquivalent"] for line in lines] == [1.0, 0.5]
    missing, snapshots = pallet_exposure([{"product": product("unknown", kgPerPallet=None), "qty": 10}])
    assert missing is None and snapshots[0]["configured"] is False


def test_pallet_exposure_supports_case_and_piece_configurations():
    cases, case_lines = pallet_exposure([{
        "product": product("case", unit="case", kgPerPallet=None, casesPerPallet=36), "qty": 18,
    }])
    pieces, piece_lines = pallet_exposure([{
        "product": product("piece", unit="piece", kgPerPallet=None, unitsPerCase=6, casesPerPallet=30), "qty": 90,
    }])
    assert cases == Decimal("0.5") and case_lines[0]["calculationUnit"] == "case"
    assert pieces == Decimal("0.5") and piece_lines[0]["quantityPerPallet"] == 180


def test_conflicting_pallet_configuration_fails_closed():
    pallets, lines = pallet_exposure([{
        "product": product(kgPerPallet=648, kgPerCase=6, casesPerPallet=100),
        "qty": 648,
    }])
    assert pallets is None
    assert lines[0]["configured"] is False


def test_order_approval_reasons_cover_credit_pallet_overdue_block_and_missing_config():
    database = AsyncDatabase("financial_approval")
    access = scoped(database)
    customer = company(
        status="Gesperrt", paymentTermsDays=14, creditLimitMinor=10_000,
        creditCurrency="EUR", palletApprovalLimit=1,
    )
    run(access.companies.insert_one(customer))
    run(access.invoices.insert_one({
        "id": "overdue", "companyId": "c1", "currency": "EUR", "amountMinor": 5_000,
        "amount": 50, "paidAmountMinor": 0, "status": "Offen", "dueDate": "2020-01-01",
    }))
    result = run(evaluate_b2b_order(
        access, company=customer,
        item_products=[{"product": product(kgPerPallet=100), "qty": 200}],
        order_total_minor=6_000, currency="EUR",
    ))
    codes = {reason["code"] for reason in result["reasons"]}
    assert {"CUSTOMER_BLOCKED", "AVAILABLE_CREDIT_EXCEEDED", "PALLET_LIMIT_EXCEEDED", "OVERDUE_RECEIVABLES"} <= codes


def test_customer_approval_response_hides_internal_credit_reasons():
    evaluation = {
        "required": True, "status": "required", "palletExposure": 2,
        "reasons": [
            {"code": "AVAILABLE_CREDIT_EXCEEDED", "message": "Verfügbare Kreditlinie reicht nicht aus"},
            {"code": "OVERDUE_RECEIVABLES", "message": "Überfällige Forderungen vorhanden"},
        ],
        "creditSnapshot": {"creditLimitMinor": 100_000, "availableMinor": 0},
        "termsSnapshot": {"paymentTermsDays": 14},
    }
    result = public_approval(evaluation, admin=False, customer=True)
    assert result["reasons"] == [{"code": "ORDER_REVIEW_REQUIRED", "message": "Bestellung wird geprüft"}]
    assert "creditSnapshot" not in result and "termsSnapshot" not in result


@pytest.mark.parametrize("method", ["card", "other"])
def test_b2b_payment_methods_block_online_and_unknown_methods(method):
    with pytest.raises(HTTPException) as rejected:
        validate_b2b_payment_method(method)
    assert rejected.value.status_code == 400

    validate_b2b_payment_method("bank_transfer")
    validate_b2b_payment_method("cash")


def test_b2b_invoice_cannot_create_stripe_checkout():
    database = AsyncDatabase("financial_b2b_checkout")
    access = scoped(database)
    run(access.companies.insert_one(company()))
    run(access.invoices.insert_one({
        "id": "i1", "companyId": "c1", "salesChannel": "b2b", "status": "Offen",
        "currency": "EUR", "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0,
    }))
    with pytest.raises(HTTPException) as rejected:
        run(payments.create_checkout("i1", principal(access), access))
    assert rejected.value.status_code == 409


def _seed_agreement(access, agreement_id="a1", **extra):
    if not run(access.tenant_memberships.find_one({"userId": "sales-1"})):
        run(access.tenant_memberships.insert_one({
            "id": "mem-sales-1", "userId": "sales-1", "role": "sales", "active": True,
        }))
    row = {
        "id": agreement_id, "salesRepId": "sales-1", "commissionType": "PER_KG",
        "rateMinor": 250, "currency": "EUR", "active": True,
    }
    row.update(extra)
    run(access.commission_agreements.insert_one(row))
    return row


def _order(**extra):
    row = {
        "id": "o1", "companyId": "c1", "currency": "EUR",
        "items": [{"productId": "p1", "qty": 10, "unit": "kg"}],
        "salesAttribution": {"salesRepId": "sales-1", "salesRepName": "Sales"},
    }
    row.update(extra)
    return row


def test_per_kg_commission_is_pending_then_proportionally_earned_and_duplicate_safe():
    database = AsyncDatabase("financial_commission")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)
    pending = run(create_pending_commission(access, order=_order()))
    run(create_pending_commission(access, order=_order()))
    assert pending[0]["amountMinor"] == 2500
    first = run(earn_commission_for_payment(access, order_id="o1", invoice_id="i1", payment_id="pay1", payment_amount_minor=4000, paid_total_minor=4000, document_total_minor=10_000))
    replay = run(earn_commission_for_payment(access, order_id="o1", invoice_id="i1", payment_id="pay1", payment_amount_minor=4000, paid_total_minor=4000, document_total_minor=10_000))
    final = run(earn_commission_for_payment(access, order_id="o1", invoice_id="i1", payment_id="pay2", payment_amount_minor=6000, paid_total_minor=10_000, document_total_minor=10_000))
    assert first[0]["amountMinor"] == 1000
    assert replay == []
    assert final[0]["amountMinor"] == 1500
    assert database.raw.commission_entries.count_documents({"tenantId": "tenant-a"}) == 3


def test_concurrent_partial_payment_commission_uses_disjoint_payment_intervals():
    database = AsyncDatabase("financial_commission_parallel_payments")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)
    run(create_pending_commission(access, order=_order()))

    async def earn_both():
        return await asyncio.gather(
            earn_commission_for_payment(
                access, order_id="o1", invoice_id="i1", payment_id="pay1", payment_amount_minor=4000,
                paid_total_minor=4000, document_total_minor=10_000,
            ),
            earn_commission_for_payment(
                access, order_id="o1", invoice_id="i1", payment_id="pay2", payment_amount_minor=6000,
                paid_total_minor=10_000, document_total_minor=10_000,
            ),
        )

    first, second = run(earn_both())
    assert first[0]["amountMinor"] == 1000
    assert second[0]["amountMinor"] == 1500
    assert sum(row["amountMinor"] for row in first + second) == 2500


def test_repeated_product_lines_create_distinct_stable_pending_entries():
    database = AsyncDatabase("financial_commission_repeated_lines")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)
    order = _order(items=[
        {"productId": "p1", "qty": 2, "unit": "kg"},
        {"productId": "p1", "qty": 3, "unit": "kg"},
    ])
    first = run(create_pending_commission(access, order=order))
    replay = run(create_pending_commission(access, order=order))
    assert [row["amountMinor"] for row in first] == [500, 750]
    assert [row["id"] for row in replay] == [row["id"] for row in first]


def test_simultaneous_pending_commission_creation_is_duplicate_safe():
    database = AsyncDatabase("financial_commission_parallel_pending")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)

    async def create_both():
        return await asyncio.gather(
            create_pending_commission(access, order=_order()),
            create_pending_commission(access, order=_order()),
        )

    first, second = run(create_both())
    assert first[0]["eventKey"] == second[0]["eventKey"]
    assert database.raw.commission_entries.count_documents({"status": "PENDING"}) == 1


def test_order_confirmation_validation_rejects_corrupt_legacy_commission_agreement():
    database = AsyncDatabase("financial_commission_invalid_legacy")
    access = scoped(database)
    _seed_agreement(access, rateMinor=0)

    with pytest.raises(CommissionAmbiguous, match="rate is invalid"):
        run(validate_commission_for_order(access, _order()))


def test_commission_currency_and_historical_agreement_snapshot_are_stable():
    database = AsyncDatabase("financial_commission_snapshot")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access, rateMinor=400, currency="CHF")
    pending = run(create_pending_commission(access, order=_order(currency="CHF")))[0]
    run(access.commission_agreements.update_one({"id": "a1"}, {"$set": {"rateMinor": 999}}))
    assert pending["currency"] == "CHF"
    assert pending["agreementSnapshot"]["rateMinor"] == 400


def test_inactive_sales_membership_does_not_create_commission():
    database = AsyncDatabase("financial_commission_inactive_sales")
    access = scoped(database)
    _seed_agreement(access)
    run(access.tenant_memberships.update_one({"userId": "sales-1"}, {"$set": {"active": False}}))
    assert run(create_pending_commission(access, order=_order())) == []


def test_recurring_orders_create_distinct_commission_and_reassignment_preserves_history():
    database = AsyncDatabase("financial_commission_recurring")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)
    first = run(create_pending_commission(access, order=_order(id="order-1")))[0]
    second = run(create_pending_commission(access, order=_order(id="order-2")))[0]
    assert first["id"] != second["id"]
    reassigned = _order(
        id="order-3",
        salesAttribution={"salesRepId": "sales-2", "salesRepName": "New Sales"},
    )
    assert run(create_pending_commission(access, order=reassigned)) == []
    stored = database.raw.commission_entries.find_one({"id": first["id"]})
    assert stored["salesAttributionSnapshot"]["salesRepId"] == "sales-1"


def test_ambiguous_commission_agreements_fail_closed_without_double_commission():
    database = AsyncDatabase("financial_commission_ambiguous")
    access = scoped(database)
    _seed_agreement(access, "a1")
    _seed_agreement(access, "a2", companyId="c1")
    with pytest.raises(CommissionAmbiguous):
        run(resolve_agreement(access, sales_rep_id="sales-1", company_id="c1", product_id="p1", currency="EUR", at=datetime.now(timezone.utc)))


def test_admin_can_deactivate_agreement_without_mutating_historical_entries(monkeypatch):
    database = AsyncDatabase("financial_commission_agreement_update")
    access = scoped(database)
    _seed_agreement(access)
    historical = {
        "id": "pending-1", "eventKey": "pending-1", "status": "PENDING",
        "salesRepId": "sales-1", "currency": "EUR", "amountMinor": 250,
        "agreementSnapshot": {"agreementId": "a1", "rateMinor": 250},
    }
    run(access.commission_entries.insert_one(historical))
    monkeypatch.setattr(financial_operations, "tenant_audit", no_audit)
    updated = run(financial_operations.update_commission_agreement(
        "a1",
        CommissionAgreementIn(
            salesRepId="sales-1", commissionType="PER_KG", rateMinor=250,
            currency="EUR", active=False,
        ),
        principal(access), access,
    ))
    assert updated["active"] is False
    stored = database.raw.commission_entries.find_one({"id": "pending-1"})
    assert stored["agreementSnapshot"] == historical["agreementSnapshot"]


def test_commission_agreement_rejects_inverted_validity_period(monkeypatch):
    database = AsyncDatabase("financial_commission_agreement_period")
    access = scoped(database)
    _seed_agreement(access)
    monkeypatch.setattr(financial_operations, "tenant_audit", no_audit)
    with pytest.raises(HTTPException) as rejected:
        run(financial_operations.update_commission_agreement(
            "a1",
            CommissionAgreementIn(
                salesRepId="sales-1", commissionType="PER_KG", rateMinor=250,
                currency="EUR", active=True,
                validFrom=datetime(2027, 1, 1, tzinfo=timezone.utc),
                validUntil=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            principal(access), access,
        ))
    assert rejected.value.status_code == 400

    with pytest.raises(HTTPException) as create_rejected:
        run(financial_operations.create_commission_agreement(
            CommissionAgreementIn(
                salesRepId="sales-1", commissionType="PER_KG", rateMinor=250,
                currency="EUR", active=True,
                validFrom=datetime(2027, 1, 1, tzinfo=timezone.utc),
                validUntil=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ),
            principal(access), access,
        ))
    assert create_rejected.value.status_code == 400


def test_sales_commission_ledger_is_own_only_and_period_filter_is_server_side():
    database = AsyncDatabase("financial_commission_visibility")
    access = scoped(database)
    for row in (
        {"id": "own", "eventKey": "own", "salesRepId": "sales-1", "currency": "EUR", "status": "EARNED", "amountMinor": 100, "createdAt": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"id": "other", "eventKey": "other", "salesRepId": "sales-2", "currency": "EUR", "status": "EARNED", "amountMinor": 200, "createdAt": datetime(2026, 6, 1, tzinfo=timezone.utc)},
        {"id": "old", "eventKey": "old", "salesRepId": "sales-1", "currency": "EUR", "status": "EARNED", "amountMinor": 300, "createdAt": datetime(2025, 6, 1, tzinfo=timezone.utc)},
    ):
        run(access.commission_entries.insert_one(row))
    sales_user = {"id": "sales-1", "role": "sales"}
    visible = run(financial_operations.list_commission_ledger(
        sales_user, access, sales_rep_id=None, status="EARNED", currency="EUR",
        start="2026-01-01T00:00:00Z", end="2027-01-01T00:00:00Z", limit=500,
    ))
    assert [row["id"] for row in visible] == ["own"]
    with pytest.raises(HTTPException) as denied:
        run(financial_operations.list_commission_ledger(
            sales_user, access, sales_rep_id="sales-2", status=None, currency=None,
            start=None, end=None, limit=500,
        ))
    assert denied.value.status_code == 403


def test_settlement_is_locked_idempotent_and_admin_payout_preserves_entries():
    database = AsyncDatabase("financial_settlement")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({"id": "e1", "eventKey": "e1", "salesRepId": "sales-1", "currency": "EUR", "status": "EARNED", "amountMinor": 500, "createdAt": datetime.now(timezone.utc)}))
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)
    first = run(create_settlement(access, sales_rep_id="sales-1", currency="EUR", actor_id="admin", idempotency_key="settlement-1", period_start=start, period_end=end))
    replay = run(create_settlement(access, sales_rep_id="sales-1", currency="EUR", actor_id="admin", idempotency_key="settlement-1", period_start=start, period_end=end))
    paid = run(mark_settlement_paid(access, settlement_id=first["id"], reference="bank-1", actor_id="admin"))
    replay_paid = run(mark_settlement_paid(access, settlement_id=first["id"], reference="bank-1", actor_id="admin"))
    assert first["id"] == replay["id"] and first["status"] == "LOCKED"
    assert paid["status"] == "PAID_OUT"
    assert replay_paid["id"] == paid["id"]
    assert database.raw.commission_entries.find_one({"id": "e1"})["status"] == "EARNED"


def test_simultaneous_settlement_creation_has_one_locked_result():
    database = AsyncDatabase("financial_settlement_parallel")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({"id": "e1", "eventKey": "e1", "salesRepId": "sales-1", "currency": "EUR", "status": "EARNED", "amountMinor": 500, "createdAt": datetime.now(timezone.utc)}))
    start, end = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)

    async def create_both():
        return await asyncio.gather(*[
            create_settlement(access, sales_rep_id="sales-1", currency="EUR", actor_id="admin", idempotency_key=key, period_start=start, period_end=end)
            for key in ("settlement-parallel-1", "settlement-parallel-2")
        ])

    first, second = run(create_both())
    assert first["id"] == second["id"]
    assert database.raw.commission_settlements.count_documents({"status": "LOCKED"}) == 1


def test_concurrent_settlement_for_different_period_never_returns_wrong_period():
    database = AsyncDatabase("financial_settlement_parallel_periods")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({"id": "e1", "eventKey": "e1", "salesRepId": "sales-1", "currency": "EUR", "status": "EARNED", "amountMinor": 500, "createdAt": datetime(2026, 6, 1, tzinfo=timezone.utc)}))

    async def create_both():
        return await asyncio.gather(
            create_settlement(
                access, sales_rep_id="sales-1", currency="EUR", actor_id="admin",
                idempotency_key="first-period",
                period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                period_end=datetime(2027, 1, 1, tzinfo=timezone.utc),
            ),
            create_settlement(
                access, sales_rep_id="sales-1", currency="EUR", actor_id="admin",
                idempotency_key="second-period",
                period_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
                period_end=datetime(2026, 7, 1, tzinfo=timezone.utc),
            ),
            return_exceptions=True,
        )

    results = run(create_both())
    assert sum(isinstance(value, dict) for value in results) == 1
    assert sum(isinstance(value, ValueError) for value in results) == 1


def test_commission_adjustments_are_append_only_idempotent_and_cannot_exceed_earned_value():
    database = AsyncDatabase("financial_commission_adjustment")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({
        "id": "earned-1", "eventKey": "earned-1", "salesRepId": "sales-1",
        "currency": "EUR", "status": "EARNED", "amountMinor": 1000,
        "createdAt": datetime.now(timezone.utc),
    }))
    first = run(append_commission_adjustment(
        access, source_entry_id="earned-1", event_key="credit-1", amount_minor=-400,
        reason="Teil-Gutschrift", actor_id="admin", kind="partial_credit", reference="GS-1",
    ))
    replay = run(append_commission_adjustment(
        access, source_entry_id="earned-1", event_key="credit-1", amount_minor=-400,
        reason="Teil-Gutschrift", actor_id="admin", kind="partial_credit", reference="GS-1",
    ))
    final = run(append_commission_adjustment(
        access, source_entry_id="earned-1", event_key="credit-2", amount_minor=-600,
        reason="Restliche Gutschrift", actor_id="admin", kind="full_credit", reference="GS-2",
    ))
    assert first["status"] == "ADJUSTED" and replay["id"] == first["id"]
    assert final["status"] == "REVERSED"
    assert database.raw.commission_entries.find_one({"id": "earned-1"})["amountMinor"] == 1000
    with pytest.raises(ValueError, match="exceeds"):
        run(append_commission_adjustment(
            access, source_entry_id="earned-1", event_key="credit-3", amount_minor=-1,
            reason="Zu viel", actor_id="admin",
        ))


@pytest.mark.parametrize("kind", ["cancellation", "refund", "reversal"])
def test_commission_reduction_kinds_create_reversal_entries(kind):
    database = AsyncDatabase(f"financial_commission_{kind}")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({
        "id": "earned-1", "eventKey": "earned-1", "salesRepId": "sales-1",
        "currency": "EUR", "status": "EARNED", "amountMinor": 1000,
        "createdAt": datetime.now(timezone.utc),
    }))
    row = run(append_commission_adjustment(
        access, source_entry_id="earned-1", event_key=f"{kind}-1",
        amount_minor=-1000, reason=kind, actor_id="admin", kind=kind,
        reference=f"reference-{kind}",
    ))
    assert row["status"] == "REVERSED"
    assert row["adjustmentKind"] == kind


def test_adjustment_after_paid_settlement_is_carried_into_later_settlement():
    database = AsyncDatabase("financial_commission_later_adjustment")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.commission_entries.insert_one({
        "id": "earned-old", "eventKey": "earned-old", "salesRepId": "sales-1",
        "currency": "EUR", "status": "EARNED", "amountMinor": 1000,
        "createdAt": datetime(2026, 1, 15, tzinfo=timezone.utc),
    }))
    old = run(create_settlement(
        access, sales_rep_id="sales-1", currency="EUR", actor_id="admin",
        idempotency_key="old", period_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 2, 1, tzinfo=timezone.utc),
    ))
    run(mark_settlement_paid(access, settlement_id=old["id"], reference="paid-old", actor_id="admin"))
    run(append_commission_adjustment(
        access, source_entry_id="earned-old", event_key="late-credit", amount_minor=-200,
        reason="Spätere Gutschrift", actor_id="admin", kind="partial_credit", reference="GS-late",
    ))
    run(access.commission_entries.insert_one({
        "id": "earned-new", "eventKey": "earned-new", "salesRepId": "sales-1",
        "currency": "EUR", "status": "EARNED", "amountMinor": 1000,
        "createdAt": datetime.now(timezone.utc),
    }))
    now = datetime.now(timezone.utc)
    month_start = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    month_end = (
        datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
        if now.month == 12 else datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    )
    later = run(create_settlement(
        access, sales_rep_id="sales-1", currency="EUR", actor_id="admin",
        idempotency_key="later", period_start=month_start, period_end=month_end,
    ))
    assert later["amountMinor"] == 800


def test_per_kg_commission_fails_closed_without_kg_snapshot():
    database = AsyncDatabase("financial_commission_unit")
    access = scoped(database)
    _seed_agreement(access)
    with pytest.raises(CommissionAmbiguous, match="kilogram"):
        run(create_pending_commission(access, order=_order(items=[{"productId": "p1", "qty": 10, "unit": "piece"}])))


class FakeAccountingProvider:
    name = "fake"

    def __init__(self, fail_invoice=False):
        self.fail_invoice = fail_invoice
        self.contacts = 0
        self.invoices = 0
        self.contact_keys = []
        self.invoice_keys = []

    async def sync_contact(self, *, external_key, company):
        self.contacts += 1
        self.contact_keys.append(external_key)
        return AccountingContactResult("contact-1")

    async def sync_invoice(self, *, external_key, contact_id, document):
        self.invoices += 1
        self.invoice_keys.append(external_key)
        if self.fail_invoice:
            raise RuntimeError("provider down")
        return AccountingInvoiceResult("provider-invoice-1", "RE-EXT-1", "doc-1")


def test_sevdesk_adapter_reuses_external_keys_before_creating_provider_objects(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    provider = SevdeskProvider(token="test-token-that-is-not-a-secret")
    posts = []

    async def fake_get(path, params):
        if path == "Contact":
            assert params["customerNumber"] == "c1"
            return {"objects": [{"id": "contact-existing"}]}
        assert params["customerInternalNote"] == "i1"
        return {"objects": [{"id": "invoice-existing", "invoiceNumber": "RE-EXT"}]}

    async def fake_post(path, payload):
        posts.append((path, payload))
        return {"objects": []}

    monkeypatch.setattr(provider, "_get", fake_get)
    monkeypatch.setattr(provider, "_post", fake_post)
    contact = run(provider.sync_contact(external_key="c1", company=company()))
    invoice = run(provider.sync_invoice(external_key="i1", contact_id=contact.provider_contact_id, document={}))
    assert contact.provider_contact_id == "contact-existing"
    assert invoice.provider_invoice_id == "invoice-existing"
    assert posts == []


def test_sevdesk_adapter_fails_closed_on_ambiguous_provider_duplicate(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    provider = SevdeskProvider(token="test-token-that-is-not-a-secret")

    async def duplicate_get(_path, _params):
        return {"objects": [{"id": "one"}, {"id": "two"}]}

    monkeypatch.setattr(provider, "_get", duplicate_get)
    with pytest.raises(AccountingProviderError, match="duplicate_requires_review"):
        run(provider.sync_contact(external_key="c1", company=company()))


def test_accounting_sync_is_not_configured_safe_and_provider_retry_is_idempotent(monkeypatch):
    database = AsyncDatabase("financial_accounting")
    prepare_financial_indexes(database)
    access = scoped(database)
    monkeypatch.delenv("ACCOUNTING_PROVIDER", raising=False)
    run(access.companies.insert_one(company()))
    run(access.invoices.insert_one({"id": "i1", "companyId": "c1", "currency": "EUR", "date": "2026-10-01", "lineItems": []}))
    scheduled = run(schedule_accounting_sync(access, resource_type="invoice", resource_id="i1", actor_id="admin"))
    assert scheduled["status"] == "not_configured"
    provider = FakeAccountingProvider()
    completed = run(process_accounting_sync(access, sync_id=scheduled["id"], provider=provider))
    replay = run(process_accounting_sync(access, sync_id=scheduled["id"], provider=provider))
    assert completed["providerInvoiceId"] == "provider-invoice-1"
    assert replay["providerInvoiceId"] == "provider-invoice-1"
    assert (provider.contacts, provider.invoices) == (1, 1)
    assert provider.contact_keys == ["tenant-a:c1"]
    assert provider.invoice_keys == ["tenant-a:invoice:i1"]


def test_staging_accounting_requires_explicit_write_gate(monkeypatch):
    database = AsyncDatabase("financial_accounting_staging_gate")
    prepare_financial_indexes(database)
    access = scoped(database)
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.setenv("ACCOUNTING_PROVIDER", "sevdesk")
    monkeypatch.setenv("SEVDESK_API_TOKEN", "staging-token-for-test-only")
    monkeypatch.delenv("SEVDESK_STAGING_WRITES_ENABLED", raising=False)
    disabled = run(schedule_accounting_sync(
        access, resource_type="invoice", resource_id="i-disabled", actor_id="admin",
    ))
    assert disabled["status"] == "not_configured"
    assert database.raw.background_jobs.count_documents({}) == 0

    monkeypatch.setenv("SEVDESK_STAGING_WRITES_ENABLED", "true")
    enabled = run(schedule_accounting_sync(
        access, resource_type="invoice", resource_id="i-enabled", actor_id="admin",
    ))
    assert enabled["status"] == "pending"
    assert database.raw.background_jobs.count_documents({}) == 1


def test_sevdesk_provider_fails_closed_when_environment_is_missing_or_unknown(monkeypatch):
    monkeypatch.setenv("SEVDESK_API_TOKEN", "environment-gate-test-token")
    for value in (None, "preview"):
        if value is None:
            monkeypatch.delenv("APP_ENV", raising=False)
        else:
            monkeypatch.setenv("APP_ENV", value)
        with pytest.raises(AccountingProviderError, match="environment_not_configured"):
            SevdeskProvider()


def test_simultaneous_accounting_retry_creates_only_one_job(monkeypatch):
    database = AsyncDatabase("financial_accounting_parallel_retry")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.accounting_syncs.insert_one({
        "id": "s1", "resourceType": "invoice", "resourceId": "i1", "status": "failed",
    }))
    monkeypatch.setenv("ACCOUNTING_PROVIDER", "sevdesk")
    monkeypatch.setenv("SEVDESK_API_TOKEN", "parallel-retry-test-token")
    monkeypatch.setenv("APP_ENV", "test")

    async def retry_both():
        return await asyncio.gather(
            request_accounting_retry(access, sync_id="s1", actor_id="admin"),
            request_accounting_retry(access, sync_id="s1", actor_id="admin"),
        )

    first, second = run(retry_both())
    assert first["status"] == second["status"] == "pending"
    assert database.raw.background_jobs.count_documents({"jobType": "accounting.sync"}) == 1


def test_provider_outage_does_not_change_paid_invoice():
    database = AsyncDatabase("financial_accounting_outage")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.companies.insert_one(company()))
    run(access.invoices.insert_one({"id": "i1", "companyId": "c1", "currency": "EUR", "date": "2026-10-01", "lineItems": [], "status": "Bezahlt"}))
    run(access.accounting_syncs.insert_one({"id": "s1", "resourceType": "invoice", "resourceId": "i1", "status": "pending"}))
    with pytest.raises(RuntimeError, match="provider down"):
        run(process_accounting_sync(access, sync_id="s1", provider=FakeAccountingProvider(fail_invoice=True)))
    assert database.raw.invoices.find_one({"id": "i1"})["status"] == "Bezahlt"


def test_accounting_reuses_company_contact_and_repairs_partial_local_completion():
    database = AsyncDatabase("financial_accounting_recovery")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.companies.insert_one(company(providerContactId="contact-existing")))
    run(access.invoices.insert_one({"id": "i1", "companyId": "c1", "currency": "EUR", "date": "2026-10-01", "lineItems": []}))
    run(access.accounting_syncs.insert_one({
        "id": "s1", "resourceType": "invoice", "resourceId": "i1", "status": "completed",
        "provider": "fake", "providerContactId": "contact-existing",
        "providerInvoiceId": "provider-invoice-1", "invoiceNumber": "RE-EXT-1",
        "lastSyncAt": datetime.now(timezone.utc),
    }))
    provider = FakeAccountingProvider()
    run(process_accounting_sync(access, sync_id="s1", provider=provider))
    invoice = database.raw.invoices.find_one({"id": "i1"})
    assert invoice["providerInvoiceId"] == "provider-invoice-1"
    assert (provider.contacts, provider.invoices) == (0, 0)


def test_pending_accounting_intent_is_reenqueued_and_failed_retry_is_explicit(monkeypatch):
    database = AsyncDatabase("financial_accounting_queue_recovery")
    prepare_financial_indexes(database)
    access = scoped(database)
    run(access.accounting_syncs.insert_one({
        "id": "s1", "resourceType": "invoice", "resourceId": "i1", "status": "pending",
        "createdAt": datetime.now(timezone.utc),
    }))
    assert run(schedule_pending_accounting_jobs(access)) == 1
    job = database.raw.background_jobs.find_one({"jobType": "accounting.sync"})
    assert job["payload"] == {"syncId": "s1"}
    database.raw.background_jobs.update_one({"id": job["id"]}, {"$set": {"status": "failed"}})
    database.raw.accounting_syncs.update_one({"id": "s1"}, {"$set": {"status": "failed"}})
    monkeypatch.setenv("ACCOUNTING_PROVIDER", "sevdesk")
    monkeypatch.setenv("SEVDESK_API_TOKEN", "retry-token-for-test-only")
    retried = run(request_accounting_retry(access, sync_id="s1", actor_id="admin"))
    assert retried["status"] == "pending"
    assert database.raw.background_jobs.find_one({"id": job["id"]})["status"] == "pending"


def test_reconciliation_reports_accounting_and_settlement_mismatches_without_repairing():
    database = AsyncDatabase("financial_reconciliation")
    access = scoped(database)
    run(access.invoices.insert_one({"id": "i1", "companyId": "c1", "amountMinor": 100, "paidAmountMinor": 0, "currency": "EUR", "status": "Offen"}))
    run(access.accounting_syncs.insert_one({"id": "s1", "resourceType": "invoice", "resourceId": "i1", "status": "completed", "providerInvoiceId": "provider-1"}))
    run(access.commission_entries.insert_one({"id": "e1", "amountMinor": 100, "currency": "EUR", "status": "EARNED"}))
    run(access.commission_settlements.insert_one({"id": "st1", "entryIds": ["e1"], "amountMinor": 90, "currency": "EUR", "status": "LOCKED"}))
    result = run(run_reconciliation(access, actor_id="admin"))
    assert {issue["code"] for issue in result["issues"]} >= {
        "accounting_resource_mismatch", "commission_settlement_amount_mismatch",
    }
    assert database.raw.invoices.find_one({"id": "i1"}).get("providerInvoiceId") is None


def test_sales_cannot_change_financial_terms_or_confirm_payment_runtime(monkeypatch):
    database = AsyncDatabase("financial_security")
    access = scoped(database, role="sales", actor="sales-1")
    run(access.companies.insert_one(company(assignedSalesRepId="sales-1")))
    with pytest.raises(HTTPException) as denied:
        run(financial_operations.update_financial_terms(
            "c1", CompanyFinancialTermsIn(paymentTermsDays=14, creditLimitMinor=1000, creditCurrency="EUR", palletApprovalLimit=1),
            principal(access), access,
        ))
    assert denied.value.status_code == 403
    invoice = {"id": "i1", "companyId": "c1", "currency": "EUR", "amountMinor": 1000, "amount": 10, "status": "Offen"}
    with pytest.raises(HTTPException) as payment_denied:
        run(invoices._record_payment(principal(access), access, invoice, PaymentRecordIn(amount=10, method="cash")))
    assert payment_denied.value.status_code == 403
    with pytest.raises(HTTPException) as payout_denied:
        run(financial_operations.pay_commission_settlement(
            "settlement-own", CommissionPayoutIn(reference="self-confirmed"), principal(access), access,
        ))
    assert payout_denied.value.status_code == 403


def test_admin_cannot_record_online_payment_method_for_b2b_invoice():
    database = AsyncDatabase("financial_b2b_manual_payment_method")
    access = scoped(database)
    invoice = {
        "id": "i1", "companyId": "c1", "currency": "EUR", "salesChannel": "b2b",
        "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen",
    }
    with pytest.raises(HTTPException) as rejected:
        run(invoices._record_payment(
            principal(access), access, invoice, PaymentRecordIn(amount=10, method="card"),
        ))
    assert rejected.value.status_code == 400


def test_admin_cannot_manually_mark_b2c_invoice_paid():
    database = AsyncDatabase("financial_b2c_manual_payment")
    access = scoped(database)
    invoice = {
        "id": "i1", "companyId": "c1", "currency": "EUR", "salesChannel": "b2c",
        "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen",
    }
    with pytest.raises(HTTPException) as rejected:
        run(invoices._record_payment(
            principal(access), access, invoice, PaymentRecordIn(amount=10, method="card"),
        ))
    assert rejected.value.status_code == 403


def test_legacy_b2b_full_payment_defaults_to_bank_transfer(monkeypatch):
    database = AsyncDatabase("financial_legacy_b2b_payment")
    access = scoped(database)
    run(access.companies.insert_one(company()))
    run(access.orders.insert_one({"id": "o1", "companyId": "c1", "status": "Bestätigt"}))
    run(access.invoices.insert_one({
        "id": "i1", "orderId": "o1", "companyId": "c1", "currency": "EUR",
        "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen",
    }))
    monkeypatch.setattr(invoices, "tenant_audit", no_audit)

    response = run(invoices.mark_invoice_paid("i1", principal(access), access))

    assert response["status"] == "Bezahlt"
    stored = database.raw.invoices.find_one({"id": "i1"})
    assert stored["paymentRecords"][0]["method"] == "bank_transfer"


def test_customer_cannot_confirm_own_invoice_payment():
    database = AsyncDatabase("financial_customer_payment_denied")
    access = scoped(database)
    invoice = {
        "id": "i1", "companyId": "c1", "currency": "EUR", "salesChannel": "b2b",
        "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen",
    }
    with pytest.raises(HTTPException) as denied:
        run(invoices._record_payment(
            {"id": "customer-1", "role": "customer", "companyId": "c1"}, access, invoice,
            PaymentRecordIn(amount=10, method="bank_transfer"),
        ))
    assert denied.value.status_code == 403


def test_recovered_payment_completes_missing_commission_side_effect(monkeypatch):
    database = AsyncDatabase("financial_payment_commission_recovery")
    prepare_financial_indexes(database)
    access = scoped(database)
    _seed_agreement(access)
    run(create_pending_commission(access, order=_order()))
    invoice = {
        "id": "i1", "orderId": "o1", "companyId": "c1", "currency": "EUR",
        "salesChannel": "b2b", "amountMinor": 10_000, "amount": 100,
        "paidAmountMinor": 10_000, "status": "Bezahlt", "paymentMethod": "bank_transfer",
        "paymentRecords": [{
            "id": "pay1", "operationId": "idem1", "amountMinor": 10_000,
            "method": "bank_transfer",
        }],
    }
    monkeypatch.setattr(invoices, "tenant_audit", no_audit)
    response = run(invoices._record_payment(
        principal(access), access, invoice,
        PaymentRecordIn(amount=100, method="bank_transfer"), operation_id="idem1",
    ))
    assert response["paymentId"] == "pay1"
    earned = database.raw.commission_entries.find_one({"paymentId": "pay1"})
    assert earned["status"] == "EARNED" and earned["amountMinor"] == 2500


def test_financial_migration_is_additive_only():
    plan = migration16.inspect(None)
    assert plan.expected_changes["documentsChanged"] == 0
    assert plan.expected_changes["indexesToEnsure"] >= 9
    settlement_lock = next(index for index in migration16.INDEXES if index[2] == "uniq_tenant_open_commission_settlement")
    assert settlement_lock[3] == {
        "unique": True,
        "partialFilterExpression": {"settlementLockKey": {"$type": "string"}},
    }


def test_financial_migration_apply_preserves_business_documents_and_builds_indexes():
    database = AsyncDatabase("financial_migration_apply")
    database.raw.orders.insert_one({"tenantId": "tenant-a", "id": "o1", "status": "Neu"})
    before = database.raw.orders.find_one({"id": "o1"})
    context = type("Context", (), {"checkpoint": lambda self: None})()

    result = migration16.apply(database.raw, context)

    assert result == {"indexesEnsured": len(migration16.INDEXES), "documentsChanged": 0}
    assert database.raw.orders.find_one({"id": "o1"}) == before
    settlement_indexes = database.raw.commission_settlements.index_information()
    assert settlement_indexes["uniq_tenant_open_commission_settlement"]["unique"] is True
    assert settlement_indexes["uniq_tenant_open_commission_settlement"]["partialFilterExpression"] == {
        "settlementLockKey": {"$type": "string"},
    }
