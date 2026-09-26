"""Small explicit tenant-scoped persistence boundary for business data."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .tenancy import TenantContext


TENANT_SCOPED_BUSINESS_COLLECTIONS = frozenset({
    "audit_log",
    "background_jobs",
    "companies",
    "contracts",
    "customer_activities",
    "customer_addresses",
    "customer_contacts",
    "customer_prices",
    "customer_tasks",
    "customer_types",
    "customer_tags",
    "business_brands",
    "equipment_requests",
    "email_outbox",
    "invoices",
    "idempotency_records",
    "machine_requests",
    "machines",
    "newsletter",
    "notifications",
    "offers",
    "orders",
    "payment_provider_events",
    "price_history",
    "price_approvals",
    "pricing_promotions",
    "product_categories",
    "products",
    "push_registrations",
    "settings",
    "shop_orders",
    "shop_collections",
    "subscriptions",
    "reconciliation_runs",
    "technical_errors",
    "tenant_memberships",
    "uploads",
    "worker_heartbeats",
})


class TenantScopeViolation(ValueError):
    """Raised when a caller attempts to influence persisted tenant ownership."""


def _tenant_values(value: Any):
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if key == "tenantId":
                yield nested
            yield from _tenant_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _tenant_values(nested)


def _validate_filter_tenant(query: Mapping[str, Any], tenant_id: str) -> None:
    for value in _tenant_values(query):
        if value != tenant_id:
            raise TenantScopeViolation("Conflicting tenantId in tenant-scoped filter")


def _validate_update(update: Mapping[str, Any]) -> None:
    if not update or any(not str(operator).startswith("$") for operator in update):
        raise TenantScopeViolation("Tenant-scoped updates must use MongoDB update operators")
    for operator, changes in update.items():
        if not isinstance(changes, Mapping):
            raise TenantScopeViolation(f"Invalid tenant-scoped update operator {operator}")
        for field, value in changes.items():
            if field == "tenantId" or field.startswith("tenantId."):
                raise TenantScopeViolation("tenantId is immutable")
            if operator == "$rename" and (
                value == "tenantId"
                or isinstance(value, str)
                and value.startswith("tenantId.")
            ):
                raise TenantScopeViolation("tenantId is immutable")


class TenantScopedCollection:
    """Expose only the MongoDB operations needed by the converted code paths."""

    def __init__(self, database, name: str, context: TenantContext) -> None:
        if name not in TENANT_SCOPED_BUSINESS_COLLECTIONS:
            raise ValueError(f"Collection {name!r} is not approved for tenant-scoped access")
        self._collection = database[name]
        self._tenant_id = context.tenant_id

    def _filter(self, query: Mapping[str, Any] | None = None) -> dict[str, Any]:
        query = dict(query or {})
        _validate_filter_tenant(query, self._tenant_id)
        return {"tenantId": self._tenant_id, **query}

    def find(self, query: Mapping[str, Any] | None = None, *args, **kwargs):
        return self._collection.find(self._filter(query), *args, **kwargs)

    def aggregate(self, pipeline, *args, **kwargs):
        """Run a read-only aggregation with an unremovable leading tenant match."""

        if not isinstance(pipeline, (list, tuple)):
            raise TenantScopeViolation("Aggregation pipeline must be a list")
        allowed = {"$match", "$sort", "$group", "$project", "$limit", "$skip", "$count", "$unwind", "$set", "$addFields"}
        safe_pipeline = []
        for stage in deepcopy(list(pipeline)):
            if not isinstance(stage, Mapping) or len(stage) != 1:
                raise TenantScopeViolation("Invalid tenant-scoped aggregation stage")
            operator = next(iter(stage))
            if operator not in allowed:
                raise TenantScopeViolation(f"Aggregation stage {operator} is not allowed")
            if operator == "$match":
                _validate_filter_tenant(stage[operator], self._tenant_id)
            safe_pipeline.append(stage)
        return self._collection.aggregate(
            [{"$match": {"tenantId": self._tenant_id}}, *safe_pipeline], *args, **kwargs
        )

    async def find_one(self, query: Mapping[str, Any], *args, **kwargs):
        return await self._collection.find_one(self._filter(query), *args, **kwargs)

    async def insert_one(self, document: Mapping[str, Any], *args, **kwargs):
        payload = deepcopy(dict(document))
        supplied = payload.get("tenantId")
        if supplied is not None and supplied != self._tenant_id:
            raise TenantScopeViolation("Conflicting tenantId in tenant-scoped document")
        payload["tenantId"] = self._tenant_id
        return await self._collection.insert_one(payload, *args, **kwargs)

    async def update_one(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *args,
        upsert: bool = False,
        **kwargs,
    ):
        payload = deepcopy(dict(update))
        _validate_update(payload)
        if upsert:
            set_on_insert = dict(payload.get("$setOnInsert", {}))
            set_on_insert["tenantId"] = self._tenant_id
            payload["$setOnInsert"] = set_on_insert
        return await self._collection.update_one(
            self._filter(query),
            payload,
            *args,
            upsert=upsert,
            **kwargs,
        )

    async def find_one_and_update(
        self,
        query: Mapping[str, Any],
        update: Mapping[str, Any],
        *args,
        **kwargs,
    ):
        payload = deepcopy(dict(update))
        _validate_update(payload)
        return await self._collection.find_one_and_update(
            self._filter(query), payload, *args, **kwargs
        )

    async def delete_one(self, query: Mapping[str, Any], *args, **kwargs):
        return await self._collection.delete_one(self._filter(query), *args, **kwargs)

    async def count_documents(self, query: Mapping[str, Any] | None = None, *args, **kwargs):
        return await self._collection.count_documents(self._filter(query), *args, **kwargs)


class TenantBusinessAccess:
    """Named tenant-scoped collections; deliberately not a generic repository."""

    def __init__(self, database, context: TenantContext) -> None:
        self.context = context
        self.audit_log = TenantScopedCollection(database, "audit_log", context)
        self.background_jobs = TenantScopedCollection(database, "background_jobs", context)
        self.companies = TenantScopedCollection(database, "companies", context)
        self.contracts = TenantScopedCollection(database, "contracts", context)
        self.customer_activities = TenantScopedCollection(database, "customer_activities", context)
        self.customer_addresses = TenantScopedCollection(database, "customer_addresses", context)
        self.customer_contacts = TenantScopedCollection(database, "customer_contacts", context)
        self.customer_tasks = TenantScopedCollection(database, "customer_tasks", context)
        self.customer_types = TenantScopedCollection(database, "customer_types", context)
        self.customer_tags = TenantScopedCollection(database, "customer_tags", context)
        self.business_brands = TenantScopedCollection(database, "business_brands", context)
        self.equipment_requests = TenantScopedCollection(database, "equipment_requests", context)
        self.email_outbox = TenantScopedCollection(database, "email_outbox", context)
        self.products = TenantScopedCollection(database, "products", context)
        self.product_categories = TenantScopedCollection(database, "product_categories", context)
        self.customer_prices = TenantScopedCollection(database, "customer_prices", context)
        self.invoices = TenantScopedCollection(database, "invoices", context)
        self.idempotency_records = TenantScopedCollection(database, "idempotency_records", context)
        self.machine_requests = TenantScopedCollection(database, "machine_requests", context)
        self.machines = TenantScopedCollection(database, "machines", context)
        self.newsletter = TenantScopedCollection(database, "newsletter", context)
        self.notifications = TenantScopedCollection(database, "notifications", context)
        self.offers = TenantScopedCollection(database, "offers", context)
        self.orders = TenantScopedCollection(database, "orders", context)
        self.payment_provider_events = TenantScopedCollection(database, "payment_provider_events", context)
        self.price_history = TenantScopedCollection(database, "price_history", context)
        self.price_approvals = TenantScopedCollection(database, "price_approvals", context)
        self.pricing_promotions = TenantScopedCollection(database, "pricing_promotions", context)
        self.push_registrations = TenantScopedCollection(database, "push_registrations", context)
        self.settings = TenantScopedCollection(database, "settings", context)
        self.shop_orders = TenantScopedCollection(database, "shop_orders", context)
        self.shop_collections = TenantScopedCollection(database, "shop_collections", context)
        self.subscriptions = TenantScopedCollection(database, "subscriptions", context)
        self.reconciliation_runs = TenantScopedCollection(database, "reconciliation_runs", context)
        self.technical_errors = TenantScopedCollection(database, "technical_errors", context)
        self.tenant_memberships = TenantScopedCollection(database, "tenant_memberships", context)
        self.uploads = TenantScopedCollection(database, "uploads", context)
        self.worker_heartbeats = TenantScopedCollection(database, "worker_heartbeats", context)
