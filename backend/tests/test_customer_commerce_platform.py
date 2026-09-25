from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os

import mongomock
import pytest
from fastapi import HTTPException

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:1")
os.environ.setdefault("DB_NAME", "ordo_test_customer_commerce")
os.environ.setdefault("JWT_SECRET", "test-only-customer-commerce-secret-32-bytes")
os.environ.setdefault("APP_ENV", "test")

from app.models import (
    CompanyAssignmentIn, CompanyCreateIn, CompanyUpdateIn, CustomerActivityIn, CustomerPriceIn,
    CustomerTaskIn, EquipmentFinancingRequestIn, OrderCreate, PaymentRecordIn,
    MachineIn, ProductCategoryIn, ProductIn,
)
from app.routers import billing, companies, crm, dashboard, invoices, machines, orders, pricing, products, shop
from app.tenant_access import TenantBusinessAccess
from app.tenancy import TenantContext, TenantResolutionSource


class AsyncCursor:
    def __init__(self, cursor): self.cursor = cursor
    def sort(self, *args, **kwargs): self.cursor = self.cursor.sort(*args, **kwargs); return self
    async def to_list(self, length=None):
        rows = list(self.cursor)
        return rows if length is None else rows[:length]


class AsyncCollection:
    def __init__(self, collection): self.collection = collection
    def find(self, *args, **kwargs): return AsyncCursor(self.collection.find(*args, **kwargs))
    async def find_one(self, *args, **kwargs): return self.collection.find_one(*args, **kwargs)
    async def insert_one(self, document, *args, **kwargs): return self.collection.insert_one(deepcopy(document), *args, **kwargs)
    async def update_one(self, *args, **kwargs): return self.collection.update_one(*args, **kwargs)
    async def find_one_and_update(self, *args, **kwargs): return self.collection.find_one_and_update(*args, **kwargs)
    async def delete_one(self, *args, **kwargs): return self.collection.delete_one(*args, **kwargs)
    async def count_documents(self, *args, **kwargs): return self.collection.count_documents(*args, **kwargs)


class AsyncDatabase:
    def __init__(self, name): self.raw = mongomock.MongoClient(tz_aware=True)[name]
    def __getitem__(self, name): return AsyncCollection(self.raw[name])


def run(value): return asyncio.run(value)


def scoped(database, role="admin", actor="admin", tenant="tenant-a"):
    context = TenantContext(
        tenant_id=tenant, actor_user_id=actor, membership_id=f"mbr-{actor}", role=role,
        resolution_source=TenantResolutionSource.MEMBERSHIP, default_currency="EUR",
    )
    return TenantBusinessAccess(database, context)


def principal(access, *, company_id=None, name=None):
    return {"id": access.context.actor_user_id, "name": name or access.context.actor_user_id,
            "role": access.context.role, "companyId": company_id, "_tenant_context": access.context}


async def no_audit(*_args, **_kwargs): return None


def seed_product(access, product_id="p1", **extra):
    row = {
        "id": product_id, "name": "Universal Item", "brand": "", "unit": "piece",
        "active": True, "b2bAvailable": True, "b2cAvailable": True,
        "directPurchaseAllowed": True, "financingRequestAllowed": True,
        "currency": "EUR", "standardPriceMinor": 1000, "standardPrice": 10,
        "salesFloorMinor": 900, "salesFloor": 9, "absoluteFloorMinor": 800,
        "absoluteFloor": 8, "costMinor": 500, "cost": 5, "b2cPriceMinor": 1500,
        "b2cPrice": 15, "taxRate": 19, "b2cTiers": [],
    }
    row.update(extra)
    run(access.products.insert_one(row))


@pytest.fixture(autouse=True)
def isolate_audits(monkeypatch):
    for module in (companies, crm, invoices, pricing, products):
        if hasattr(module, "tenant_audit"):
            monkeypatch.setattr(module, "tenant_audit", no_audit)


