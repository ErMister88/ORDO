"""Immutable line-item snapshots for commercial documents."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .money import amount_minor, currency_code, from_minor, line_total_minor, require_minor


SNAPSHOT_VERSION = 1


def validate_product_item_snapshot(item: Mapping[str, Any], *, currency: str) -> None:
    """Reject incomplete or internally inconsistent commercial snapshots."""
    code = currency_code(currency)
    if item.get("snapshotVersion") != SNAPSHOT_VERSION or item.get("currency") != code:
        raise ValueError("Commercial source has no compatible immutable item snapshot")
    for field in ("productId", "productName", "unit", "priceSource"):
        value = item.get(field)
        if not isinstance(value, str) or not value or value != value.strip():
            raise ValueError(f"Commercial snapshot has no valid {field}")
    try:
        quantity = Decimal(str(item.get("qty")))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Commercial snapshot has no valid quantity") from exc
    if not quantity.is_finite() or quantity <= 0:
        raise ValueError("Commercial snapshot has no valid quantity")
    unit_price = require_minor(item.get("unitPriceMinor"))
    line_total = require_minor(item.get("lineTotalMinor"))
    if line_total_minor(unit_price, quantity) != line_total:
        raise ValueError("Commercial snapshot line total is inconsistent")
    tax_rate = item.get("taxRate")
    if isinstance(tax_rate, bool) or not isinstance(tax_rate, int) or not 0 <= tax_rate <= 100:
        raise ValueError("Commercial snapshot has no valid tax rate")
    if item.get("costMinor") is not None:
        require_minor(item["costMinor"])


def redact_internal_snapshot_fields(document: Mapping[str, Any]) -> dict[str, Any]:
    """Keep historical cost bases server-side even when documents are returned."""
    public = deepcopy(dict(document))
    for field in ("items", "lineItems"):
        for item in public.get(field) or []:
            if isinstance(item, dict):
                item.pop("costMinor", None)
    attribution = public.get("salesAttribution")
    if isinstance(attribution, dict):
        attribution.pop("membershipId", None)
    return public


def product_item_snapshot(
    product: Mapping[str, Any],
    *,
    quantity: Any,
    unit_price_minor: int,
    currency: str,
    price_source: str,
) -> dict[str, Any]:
    code = currency_code(currency)
    product_currency = product.get("currency")
    if product_currency is not None and currency_code(product_currency) != code:
        raise ValueError("Product currency mismatch")
    total_minor = line_total_minor(unit_price_minor, quantity)
    item: dict[str, Any] = {
        "snapshotVersion": SNAPSHOT_VERSION,
        "productId": product["id"],
        "sku": product.get("sku"),
        "productName": f"{product.get('brand', '')} {product.get('name', '')}".strip(),
        "description": product.get("description", ""),
        "unit": product.get("unit", "kg"),
        "qty": quantity,
        "price": from_minor(unit_price_minor),
        "unitPriceMinor": unit_price_minor,
        "lineTotalMinor": total_minor,
        "currency": code,
        "taxRate": int(product.get("taxRate", 7)),
        "discountMinor": 0,
        "discountPercent": 0,
        "priceSource": price_source,
    }
    if "costMinor" in product or "cost" in product:
        item["costMinor"] = amount_minor(product, "cost", expected_currency=code)
    else:
        item["costMinor"] = None
    return item


def clone_snapshot_items(items: list[Mapping[str, Any]], *, currency: str) -> list[dict[str, Any]]:
    code = currency_code(currency)
    cloned: list[dict[str, Any]] = []
    for source in items:
        validate_product_item_snapshot(source, currency=code)
        cloned.append(deepcopy(dict(source)))
    return cloned


def items_total_minor(items: list[Mapping[str, Any]], *, currency: str) -> int:
    code = currency_code(currency)
    total = 0
    for item in items:
        validate_product_item_snapshot(item, currency=code)
        value = item["lineTotalMinor"]
        total += value
        require_minor(total)
    return total
