"""Canonical money arithmetic for persisted commercial documents.

New authoritative amounts are integer minor units.  Legacy major-unit fields
remain available at the API boundary during the expand/backfill transition.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
from typing import Any, Mapping


MINOR_FACTOR = 100
MAX_MINOR = 9_000_000_000_000_000
_CURRENCY = re.compile(r"^[A-Z]{3}$")


class MoneyError(ValueError):
    pass


def currency_code(value: Any) -> str:
    if not isinstance(value, str) or value != value.strip() or not _CURRENCY.fullmatch(value):
        raise MoneyError("Currency must be a normalized three-letter ISO code")
    return value


def to_minor(value: Any, *, allow_negative: bool = False) -> int:
    """Convert a decimal major-unit value to minor units with commercial rounding."""

    if isinstance(value, bool) or value is None:
        raise MoneyError("Money value must be numeric")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("Money value must be numeric") from exc
    if not decimal.is_finite():
        raise MoneyError("Money value must be finite")
    minor = int((decimal * MINOR_FACTOR).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if not allow_negative and minor < 0:
        raise MoneyError("Money value must not be negative")
    if abs(minor) > MAX_MINOR:
        raise MoneyError("Money value exceeds the supported range")
    return minor


def from_minor(value: Any) -> float:
    minor = require_minor(value, allow_negative=True)
    return float(Decimal(minor) / MINOR_FACTOR)


def require_minor(value: Any, *, allow_negative: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MoneyError("Minor-unit amount must be an integer")
    if not allow_negative and value < 0:
        raise MoneyError("Minor-unit amount must not be negative")
    if abs(value) > MAX_MINOR:
        raise MoneyError("Minor-unit amount exceeds the supported range")
    return value


def amount_minor(
    document: Mapping[str, Any],
    field: str,
    *,
    expected_currency: str,
    allow_negative: bool = False,
) -> int:
    """Read a new minor-unit field, falling back only to its embedded legacy value."""

    expected = currency_code(expected_currency)
    stored_currency = document.get("currency")
    if stored_currency is not None and currency_code(stored_currency) != expected:
        raise MoneyError("Currency mismatch")
    minor_field = f"{field}Minor"
    if minor_field in document:
        return require_minor(document[minor_field], allow_negative=allow_negative)
    if field not in document:
        raise MoneyError(f"Missing money field {field}")
    return to_minor(document[field], allow_negative=allow_negative)


def line_total_minor(unit_price_minor: int, quantity: Any) -> int:
    price = require_minor(unit_price_minor)
    try:
        qty = Decimal(str(quantity))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("Quantity must be numeric") from exc
    if not qty.is_finite() or qty <= 0:
        raise MoneyError("Quantity must be positive and finite")
    total = int((Decimal(price) * qty).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return require_minor(total)


def percentage_minor(amount: int, percent: Any) -> int:
    base = require_minor(amount)
    try:
        rate = Decimal(str(percent))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("Percentage must be numeric") from exc
    if not rate.is_finite() or rate < 0 or rate > 100:
        raise MoneyError("Percentage must be between 0 and 100")
    return require_minor(
        int((Decimal(base) * rate / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    )


def tax_minor(net_minor: int, rate: Any) -> int:
    return percentage_minor(net_minor, rate)


def included_tax_minor(gross_minor: int, rate: Any) -> int:
    gross = require_minor(gross_minor)
    try:
        tax_rate = Decimal(str(rate))
    except (InvalidOperation, ValueError) as exc:
        raise MoneyError("Tax rate must be numeric") from exc
    if not tax_rate.is_finite() or tax_rate < 0:
        raise MoneyError("Tax rate must not be negative")
    if tax_rate == 0:
        return 0
    net = (Decimal(gross) * 100 / (Decimal(100) + tax_rate)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return require_minor(gross - int(net))


def legacy_money_fields(field: str, minor: int) -> dict[str, int | float]:
    checked = require_minor(minor, allow_negative=True)
    return {field: from_minor(checked), f"{field}Minor": checked}