def test_admin_and_sales_create_customer_with_expected_assignment():
    database = AsyncDatabase("customer_create")
    admin = scoped(database)
    sales = scoped(database, role="sales", actor="sales-1")
    run(admin.tenant_memberships.insert_one({"id": "mbr-sales", "userId": "sales-1", "role": "sales", "status": "active"}))
    admin_customer = run(companies.create_company(CompanyCreateIn(name="Admin Lead"), principal(admin), admin))
    sales_customer = run(companies.create_company(CompanyCreateIn(name="Sales Lead"), principal(sales), sales))
    assert admin_customer["assignedSalesRepId"] is None
    assert sales_customer["assignedSalesRepId"] == "sales-1"
    assert {admin_customer["status"], sales_customer["status"]} == {"Lead"}
    assert database.raw.customer_activities.count_documents({"tenantId": "tenant-a"}) == 2


def test_admin_assignment_is_tenant_scoped_and_sales_cannot_assign():
    database = AsyncDatabase("customer_assignment")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(admin.tenant_memberships.insert_one({"id": "m1", "userId": "sales-a", "role": "sales", "status": "active"}))
    run(admin.tenant_memberships.insert_one({"id": "m3", "userId": "sales-c", "role": "sales", "status": "active"}))
    result = run(companies.assign_company_sales_rep("c1", CompanyAssignmentIn(assignedSalesRepId="sales-a"), principal(admin), admin))
    assert result["assignedSalesRepId"] == "sales-a"
    reassigned = run(companies.assign_company_sales_rep("c1", CompanyAssignmentIn(assignedSalesRepId="sales-c"), principal(admin), admin))
    assert reassigned["assignedSalesRepId"] == "sales-c"
    foreign = scoped(database, tenant="tenant-b")
    run(foreign.tenant_memberships.insert_one({"id": "m2", "userId": "sales-b", "role": "sales", "status": "active"}))
    with pytest.raises(HTTPException, match="Vertrieb"):
        run(companies.assign_company_sales_rep("c1", CompanyAssignmentIn(assignedSalesRepId="sales-b"), principal(admin), admin))


def test_general_company_update_rejects_admin_as_sales_assignment():
    database = AsyncDatabase("customer_update_assignment")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(admin.tenant_memberships.insert_one({"id": "m-admin", "userId": "other-admin", "role": "admin", "status": "active"}))
    with pytest.raises(HTTPException, match="Vertrieb"):
        run(companies.update_company(
            "c1", CompanyUpdateIn(name="Customer", assignedSalesRepId="other-admin"),
            principal(admin), admin,
        ))


def test_crm_notes_and_tasks_are_limited_to_visible_sales_customer():
    database = AsyncDatabase("crm_visibility")
    sales = scoped(database, role="sales", actor="sales-a")
    run(sales.companies.insert_one({"id": "own", "name": "Own", "assignedSalesRepId": "sales-a", "active": True}))
    run(sales.companies.insert_one({"id": "foreign", "name": "Foreign", "assignedSalesRepId": "sales-b", "active": True}))
    run(sales.tenant_memberships.insert_one({"id": "m1", "userId": "sales-a", "role": "sales", "status": "active"}))
    user = principal(sales)
    activity = run(crm.create_customer_activity("own", CustomerActivityIn(type="note", title="Call", note="internal"), user, sales))
    task = run(crm.create_customer_task("own", CustomerTaskIn(title="Follow up", dueAt=datetime.now(timezone.utc) + timedelta(days=1)), user, sales))
    assert activity["internal"] is True and task["status"] == "open"
    with pytest.raises(HTTPException) as denied:
        run(crm.list_customer_activities("foreign", user, sales))
    assert denied.value.status_code == 403


def test_sales_cannot_assign_customer_task_to_another_staff_member():
    database = AsyncDatabase("crm_task_assignment")
    sales = scoped(database, role="sales", actor="sales-a")
    run(sales.companies.insert_one({"id": "own", "name": "Own", "assignedSalesRepId": "sales-a", "active": True}))
    run(sales.tenant_memberships.insert_one({"id": "m1", "userId": "sales-a", "role": "sales", "status": "active"}))
    run(sales.tenant_memberships.insert_one({"id": "m2", "userId": "sales-b", "role": "sales", "status": "active"}))
    with pytest.raises(HTTPException) as denied:
        run(crm.create_customer_task(
            "own",
            CustomerTaskIn(
                title="Foreign assignment",
                dueAt=datetime.now(timezone.utc) + timedelta(days=1),
                assignedUserId="sales-b",
            ),
            principal(sales), sales,
        ))
    assert denied.value.status_code == 403


