"""Add exact minor-unit amounts and currency without inventing snapshots."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

from ..models import Migration, MigrationPlan, MigrationStateError, checksum_file


COLLECTION_FIELDS: Mapping[str, tuple[str, ...]] = {
    "products": ("standardPrice", "salesFloor", "absoluteFloor", "cost", "b2cPrice"),
    "customer_prices": ("price",),
    "price_history": ("oldPrice", "newPrice"),
    "contracts": ("price", "machineRate", "serviceRate"),
    "machines": ("price",),
    "machine_requests": ("machinePrice",),
    "settings": ("freeShippingThreshold", "shippingFee"),
    "shop_orders": ("subtotal", "shipping", "total", "discount", "taxTotal"),
    "invoices": ("net", "taxTotal", "amount"),
}
ITEM_COLLECTIONS = ("offers", "orders", "subscriptions", "shop_orders")
MAX_MINOR = 9_000_000_000_000_000


def _minor(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise MigrationStateError("Money source value is not numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MigrationStateError("Money source value is not numeric") from exc
    if not decimal.is_finite():
        raise MigrationStateError("Money source value is not finite")
    result = int((decimal * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if result < 0 or result > MAX_MINOR:
        raise MigrationStateError("Money source value is outside the supported range")
    return result


def _currency(database, document: Mapping[str, Any]) -> str:
    tenant_id = document.get("tenantId")
    tenant = database["tenants"].find_one({"id": tenant_id})
    if not tenant:
        raise MigrationStateError("Money document references no tenant")
    code = tenant.get("defaultCurrency")
    if not isinstance(code, str) or len(code) != 3 or not code.isalpha() or not code.isupper():
        raise MigrationStateError("Tenant has no valid default currency")
    existing = document.get("currency")
    if existing is not None and existing != code:
        raise MigrationStateError("Money document currency conflicts with its tenant")
    return code


def _set_minor(target: dict[str, Any], field: str) -> bool:
    if field not in target or target[field] is None:
        return False
    expected = _minor(target[field])
    minor_field = f"{field}Minor"
    existing = target.get(minor_field)
    if existing is not None and existing != expected:
        raise MigrationStateError(f"Existing {minor_field} conflicts with {field}")
    if existing is None:
        target[minor_field] = expected
        return True
    return False


def _line_minor(unit_minor: int, quantity: Any) -> int:
    try:
        qty = Decimal(str(quantity))
    except (InvalidOperation, ValueError) as exc:
        raise MigrationStateError("Commercial item quantity is not numeric") from exc
    if not qty.is_finite() or qty <= 0:
        raise MigrationStateError("Commercial item quantity is not positive and finite")
    result = int((Decimal(unit_minor) * qty).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if result < 0 or result > MAX_MINOR:
        raise MigrationStateError("Commercial item line total is outside the supported range")
    return result


def _expand_breakdown(result: dict[str, Any], field: str) -> bool:
    source = result.get(field)
    if source is None:
        return False
    if not isinstance(source, Mapping):
        raise MigrationStateError(f"{field} must be an object")
    expected = {str(rate): _minor(value) for rate, value in source.items()}
    minor_field = f"{field}Minor"
    existing = result.get(minor_field)
    if existing is not None and existing != expected:
        raise MigrationStateError(f"Existing {minor_field} conflicts with {field}")
    if existing is None:
        result[minor_field] = expected
        return True
    return False


def _expanded(database, collection: str, document: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    result = deepcopy(dict(document))
    currency = _currency(database, result)
    changed = result.get("currency") is None
    result["currency"] = currency
    for field in COLLECTION_FIELDS.get(collection, ()):
        changed = _set_minor(result, field) or changed

    if collection == "products":
        tiers = result.get("discountTiers") or []
        for tier in tiers:
            changed = _set_minor(tier, "price") or changed
            if tier.get("currency") is None:
                tier["currency"] = currency
                changed = True

    if collection in ITEM_COLLECTIONS:
        for item in result.get("items") or []:
            if "price" not in item or "qty" not in item:
                raise MigrationStateError(f"{collection} item has no embedded price and quantity")
            changed = _set_minor(item, "price") or changed
            total = _line_minor(item["priceMinor"], item["qty"])
            if item.get("lineTotalMinor") not in (None, total):
                raise MigrationStateError(f"{collection} item line total conflicts")
            if item.get("lineTotalMinor") is None:
                item["lineTotalMinor"] = total
                changed = True
            if item.get("currency") not in (None, currency):
                raise MigrationStateError(f"{collection} item currency conflicts")
            if item.get("currency") is None:
                item["currency"] = currency
                changed = True
        if collection in {"offers", "orders", "subscriptions"}:
            expected_total = sum(item["lineTotalMinor"] for item in result.get("items") or [])
            if expected_total > MAX_MINOR:
                raise MigrationStateError(f"{collection} total is outside the supported range")
            if result.get("netTotalMinor") not in (None, expected_total):
                raise MigrationStateError(f"{collection} net total conflicts")
            if result.get("netTotalMinor") is None:
                result["netTotalMinor"] = expected_total
                changed = True

    if collection == "invoices":
        for item in result.get("lineItems") or []:
            for field in ("price", "net"):
                changed = _set_minor(item, field) or changed
            if item.get("currency") not in (None, currency):
                raise MigrationStateError("Invoice item currency conflicts")
            if item.get("currency") is None:
                item["currency"] = currency
                changed = True
            if item.get("netMinor") is not None and item.get("taxRate") is not None:
                tax = int((Decimal(item["netMinor"]) * Decimal(str(item["taxRate"])) / 100).quantize(
                    Decimal("1"), rounding=ROUND_HALF_UP
                ))
                if item.get("taxMinor") not in (None, tax):
                    raise MigrationStateError("Invoice item tax conflicts")
                if item.get("taxMinor") is None:
                    item["taxMinor"] = tax
                    item["grossMinor"] = item["netMinor"] + tax
                    changed = True

    if collection == "machine_requests":
        terms = result.get("terms")
        if isinstance(terms, dict):
            for field in ("downPayment", "monthlyRate", "finalPayment", "coffeePricePerKg"):
                changed = _set_minor(terms, field) or changed
            if terms.get("currency") not in (None, currency):
                raise MigrationStateError("Machine terms currency conflicts")
            if terms.get("currency") is None:
                terms["currency"] = currency
                changed = True
    if collection in {"shop_orders", "invoices"}:
        changed = _expand_breakdown(result, "taxBreakdown") or changed
    return result, changed


def _analyze(database) -> dict[str, Any]:
    counts: dict[str, int] = {}
    unscoped: dict[str, int] = {}
    for collection in sorted(set(COLLECTION_FIELDS) | set(ITEM_COLLECTIONS)):
        pending = 0
        for document in database[collection].find({"tenantId": {"$exists": True}}):
            _expanded_document, needs_update = _expanded(database, collection, document)
            if needs_update:
                pending += 1
        counts[collection] = pending
        unscoped[collection] = database[collection].count_documents({
            "$or": [
                {"tenantId": {"$exists": False}},
                {"tenantId": None},
            ]
        })
    return {
        "documentsToExpand": counts,
        "totalDocumentsToExpand": sum(counts.values()),
        "documentsWithoutTenantId": unscoped,
        "totalDocumentsWithoutTenantId": sum(unscoped.values()),
    }


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only documents with a tenantId are expanded; tenantless legacy documents are reported and unchanged",
            "Every tenant defines an uppercase three-letter default currency",
            "Existing minor-unit fields, where present, agree with embedded major-unit values",
            "No product names, tax rates, costs, or other missing historical facts are reconstructed",
        ),
        expected_changes=_analyze(database),
    )


def apply(database, context) -> dict[str, Any]:
    changed: dict[str, int] = {}
    for collection in sorted(set(COLLECTION_FIELDS) | set(ITEM_COLLECTIONS)):
        count = 0
        for document in database[collection].find({"tenantId": {"$exists": True}}):
            expanded, needs_update = _expanded(database, collection, document)
            if not needs_update:
                continue
            context.checkpoint()
            expanded.pop("_id", None)
            result = database[collection].update_one(
                {"_id": document["_id"], "tenantId": document["tenantId"]},
                {"$set": expanded},
            )
            if result.matched_count != 1:
                raise MigrationStateError(f"{collection} changed concurrently during money expansion")
            context.checkpoint()
            count += 1
        changed[collection] = count
    return {"documentsExpanded": changed, "totalDocumentsExpanded": sum(changed.values())}


MIGRATION = Migration(
    version=4,
    name="money_expansion",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
