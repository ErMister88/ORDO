"""Bridge proven ORDO single-tenant legacy data into the canonical S&S tenant."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping

from ..models import Migration, MigrationPlan, MigrationStateError, checksum_file


SS_TENANT_ID = "tnt_ss_0001"
SS_TENANT_SLUG = "ss-coffee-and-more"

BUSINESS_COLLECTIONS = (
    "audit_log",
    "companies",
    "contracts",
    "customer_prices",
    "invoices",
    "machine_requests",
    "machines",
    "newsletter",
    "offers",
    "orders",
    "price_history",
    "pricing_promotions",
    "products",
    "push_registrations",
    "settings",
    "shop_orders",
    "subscriptions",
    "uploads",
)
GLOBAL_COLLECTIONS = {
    "auth_rate_limits",
    "counters",
    "password_resets",
    "schema_migration_lock",
    "schema_migrations",
    "tenant_memberships",
    "tenants",
    "users",
}
KNOWN_COLLECTIONS = set(BUSINESS_COLLECTIONS) | GLOBAL_COLLECTIONS

# These events belong to global identity/session activity by design. Any other
# tenantless audit event is a legacy S&S business event and is backfilled.
GLOBAL_AUDIT_ACTIONS = {"login"}
GLOBAL_AUDIT_PREFIXES = ("identity.",)

UNIQUE_KEYS: Mapping[str, tuple[str, ...]] = {
    "companies": ("id",),
    "contracts": ("id",),
    "customer_prices": ("companyId", "productId"),
    "invoices": ("id",),
    "machine_requests": ("id",),
    "machines": ("id",),
    "newsletter": ("email",),
    "offers": ("id",),
    "orders": ("id",),
    "pricing_promotions": ("id",),
    "products": ("id",),
    "push_registrations": ("userId",),
    "settings": ("key",),
    "shop_orders": ("id",),
    "subscriptions": ("id",),
    "uploads": ("storagePath",),
}


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _is_global_audit(document: Mapping[str, Any]) -> bool:
    action = document.get("action")
    return isinstance(action, str) and (
        action in GLOBAL_AUDIT_ACTIONS
        or any(action.startswith(prefix) for prefix in GLOBAL_AUDIT_PREFIXES)
    )


def _canonical_tenant(database) -> Mapping[str, Any]:
    matches = list(database["tenants"].find({
        "$or": [{"id": SS_TENANT_ID}, {"slug": SS_TENANT_SLUG}],
    }))
    if len(matches) != 1:
        raise MigrationStateError(
            "Canonical S&S tenant must exist exactly once before legacy backfill"
        )
    tenant = matches[0]
    if (
        tenant.get("id") != SS_TENANT_ID
        or tenant.get("slug") != SS_TENANT_SLUG
        or tenant.get("status") != "active"
    ):
        raise MigrationStateError("Canonical S&S tenant is conflicting or inactive")
    return tenant


def _documents(database, collection: str) -> list[dict[str, Any]]:
    return [dict(document) for document in database[collection].find({})]


def _tenantless_business_documents(
    database,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    pending: dict[str, list[dict[str, Any]]] = {}
    global_audit_count = 0
    scoped_counts: dict[str, int] = {}
    for collection in BUSINESS_COLLECTIONS:
        rows = _documents(database, collection)
        candidates = []
        scoped = 0
        for document in rows:
            if "tenantId" not in document:
                if collection == "audit_log" and _is_global_audit(document):
                    global_audit_count += 1
                    continue
                candidates.append(document)
            else:
                tenant_id = document["tenantId"]
                if not _nonempty_string(tenant_id):
                    raise MigrationStateError(
                        f"{collection} contains an invalid explicit tenantId"
                    )
                scoped += 1
        pending[collection] = candidates
        scoped_counts[collection] = scoped
    return pending, {
        "globalAuditDocumentsPreserved": global_audit_count,
        "tenantScopedDocumentsObserved": sum(scoped_counts.values()),
    }


def _require_baseline(database) -> Mapping[str, Any]:
    baseline = database["schema_migrations"].find_one({
        "version": 1,
        "status": "completed",
    })
    inventory = (baseline or {}).get("resultSummary", {}).get("baselineInventory")
    if not isinstance(inventory, Mapping):
        raise MigrationStateError(
            "Legacy backfill requires the completed baseline inventory"
        )
    if not isinstance(inventory.get("documentCounts"), Mapping):
        raise MigrationStateError("Baseline inventory has no document counts")
    return inventory


def _validate_known_legacy_source(database, pending_count: int) -> None:
    if pending_count == 0:
        return
    inventory = _require_baseline(database)
    unknown = []
    for collection in database.list_collection_names():
        if collection not in KNOWN_COLLECTIONS and database[collection].count_documents({}):
            unknown.append(collection)
    if unknown:
        raise MigrationStateError(
            "Legacy ownership is ambiguous because unknown populated collections exist: "
            + ", ".join(sorted(unknown))
        )
    if int(inventory.get("totalBusinessDocuments", -1)) < 1:
        raise MigrationStateError(
            "Legacy ownership cannot be proven from an empty baseline inventory"
        )
    counts = inventory["documentCounts"]
    if any(int(counts.get(name, 0)) < 1 for name in ("companies", "products", "users")):
        raise MigrationStateError(
            "Legacy ownership requires companies, products and users in the baseline inventory"
        )
    tenants = list(database["tenants"].find({}))
    if len(tenants) != 1 or tenants[0].get("id") != SS_TENANT_ID:
        raise MigrationStateError(
            "Tenantless business data cannot be assigned when multiple tenants exist"
        )


def _validate_global_identity(database, *, legacy_backfill: bool) -> dict[str, int]:
    users = _documents(database, "users")
    seen_ids: set[str] = set()
    role_counts: defaultdict[str, int] = defaultdict(int)
    allowed_roles = {"admin", "sales", "customer", "shopuser"}
    for user in users:
        if "tenantId" in user:
            raise MigrationStateError("Global users must not contain tenantId")
        user_id = user.get("id")
        if not _nonempty_string(user_id) or user_id in seen_ids:
            raise MigrationStateError("Global users contain invalid or duplicate ids")
        seen_ids.add(user_id)
        role = user.get("role")
        if legacy_backfill and role not in allowed_roles:
            raise MigrationStateError(
                f"Legacy user {user_id!r} has an ambiguous tenant role"
            )
        if role in allowed_roles:
            role_counts[role] += 1
        if role == "customer" and not _nonempty_string(user.get("companyId")):
            raise MigrationStateError(
                f"Legacy customer {user_id!r} has no valid companyId"
            )
    return dict(role_counts)


def _validate_global_collections(database) -> None:
    for collection in ("auth_rate_limits", "counters", "password_resets"):
        if database[collection].count_documents({"tenantId": {"$exists": True}}):
            raise MigrationStateError(
                f"Global collection {collection} must not contain tenantId"
            )


def _effective_tenant(document: Mapping[str, Any]) -> str:
    if "tenantId" not in document:
        return SS_TENANT_ID
    tenant_id = document["tenantId"]
    if not _nonempty_string(tenant_id):
        raise MigrationStateError("Business document has an invalid tenantId")
    return tenant_id


def _require_reference(
    collection: str,
    document: Mapping[str, Any],
    field: str,
    targets: set[tuple[str, str]],
    *,
    optional: bool = False,
) -> None:
    value = document.get(field)
    if value is None and optional:
        return
    tenant_id = _effective_tenant(document)
    if not _nonempty_string(value) or (tenant_id, value) not in targets:
        raise MigrationStateError(
            f"{collection} contains an invalid or cross-tenant {field} reference"
        )


def _validate_unique_keys(all_documents: Mapping[str, list[dict[str, Any]]]) -> None:
    for collection, fields in UNIQUE_KEYS.items():
        seen: set[tuple[Any, ...]] = set()
        for document in all_documents[collection]:
            values = tuple(document.get(field) for field in fields)
            if not all(_nonempty_string(value) for value in values):
                raise MigrationStateError(
                    f"{collection} has invalid values for tenant unique key"
                )
            key = (_effective_tenant(document), *values)
            if key in seen:
                raise MigrationStateError(
                    f"{collection} has duplicate values for tenant unique key"
                )
            seen.add(key)


def _validate_references(database, all_documents: Mapping[str, list[dict[str, Any]]]) -> None:
    company_ids = {
        (_effective_tenant(document), document["id"])
        for document in all_documents["companies"]
    }
    product_ids = {
        (_effective_tenant(document), document["id"])
        for document in all_documents["products"]
    }
    order_ids = {
        (_effective_tenant(document), document["id"])
        for document in all_documents["orders"]
    }
    machine_ids = {
        (_effective_tenant(document), document["id"])
        for document in all_documents["machines"]
    }
    user_ids = {document["id"] for document in _documents(database, "users")}

    for collection in ("customer_prices", "price_history", "pricing_promotions"):
        for document in all_documents[collection]:
            _require_reference(collection, document, "companyId", company_ids,
                               optional=collection == "pricing_promotions")
            _require_reference(collection, document, "productId", product_ids)
    for collection in ("contracts", "offers", "orders", "subscriptions", "invoices"):
        for document in all_documents[collection]:
            _require_reference(collection, document, "companyId", company_ids)
            if collection == "contracts":
                _require_reference(collection, document, "productId", product_ids,
                                   optional=True)
            if collection == "invoices":
                _require_reference(collection, document, "orderId", order_ids, optional=True)
            for item in document.get("items") or []:
                _require_reference(collection, {**item, "tenantId": _effective_tenant(document)},
                                   "productId", product_ids)
    for document in all_documents["machine_requests"]:
        _require_reference("machine_requests", document, "machineId", machine_ids)
        customer = document.get("customer") or {}
        if customer.get("companyId") is not None:
            _require_reference(
                "machine_requests",
                {**customer, "tenantId": _effective_tenant(document)},
                "companyId",
                company_ids,
            )
        terms = document.get("terms") or {}
        if terms.get("productId") is not None:
            _require_reference(
                "machine_requests",
                {**terms, "tenantId": _effective_tenant(document)},
                "productId",
                product_ids,
            )
    for collection in ("shop_orders",):
        for document in all_documents[collection]:
            for item in document.get("items") or []:
                _require_reference(collection, {**item, "tenantId": _effective_tenant(document)},
                                   "productId", product_ids)
    for document in all_documents["push_registrations"]:
        user_id = document.get("userId")
        if not _nonempty_string(user_id) or (
            user_id not in user_ids and not user_id.startswith("anon:")
        ):
            raise MigrationStateError(
                "push_registrations contains an unknown global user reference"
            )


def _validate_memberships(database) -> None:
    for document in database["tenant_memberships"].find({}):
        if not _nonempty_string(document.get("tenantId")):
            raise MigrationStateError(
                "Existing tenant membership without tenantId is ambiguous"
            )


def _analyze(database) -> dict[str, Any]:
    _canonical_tenant(database)
    pending, observations = _tenantless_business_documents(database)
    pending_counts = {name: len(documents) for name, documents in pending.items()}
    pending_total = sum(pending_counts.values())
    _validate_known_legacy_source(database, pending_total)
    role_counts = _validate_global_identity(database, legacy_backfill=bool(pending_total))
    _validate_global_collections(database)
    _validate_memberships(database)

    all_documents = {
        collection: _documents(database, collection)
        for collection in BUSINESS_COLLECTIONS
    }
    for collection, documents in all_documents.items():
        for document in documents:
            tenant_id = document.get("tenantId")
            if tenant_id is not None and not database["tenants"].find_one({"id": tenant_id}):
                raise MigrationStateError(
                    f"{collection} references an unknown tenant"
                )
            if tenant_id is not None and tenant_id != SS_TENANT_ID:
                # A fully tenant-scoped multi-tenant database is valid, but a mixed
                # legacy/current source cannot prove who owns tenantless rows.
                if pending_total:
                    raise MigrationStateError(
                        "Tenantless business data is ambiguous beside another tenant"
                    )
    _validate_unique_keys(all_documents)
    _validate_references(database, all_documents)
    return {
        "canonicalTenantId": SS_TENANT_ID,
        "documentsToBackfill": pending_counts,
        "totalDocumentsToBackfill": pending_total,
        "globalUsersModified": 0,
        "legacyUserRolesObserved": role_counts,
        **observations,
    }


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Migration 1 baseline and migration 2 canonical S&S tenant are complete",
            "Tenantless rows belong unambiguously to the known ORDO S&S legacy inventory",
            "Global users have stable identities and unambiguous legacy roles",
            "Prospective tenant keys are unique and business references remain tenant-local",
            "Global identity/session data and explicit global audit events remain global",
        ),
        expected_changes=_analyze(database),
    )


def _verify(database) -> dict[str, Any]:
    analysis = _analyze(database)
    if analysis["totalDocumentsToBackfill"]:
        raise MigrationStateError("Legacy tenant backfill left tenantless business documents")
    return analysis


def apply(database, context) -> dict[str, Any]:
    analysis = _analyze(database)
    changed: dict[str, int] = {}
    for collection in BUSINESS_COLLECTIONS:
        count = 0
        for document in list(database[collection].find({"tenantId": {"$exists": False}})):
            if collection == "audit_log" and _is_global_audit(document):
                continue
            context.checkpoint()
            result = database[collection].update_one(
                {"_id": document["_id"], "tenantId": {"$exists": False}},
                {"$set": {"tenantId": SS_TENANT_ID}},
            )
            if result.matched_count != 1:
                current = database[collection].find_one({"_id": document["_id"]})
                if current is None or current.get("tenantId") != SS_TENANT_ID:
                    raise MigrationStateError(
                        f"{collection} changed concurrently during legacy tenant backfill"
                    )
            else:
                count += 1
            context.checkpoint()
        changed[collection] = count
    verified = _verify(database)
    return {
        "documentsBackfilled": changed,
        "totalDocumentsBackfilled": sum(changed.values()),
        "globalUsersModified": 0,
        "globalAuditDocumentsPreserved": verified["globalAuditDocumentsPreserved"],
        "plannedDocuments": analysis["totalDocumentsToBackfill"],
    }


MIGRATION = Migration(
    version=6,
    name="legacy_tenant_bridge",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
    depends_on=(2,),
)