def test_product_payload_hides_all_internal_commercial_fields_from_sales():
    database = AsyncDatabase("product_privacy")
    admin = scoped(database)
    seed_product(admin, internalCosts={"transport": 100}, margin=50, profitability="secret", metadata={"cost": 5})
    sales = scoped(database, role="sales", actor="sales")
    rows = run(products.get_products(principal(sales), sales))
    for field in ("cost", "costMinor", "salesFloor", "salesFloorMinor", "absoluteFloor", "absoluteFloorMinor", "internalCosts", "margin", "profitability", "metadata"):
        assert field not in rows[0]
    assert rows[0]["standardPriceMinor"] == 1000


def test_b2b_customer_catalog_does_not_expose_shop_prices():
    database = AsyncDatabase("product_b2b_price_context")
    admin = scoped(database)
    seed_product(admin)
    customer = TenantBusinessAccess(database, TenantContext(
        tenant_id="tenant-a", actor_user_id="customer", membership_id="mbr-customer",
        role="customer", company_id="c1", resolution_source=TenantResolutionSource.MEMBERSHIP,
        default_currency="EUR",
    ))

    rows = run(products.get_products(principal(customer, company_id="c1"), customer))

    assert rows[0]["standardPriceMinor"] == 1000
    for field in ("b2cPrice", "b2cPriceMinor", "b2cTiers"):
        assert field not in rows[0]


def test_b2b_catalog_hides_inactive_and_b2c_only_products():
    database = AsyncDatabase("product_b2b_visibility")
    admin = scoped(database)
    seed_product(admin, "visible")
    seed_product(admin, "inactive", active=False)
    seed_product(admin, "b2c-only", b2bAvailable=False)
    sales = scoped(database, role="sales", actor="sales")
    rows = run(products.get_products(principal(sales), sales))
    assert [row["id"] for row in rows] == ["visible"]


def test_public_shop_exposes_only_b2c_fields_and_products():
    database = AsyncDatabase("product_b2c_visibility")
    access = scoped(database)
    seed_product(access, "public", taxRate=7)
    seed_product(access, "b2b-only", b2cAvailable=False)
    seed_product(access, "inactive", active=False)

    rows = run(shop.shop_products(access))

    assert [row["id"] for row in rows] == ["public"]
    assert rows[0]["taxRate"] == 7
    for field in (
        "standardPrice", "standardPriceMinor", "cost", "costMinor", "salesFloor",
        "salesFloorMinor", "absoluteFloor", "absoluteFloorMinor", "metadata",
    ):
        assert field not in rows[0]


def test_arbitrary_product_category_tax_and_visibility(monkeypatch):
    database = AsyncDatabase("universal_products")
    admin = scoped(database)
    category = run(products.create_product_category(ProductCategoryIn(name="Werkzeug"), principal(admin), admin))
    async def sequence(_name): return 42
    monkeypatch.setattr(products, "next_seq", sequence)
    body = ProductIn(
        sku="TOOL-42", name="Drehmomentschlüssel", categoryId=category["id"], unit="piece",
        packagingUnit="case", packageQuantity=1, b2bAvailable=True, b2cAvailable=False,
        standardPrice=100, salesFloor=90, absoluteFloor=80, cost=50, taxRate=19,
    )
    created = run(products.create_product(body, principal(admin), admin))
    assert (created["categoryId"], created["unit"], created["taxRate"], created["b2cAvailable"]) == (category["id"], "piece", 19, False)


def test_product_update_does_not_erase_unsent_admin_metadata():
    database = AsyncDatabase("product_metadata_preservation")
    admin = scoped(database)
    seed_product(admin, metadata={"compatibility": "legacy"})
    body = ProductIn(
        name="Updated Item", standardPrice=10, salesFloor=9, absoluteFloor=8,
        cost=5, taxRate=19,
    )
    updated = run(products.update_product("p1", body, principal(admin), admin))
    assert updated["metadata"] == {"compatibility": "legacy"}


