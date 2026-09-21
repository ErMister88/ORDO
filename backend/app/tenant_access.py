"""Small explicit tenant-scoped persistence boundary for work package 3.4."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .tenancy import TenantContext


TENANT_SCOPED_BUSINESS_COLLECTIONS = frozenset({
    "companies",
    "customer_prices",
    "price_history",
    "products",
    "uploads",
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

    async def delete_one(self, query: Mapping[str, Any], *args, **kwargs):
        return await self._collection.delete_one(self._filter(query), *args, **kwargs)


class TenantBusinessAccess:
    """Named tenant-scoped collections; deliberately not a generic repository."""

    def __init__(self, database, context: TenantContext) -> None:
        self.context = context
        self.companies = TenantScopedCollection(database, "companies", context)
        self.products = TenantScopedCollection(database, "products", context)
        self.customer_prices = TenantScopedCollection(database, "customer_prices", context)
        self.price_history = TenantScopedCollection(database, "price_history", context)
        self.uploads = TenantScopedCollection(database, "uploads", context)
