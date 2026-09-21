"""Add the S&S tenant and additive tenant-aware index structures."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from ..models import Migration, MigrationPlan, MigrationStateError, checksum_file


TENANT_COLLECTION = "tenants"
SS_TENANT_ID = "tnt_ss_0001"
SS_TENANT_SLUG = "ss-coffee-and-more"
TENANT_SCHEMA_VERSION = 1

# This migration snapshot is intentionally self-contained. Changing a shared
# serializer later must not silently change an already-versioned migration.
SS_TENANT_FIELDS: Mapping[str, Any] = {
    "id": SS_TENANT_ID,
    "slug": SS_TENANT_SLUG,
    "displayName": "S&S coffee and more",
    "legalName": None,
    "status": "active",
    "defaultCurrency": "EUR",
    "defaultLocale": "de-DE",
    "timezone": "Europe/Berlin",
    "schemaVersion": TENANT_SCHEMA_VERSION,
}


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool
    partial_filter: Mapping[str, Any] | None = None


def _strings(*fields: str) -> dict[str, dict[str, str]]:
    return {field: {"$type": "string"} for field in fields}


INDEX_SPECS: tuple[IndexSpec, ...] = (
    IndexSpec(TENANT_COLLECTION, "uniq_tenants_id", (("id", ASCENDING),), True),
    IndexSpec(TENANT_COLLECTION, "uniq_tenants_slug", (("slug", ASCENDING),), True),
    IndexSpec("companies", "uniq_tenant_company_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("products", "uniq_tenant_product_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("customer_prices", "uniq_tenant_customer_price", (("tenantId", ASCENDING), ("companyId", ASCENDING), ("productId", ASCENDING)), True, _strings("tenantId", "companyId", "productId")),
    IndexSpec("price_history", "idx_tenant_price_history", (("tenantId", ASCENDING),), False, _strings("tenantId")),
    IndexSpec("offers", "uniq_tenant_offer_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("orders", "uniq_tenant_order_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("invoices", "uniq_tenant_invoice_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("contracts", "uniq_tenant_contract_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("machines", "uniq_tenant_machine_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("machine_requests", "uniq_tenant_machine_request_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("subscriptions", "uniq_tenant_subscription_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("shop_orders", "uniq_tenant_shop_order_id", (("tenantId", ASCENDING), ("id", ASCENDING)), True, _strings("tenantId", "id")),
    IndexSpec("newsletter", "uniq_tenant_newsletter_email", (("tenantId", ASCENDING), ("email", ASCENDING)), True, _strings("tenantId", "email")),
    IndexSpec("settings", "uniq_tenant_setting_key", (("tenantId", ASCENDING), ("key", ASCENDING)), True, _strings("tenantId", "key")),
    IndexSpec("uploads", "uniq_tenant_storage_path", (("tenantId", ASCENDING), ("storagePath", ASCENDING)), True, _strings("tenantId", "storagePath")),
    IndexSpec("audit_log", "idx_tenant_audit_log", (("tenantId", ASCENDING),), False, _strings("tenantId")),
    IndexSpec("push_registrations", "uniq_tenant_push_user", (("tenantId", ASCENDING), ("userId", ASCENDING)), True, _strings("tenantId", "userId")),
)


def _is_utc_datetime(value: Any) -> bool:
    return (
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() == timedelta(0)
    )


def _validate_canonical_tenant(document: Mapping[str, Any]) -> None:
    expected_fields = {*SS_TENANT_FIELDS, "createdAt", "updatedAt", "_id"}
    unexpected = set(document) - expected_fields
    if unexpected:
        raise MigrationStateError(
            "S&S tenant document contains unexpected fields: "
            + ", ".join(sorted(unexpected))
        )
    mismatched = [
        field
        for field, expected in SS_TENANT_FIELDS.items()
        if field not in document
        or type(document[field]) is not type(expected)
        or document[field] != expected
    ]
    if mismatched:
        raise MigrationStateError(
            "S&S tenant document is incomplete or conflicts in fields: "
            + ", ".join(sorted(mismatched))
        )
    created_at = document.get("createdAt")
    updated_at = document.get("updatedAt")
    if not _is_utc_datetime(created_at) or not _is_utc_datetime(updated_at):
        raise MigrationStateError("S&S tenant timestamps must be BSON UTC datetimes")
    if updated_at < created_at:
        raise MigrationStateError("S&S tenant updatedAt precedes createdAt")


def _tenant_state(database) -> str:
    matches = list(database[TENANT_COLLECTION].find({
        "$or": [
            {"id": SS_TENANT_ID},
            {"slug": SS_TENANT_SLUG},
        ]
    }))
    if not matches:
        return "pending"
    if len(matches) != 1:
        raise MigrationStateError("Multiple tenant documents conflict with the S&S identity")
    document = matches[0]
    if document.get("id") != SS_TENANT_ID or document.get("slug") != SS_TENANT_SLUG:
        raise MigrationStateError("S&S tenant id or slug is already used by another tenant")
    _validate_canonical_tenant(document)
    return "present"


def _normalized_keys(index: Mapping[str, Any]) -> tuple[tuple[str, int], ...]:
    return tuple((str(field), int(direction)) for field, direction in index.get("key", []))


def _index_matches(index: Mapping[str, Any], spec: IndexSpec) -> bool:
    return (
        _normalized_keys(index) == spec.keys
        and bool(index.get("unique", False)) is spec.unique
        and index.get("partialFilterExpression") == spec.partial_filter
    )


def _index_state(database, spec: IndexSpec) -> str:
    information = database[spec.collection].index_information()
    named = information.get(spec.name)
    if named is not None:
        if not _index_matches(named, spec):
            raise MigrationStateError(
                f"Index {spec.collection}.{spec.name} exists with another definition"
            )
        return "unchanged"
    for existing_name, existing in information.items():
        if existing_name == "_id_":
            continue
        if _normalized_keys(existing) == spec.keys:
            raise MigrationStateError(
                f"Index keys for {spec.collection}.{spec.name} already exist as {existing_name}"
            )
    return "create"


def _validate_index_candidates(database, spec: IndexSpec) -> int:
    query = {} if spec.collection == TENANT_COLLECTION else {"tenantId": {"$exists": True}}
    documents = list(database[spec.collection].find(query))
    seen: set[tuple[Any, ...]] = set()
    for document in documents:
        values = []
        for field, _direction in spec.keys:
            value = document.get(field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise MigrationStateError(
                    f"{spec.collection} contains an invalid {field} for index {spec.name}"
                )
            values.append(value)
        key = tuple(values)
        if spec.unique and key in seen:
            raise MigrationStateError(
                f"{spec.collection} contains duplicate values for index {spec.name}"
            )
        seen.add(key)
    return len(documents)


def _index_plan(database) -> tuple[list[dict[str, Any]], dict[str, int]]:
    actions = []
    tenant_document_counts: dict[str, int] = {}
    for spec in INDEX_SPECS:
        state = _index_state(database, spec)
        tenant_document_counts[spec.collection] = _validate_index_candidates(database, spec)
        actions.append({
            "collection": spec.collection,
            "name": spec.name,
            "keys": list(spec.keys),
            "unique": spec.unique,
            "partialFilterExpression": dict(spec.partial_filter) if spec.partial_filter else None,
            "action": state,
        })
    return actions, tenant_document_counts


def _analyze(database) -> dict[str, Any]:
    tenant_state = _tenant_state(database)
    index_actions, tenant_document_counts = _index_plan(database)
    return {
        "tenantState": tenant_state,
        "tenantDocumentsToInsert": 1 if tenant_state == "pending" else 0,
        "businessDocumentsModified": 0,
        "indexActions": index_actions,
        "indexesToCreate": sum(action["action"] == "create" for action in index_actions),
        "indexesAlreadyCorrect": sum(action["action"] == "unchanged" for action in index_actions),
        "tenantScopedDocumentsObserved": tenant_document_counts,
    }


def inspect(database) -> MigrationPlan:
    analysis = _analyze(database)
    return MigrationPlan(
        preconditions=(
            "S&S tenant identity is unused or contains the exact canonical tenant document",
            "Existing indexes do not conflict with additive tenant index definitions",
            "Existing tenantId documents satisfy the indexed key shapes and unique constraints",
            "Legacy business documents without tenantId remain unchanged",
        ),
        expected_changes=analysis,
    )


def _create_index(database, spec: IndexSpec) -> None:
    options: dict[str, Any] = {"name": spec.name, "unique": spec.unique}
    if spec.partial_filter is not None:
        options["partialFilterExpression"] = dict(spec.partial_filter)
    database[spec.collection].create_index(list(spec.keys), **options)
    if _index_state(database, spec) != "unchanged":
        raise MigrationStateError(f"Index {spec.collection}.{spec.name} was not persisted")


def _bootstrap_tenant(database) -> bool:
    if _tenant_state(database) == "present":
        return False
    now = datetime.now(timezone.utc)
    document = {
        **SS_TENANT_FIELDS,
        "createdAt": now,
        "updatedAt": now,
    }
    try:
        result = database[TENANT_COLLECTION].update_one(
            {"id": SS_TENANT_ID, "slug": SS_TENANT_SLUG},
            {"$setOnInsert": document},
            upsert=True,
        )
    except DuplicateKeyError:
        # A concurrent external write cannot be trusted. Re-read and accept it
        # only if it produced the exact canonical document.
        if _tenant_state(database) == "present":
            return False
        raise
    if _tenant_state(database) != "present":
        raise MigrationStateError("S&S tenant bootstrap did not persist the canonical document")
    return result.upserted_id is not None


def apply(database, context) -> dict[str, Any]:
    analysis = _analyze(database)
    created = 0
    for spec, action in zip(INDEX_SPECS, analysis["indexActions"]):
        context.checkpoint()
        if action["action"] == "create":
            _create_index(database, spec)
            created += 1
        context.checkpoint()

    context.checkpoint()
    inserted = _bootstrap_tenant(database)
    context.checkpoint()
    return {
        "businessDocumentsModified": 0,
        "tenantDocumentsInserted": int(inserted),
        "indexesCreated": created,
        "indexesAlreadyCorrect": len(INDEX_SPECS) - created,
    }


MIGRATION = Migration(
    version=2,
    name="tenant_schema_expansion",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