def test_sales_low_customer_price_creates_approval_without_disclosing_floor():
    database = AsyncDatabase("price_approval")
    sales = scoped(database, role="sales", actor="sales")
    run(sales.companies.insert_one({"id": "c1", "name": "Customer", "assignedSalesRepId": "sales", "active": True}))
    seed_product(sales)
    result = run(pricing.upsert_customer_price(CustomerPriceIn(companyId="c1", productId="p1", price=8.5), principal(sales), sales))
    assert result == {"ok": False, "approvalRequired": True, "message": "Dieser Preis benötigt eine Freigabe durch einen Administrator."}
    approval = database.raw.price_approvals.find_one({"tenantId": "tenant-a"})
    assert approval["requestedPriceMinor"] == 850
    assert "floor" not in str(result).lower()
    assert database.raw.customer_prices.count_documents({}) == 0


def test_customer_price_conditions_snapshot_excludes_internal_note_for_sales():
    database = AsyncDatabase("price_conditions")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    seed_product(admin)
    result = run(pricing.upsert_customer_price(CustomerPriceIn(
        companyId="c1", productId="p1", price=9.5, deliveryTerms="frei Haus",
        paymentTermDays=30, internalNote="admin only",
    ), principal(admin), admin))
    assert result["internalNote"] == "admin only"
    stored = database.raw.customer_prices.find_one({"tenantId": "tenant-a"})
    assert stored["paymentTermDays"] == 30
    sales = scoped(database, role="sales", actor="sales")
    run(admin.companies.update_one({"id": "c1"}, {"$set": {"assignedSalesRepId": "sales"}}))
    visible = run(companies.get_company_prices("c1", principal(sales), sales))
    assert "internalNote" not in visible[0]


def test_direct_cash_order_and_invoice_keep_authoritative_price_and_tax(monkeypatch):
    database = AsyncDatabase("direct_cash")
    sales = scoped(database, role="sales", actor="sales")
    run(sales.companies.insert_one({"id": "c1", "name": "Customer", "assignedSalesRepId": "sales", "active": True}))
    seed_product(sales)
    async def order_seq(_name): return 1
    async def invoice_seq(_name): return 2
    monkeypatch.setattr(orders, "next_seq", order_seq)
    monkeypatch.setattr(billing, "next_seq", invoice_seq)
    created = run(orders.create_order(OrderCreate(
        companyId="c1", items=[{"productId": "p1", "qty": 2}], paymentMethod="cash", createInvoice=True,
    ), principal(sales), sales))
    assert created["items"][0]["unitPriceMinor"] == 1000
    assert created["invoice"]["lineItems"][0]["taxRate"] == 19
    assert created["invoice"]["paymentMethod"] == "cash"


def test_sales_foreign_customer_direct_order_is_blocked(monkeypatch):
    database = AsyncDatabase("direct_order_scope")
    sales = scoped(database, role="sales", actor="sales")
    run(sales.companies.insert_one({"id": "foreign", "name": "Foreign", "assignedSalesRepId": "other", "active": True}))
    seed_product(sales)
    async def sequence(_name): return 1
    monkeypatch.setattr(orders, "next_seq", sequence)
    with pytest.raises(HTTPException) as denied:
        run(orders.create_order(OrderCreate(companyId="foreign", items=[{"productId": "p1", "qty": 1}], paymentMethod="cash"), principal(sales), sales))
    assert denied.value.status_code == 403


def test_partial_and_full_invoice_payments_are_tenant_scoped():
    database = AsyncDatabase("invoice_payments")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(admin.orders.insert_one({"id": "B-1", "companyId": "c1", "currency": "EUR", "items": []}))
    invoice = {"id": "RE-1", "companyId": "c1", "orderId": "B-1", "currency": "EUR", "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen"}
    run(admin.invoices.insert_one(invoice))
    user = principal(admin)
    first = run(invoices.record_invoice_payment("RE-1", PaymentRecordIn(amount=4, method="cash"), user, admin))
    assert first["status"] == "Teilweise bezahlt"
    current = run(admin.invoices.find_one({"id": "RE-1"}))
    second = run(invoices._record_payment(user, admin, current, PaymentRecordIn(amount=6, method="bank_transfer")))
    assert second["status"] == "Bezahlt"
    stored = database.raw.invoices.find_one({"tenantId": "tenant-a", "id": "RE-1"})
    assert len(stored["paymentRecords"]) == 2
    assert sum(payment["amountMinor"] for payment in stored["paymentRecords"]) == 1000
    public = run(invoices.get_invoices(user, admin))[0]
    assert all("createdBy" not in payment for payment in public["paymentRecords"])


def test_parallel_invoice_payments_cannot_apply_the_same_open_balance_twice():
    database = AsyncDatabase("invoice_payment_concurrency")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "active": True}))
    run(admin.orders.insert_one({"id": "B-1", "companyId": "c1", "currency": "EUR", "items": []}))
    invoice = {"id": "RE-1", "companyId": "c1", "orderId": "B-1", "currency": "EUR", "amountMinor": 1000, "amount": 10, "paidAmountMinor": 0, "status": "Offen"}
    run(admin.invoices.insert_one(invoice))
    user = principal(admin)

    first = run(invoices._record_payment(user, admin, invoice, PaymentRecordIn(amount=6, method="cash")))
    assert first["paidAmountMinor"] == 600
    with pytest.raises(HTTPException) as conflict:
        run(invoices._record_payment(user, admin, invoice, PaymentRecordIn(amount=6, method="cash")))
    assert conflict.value.status_code == 409

    stored = database.raw.invoices.find_one({"tenantId": "tenant-a", "id": "RE-1"})
    assert stored["paidAmountMinor"] == 600
    assert len(stored["paymentRecords"]) == 1


def test_public_equipment_financing_request_has_no_invented_financing_terms():
    database = AsyncDatabase("equipment_request")
    public = scoped(database)
    seed_product(public)
    result = run(products.create_equipment_financing_request(
        EquipmentFinancingRequestIn(productId="p1", name="Buyer", email="buyer@example.test"), public,
    ))
    stored = database.raw.equipment_requests.find_one({"tenantId": "tenant-a", "id": result["id"]})
    assert stored["requestType"] == "financing"
    assert not any(key in stored for key in ("monthlyRate", "interestRate", "termMonths"))


def test_machine_catalog_can_reference_tenant_scoped_universal_product():
    database = AsyncDatabase("machine_product_relation")
    admin = scoped(database)
    seed_product(admin, "equipment-product")
    created = run(machines.create_machine(
        MachineIn(productId="equipment-product", name="Universal Equipment", price=1000, taxRate=19),
        principal(admin), admin,
    ))
    assert created["productId"] == "equipment-product"

    foreign = scoped(database, tenant="tenant-b")
    with pytest.raises(HTTPException) as missing:
        run(machines.create_machine(
            MachineIn(productId="equipment-product", name="Foreign Equipment", price=1000, taxRate=19),
            principal(foreign), foreign,
        ))
    assert missing.value.status_code == 404


def test_dashboard_profitability_is_admin_only():
    database = AsyncDatabase("dashboard_privacy")
    admin = scoped(database)
    run(admin.companies.insert_one({"id": "c1", "name": "Customer", "assignedSalesRepId": "sales", "active": True, "createdAt": datetime.now(timezone.utc).isoformat()}))
    now = datetime.now(timezone.utc).isoformat()
    run(admin.orders.insert_one({"id": "B-1", "companyId": "c1", "createdAt": now, "items": [{"qty": 1, "price": 10, "lineTotalMinor": 1000, "costMinor": 500}], "netTotalMinor": 1000, "salesAttribution": {"salesRepId": "sales", "actorName": "Sales"}}))
    admin_result = run(dashboard.dashboard(principal(admin), admin))
    assert admin_result["topSalesReps"][0]["margin"] == 5
    sales = scoped(database, role="sales", actor="sales")
    sales_result = run(dashboard.dashboard(principal(sales), sales))
    assert sales_result["topSalesReps"] is None
    assert "margin" not in str(sales_result["topCustomers"])
