"""Server-authoritative tenant pricing for B2B and B2C transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping

from .money import (
    MoneyError,
    amount_minor,
    currency_code,
    from_minor,
    included_tax_minor,
    line_total_minor,
    percentage_minor,
    require_minor,
)
from .snapshots import product_item_snapshot
from .tenant_access import TenantBusinessAccess


class PricingError(ValueError):
    """A safe, user-facing pricing failure without internal commercial data."""


def _quantity(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PricingError("Ungültige Menge") from exc
    if not result.is_finite() or result <= 0:
        raise PricingError("Ungültige Menge")
    return result


def _tax_rate(product: Mapping[str, Any]) -> int:
    value = product.get("taxRate")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise PricingError("Produkt besitzt keine gültige Steuerkonfiguration")
    return value


def _utc(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise PricingError(f"Aktionspreis besitzt keinen gültigen {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PricingError(f"Aktionspreis besitzt keinen gültigen {field}") from exc
    if parsed.tzinfo is None:
        raise PricingError(f"Aktionspreis besitzt keinen gültigen {field}")
    return parsed.astimezone(timezone.utc)


def _line_total(unit_minor: int, quantity: Decimal) -> int:
    try:
        return line_total_minor(unit_minor, quantity)
    except MoneyError as exc:
        raise PricingError("Preisberechnung überschreitet den unterstützten Wertebereich") from exc


@dataclass(frozen=True, slots=True)
class PriceQuote:
    pricing_context: str
    product_id: str
    quantity: Decimal
    currency: str
    tax_rate: int
    price_semantics: str
    base_unit_price_minor: int
    final_unit_price_minor: int
    line_total_minor: int
    price_source: str
    base_price_source: str
    quantity_tier: Mapping[str, Any] | None = None
    promotion: Mapping[str, Any] | None = None
    subscription_discount_percent: int = 0
    subscription_discount_minor: int = 0
    commercial_conditions: Mapping[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "pricingContext": self.pricing_context,
            "productId": self.product_id,
            "quantity": float(self.quantity),
            "currency": self.currency,
            "taxRate": self.tax_rate,
            "priceSemantics": self.price_semantics,
            "baseUnitPrice": from_minor(self.base_unit_price_minor),
            "baseUnitPriceMinor": self.base_unit_price_minor,
            "finalUnitPrice": from_minor(self.final_unit_price_minor),
            "finalUnitPriceMinor": self.final_unit_price_minor,
            "lineTotal": from_minor(self.line_total_minor),
            "lineTotalMinor": self.line_total_minor,
            "priceSource": self.price_source,
            "basePriceSource": self.base_price_source,
            "quantityTier": dict(self.quantity_tier) if self.quantity_tier else None,
            "promotion": dict(self.promotion) if self.promotion else None,
            "subscriptionDiscountPercent": self.subscription_discount_percent,
            "subscriptionDiscountMinor": self.subscription_discount_minor,
            "commercialConditions": dict(self.commercial_conditions) if self.commercial_conditions else None,
        }

    def snapshot(self, product: Mapping[str, Any]) -> dict[str, Any]:
        item = product_item_snapshot(
            product,
            quantity=float(self.quantity),
            unit_price_minor=self.final_unit_price_minor,
            currency=self.currency,
            price_source=self.price_source,
        )
        item.update({
            "pricingContext": self.pricing_context,
            "priceSemantics": self.price_semantics,
            "baseUnitPriceMinor": self.base_unit_price_minor,
            "basePriceSource": self.base_price_source,
            "quantityTier": dict(self.quantity_tier) if self.quantity_tier else None,
            "promotion": dict(self.promotion) if self.promotion else None,
            "subscriptionDiscountPercent": self.subscription_discount_percent,
            "subscriptionDiscountMinor": self.subscription_discount_minor,
            "commercialConditions": dict(self.commercial_conditions) if self.commercial_conditions else None,
        })
        return item


@dataclass(frozen=True, slots=True)
class BasketQuote:
    lines: tuple[tuple[Mapping[str, Any], PriceQuote], ...]
    currency: str
    merchandise_minor: int
    basket_discount_percent: int
    basket_discount_minor: int
    payable_merchandise_minor: int
    shipping_minor: int
    total_minor: int
    tax_breakdown_minor: Mapping[str, int]
    free_shipping_threshold_minor: int

    def public(self) -> dict[str, Any]:
        return {
            "pricingContext": "b2c",
            "currency": self.currency,
            "priceSemantics": "gross",
            "lines": [quote.public() for _product, quote in self.lines],
            "merchandise": from_minor(self.merchandise_minor),
            "merchandiseMinor": self.merchandise_minor,
            "discountPercent": self.basket_discount_percent,
            "discount": from_minor(self.basket_discount_minor),
            "discountMinor": self.basket_discount_minor,
            "subtotal": from_minor(self.payable_merchandise_minor),
            "subtotalMinor": self.payable_merchandise_minor,
            "freeShippingThreshold": from_minor(self.free_shipping_threshold_minor),
            "freeShippingThresholdMinor": self.free_shipping_threshold_minor,
            "shipping": from_minor(self.shipping_minor),
            "shippingMinor": self.shipping_minor,
            "total": from_minor(self.total_minor),
            "totalMinor": self.total_minor,
            "taxBreakdown": {
                rate: from_minor(value) for rate, value in self.tax_breakdown_minor.items()
            },
            "taxBreakdownMinor": dict(self.tax_breakdown_minor),
            "taxTotal": from_minor(sum(self.tax_breakdown_minor.values())),
            "taxTotalMinor": sum(self.tax_breakdown_minor.values()),
        }


class PricingEngine:
    def __init__(self, access: TenantBusinessAccess) -> None:
        self.access = access
        self.currency = currency_code(access.context.default_currency)

    async def _product(self, product_id: str) -> dict[str, Any]:
        product = await self.access.products.find_one({
            "id": product_id, "active": {"$ne": False}, "b2bAvailable": {"$ne": False},
        })
        if not product:
            raise PricingError("Produkt ist nicht verfügbar")
        stored_currency = product.get("currency")
        if stored_currency is not None and currency_code(stored_currency) != self.currency:
            raise PricingError("Produkt besitzt eine unpassende Währung")
        _tax_rate(product)
        return product

    async def quote_b2c_machine(self, machine_id: str) -> tuple[dict[str, Any], PriceQuote]:
        """Resolve a configured direct B2C machine sale; financing remains manual."""
        machine = await self.access.machines.find_one({"id": machine_id, "active": {"$ne": False}})
        if not machine:
            raise PricingError("Maschine ist nicht verfügbar")
        stored_currency = machine.get("currency")
        if stored_currency is not None and currency_code(stored_currency) != self.currency:
            raise PricingError("Maschine besitzt eine unpassende Währung")
        try:
            price_minor = amount_minor(machine, "price", expected_currency=self.currency)
        except MoneyError as exc:
            raise PricingError("Maschine besitzt keinen gültigen B2C-Preis") from exc
        if price_minor <= 0:
            raise PricingError("Maschine besitzt keinen gültigen B2C-Preis")
        tax_rate = _tax_rate(machine)
        return machine, PriceQuote(
            pricing_context="b2c", product_id=machine_id, quantity=Decimal(1),
            currency=self.currency, tax_rate=tax_rate, price_semantics="gross",
            base_unit_price_minor=price_minor, final_unit_price_minor=price_minor,
            line_total_minor=price_minor, price_source="machine_b2c_standard",
            base_price_source="machine_b2c_standard",
        )

    async def _b2c_product(self, product_id: str) -> dict[str, Any]:
        product = await self.access.products.find_one({
            "id": product_id, "active": {"$ne": False}, "b2cAvailable": {"$ne": False},
        })
        if not product:
            raise PricingError("Produkt ist nicht verfügbar")
        stored_currency = product.get("currency")
        if stored_currency is not None and currency_code(stored_currency) != self.currency:
            raise PricingError("Produkt besitzt eine unpassende Währung")
        _tax_rate(product)
        return product

    async def quote_b2b(
        self,
        company_id: str,
        product_id: str,
        quantity: Any,
        *,
        at: datetime | None = None,
    ) -> tuple[dict[str, Any], PriceQuote]:
        qty = _quantity(quantity)
        if not await self.access.companies.find_one({"id": company_id, "active": {"$ne": False}}):
            raise PricingError("Kunde ist nicht verfügbar")
        product = await self._product(product_id)

        customer_prices = await self.access.customer_prices.find({
                "companyId": company_id,
                "productId": product_id,
                "active": {"$ne": False},
            }).to_list(20)
        instant = at or datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise PricingError("Preiszeitpunkt benötigt eine Zeitzone")
        instant = instant.astimezone(timezone.utc)
        active_customer_prices = []
        for row in customer_prices:
            valid_from = row.get("validFrom")
            valid_until = row.get("validUntil")
            if valid_from and _utc(valid_from, "Gültigkeitsbeginn") > instant:
                continue
            if valid_until and _utc(valid_until, "Gültigkeitsende") <= instant:
                continue
            active_customer_prices.append(row)
        customer_prices = active_customer_prices
        if len(customer_prices) > 1:
            raise PricingError("Mehrere aktive Kundenpreise verhindern eine eindeutige Preisauflösung")
        if customer_prices:
            stored_currency = customer_prices[0].get("currency")
            if stored_currency is not None and stored_currency != self.currency:
                raise PricingError("Kundenpreis besitzt eine unpassende Währung")
            try:
                base_minor = amount_minor(
                    customer_prices[0], "price", expected_currency=self.currency
                )
            except MoneyError as exc:
                raise PricingError("Kundenpreis ist ungültig") from exc
            base_source = "customer_price"
            customer_conditions = {
                key: customer_prices[0].get(key)
                for key in ("deliveryTerms", "transportModel", "paymentTermDays", "minimumQuantity", "validFrom", "validUntil", "sourceReference")
                if customer_prices[0].get(key) not in (None, "")
            }
        else:
            try:
                base_minor = amount_minor(
                    product, "standardPrice", expected_currency=self.currency
                )
            except MoneyError as exc:
                raise PricingError("Für dieses B2B-Produkt ist kein gültiger Preis hinterlegt") from exc
            base_source = "b2b_standard"
            customer_conditions = None
        if base_minor <= 0:
            raise PricingError("Für dieses B2B-Produkt ist kein gültiger Preis hinterlegt")

        instant_key = instant.isoformat()
        candidates = await self.access.pricing_promotions.find({
            "productId": product_id,
            "active": {"$ne": False},
            "$or": [{"companyId": company_id}, {"companyId": None}],
            "startsAt": {"$lte": instant_key},
            "endsAt": {"$gt": instant_key},
        }).sort("startsAt", -1).to_list(3)
        active_promotions = []
        for promotion in candidates:
            if promotion.get("currency") != self.currency:
                raise PricingError("Aktionspreis besitzt eine unpassende Währung")
            starts = _utc(promotion.get("startsAt"), "Startzeitpunkt")
            ends = _utc(promotion.get("endsAt"), "Endzeitpunkt")
            if starts >= ends:
                raise PricingError("Aktionspreis besitzt einen ungültigen Zeitraum")
            if starts <= instant < ends:
                active_promotions.append(promotion)
        if len(active_promotions) > 1:
            raise PricingError("Überlappende Aktionspreise verhindern eine eindeutige Preisauflösung")

        final_minor = base_minor
        source = base_source
        promotion_snapshot = None
        if active_promotions:
            promotion = active_promotions[0]
            if not isinstance(promotion.get("id"), str) or not promotion["id"]:
                raise PricingError("Aktionspreis besitzt keine gültige Identität")
            try:
                final_minor = amount_minor(promotion, "price", expected_currency=self.currency)
            except MoneyError as exc:
                raise PricingError("Aktionspreis ist ungültig") from exc
            if final_minor <= 0:
                raise PricingError("Aktionspreis ist ungültig")
            source = "b2b_promotion"
            promotion_snapshot = {
                "id": promotion["id"],
                "name": promotion.get("name", ""),
                "scope": "company" if promotion.get("companyId") else "tenant",
                "startsAt": promotion["startsAt"],
                "endsAt": promotion["endsAt"],
                "priceMinor": final_minor,
            }
        quote = PriceQuote(
            pricing_context="b2b",
            product_id=product_id,
            quantity=qty,
            currency=self.currency,
            tax_rate=_tax_rate(product),
            price_semantics="net",
            base_unit_price_minor=base_minor,
            final_unit_price_minor=final_minor,
            line_total_minor=_line_total(final_minor, qty),
            price_source=source,
            base_price_source=base_source,
            promotion=promotion_snapshot,
            commercial_conditions=customer_conditions,
        )
        return product, quote

    def _validated_b2c_tiers(self, product: Mapping[str, Any]) -> list[dict[str, Any]]:
        tiers = product.get("b2cTiers") or []
        if not isinstance(tiers, list):
            raise PricingError("B2C-Mengenstaffeln sind beschädigt")
        validated = []
        seen: set[Decimal] = set()
        for tier in tiers:
            if not isinstance(tier, Mapping):
                raise PricingError("B2C-Mengenstaffeln sind beschädigt")
            minimum = _quantity(tier.get("minQty"))
            if minimum in seen:
                raise PricingError("B2C-Mengenstaffeln enthalten doppelte Grenzen")
            seen.add(minimum)
            try:
                price = amount_minor(tier, "price", expected_currency=self.currency)
            except MoneyError as exc:
                raise PricingError("B2C-Mengenstaffel besitzt keinen gültigen Preis") from exc
            if price <= 0:
                raise PricingError("B2C-Mengenstaffel besitzt keinen gültigen Preis")
            validated.append({"minQty": minimum, "priceMinor": price})
        return sorted(validated, key=lambda item: item["minQty"])

    async def quote_b2c(
        self,
        product_id: str,
        quantity: Any,
        *,
        subscription_discount_percent: int | None = None,
    ) -> tuple[dict[str, Any], PriceQuote]:
        product = await self._b2c_product(product_id)
        return product, self.quote_b2c_product(
            product, quantity, subscription_discount_percent=subscription_discount_percent,
        )

    def quote_b2c_product(
        self,
        product: Mapping[str, Any],
        quantity: Any,
        *,
        subscription_discount_percent: int | None = None,
    ) -> PriceQuote:
        """Quote an already tenant-scoped product without a duplicate DB lookup."""

        if product.get("active") is False or product.get("b2cAvailable") is False:
            raise PricingError("Produkt ist nicht verfügbar")
        product_id = product.get("id")
        if not isinstance(product_id, str) or not product_id:
            raise PricingError("Produktreferenz ist ungültig")
        stored_currency = product.get("currency")
        if stored_currency is not None and currency_code(stored_currency) != self.currency:
            raise PricingError("Produkt besitzt eine unpassende Währung")
        qty = _quantity(quantity)
        try:
            base_minor = amount_minor(product, "b2cPrice", expected_currency=self.currency)
        except MoneyError as exc:
            raise PricingError("Produkt besitzt keinen gültigen B2C-Preis") from exc
        if base_minor <= 0:
            raise PricingError("Produkt besitzt keinen gültigen B2C-Preis")
        tier_snapshot = None
        tier_price = base_minor
        for tier in self._validated_b2c_tiers(product):
            if qty >= tier["minQty"]:
                tier_price = tier["priceMinor"]
                tier_snapshot = {
                    "minQty": float(tier["minQty"]),
                    "priceMinor": tier_price,
                }
        discount_percent = subscription_discount_percent or 0
        if isinstance(discount_percent, bool) or not isinstance(discount_percent, int) or not 0 <= discount_percent <= 100:
            raise PricingError("Abo-Rabatt ist ungültig")
        discount_minor = percentage_minor(tier_price, discount_percent) if discount_percent else 0
        final_minor = tier_price - discount_minor
        if final_minor <= 0:
            raise PricingError("Abo-Rabatt führt zu keinem gültigen Verkaufspreis")
        source = "b2c_subscription" if discount_percent else (
            "b2c_quantity_tier" if tier_snapshot else "b2c_standard"
        )
        quote = PriceQuote(
            pricing_context="b2c",
            product_id=product_id,
            quantity=qty,
            currency=self.currency,
            tax_rate=_tax_rate(product),
            price_semantics="gross",
            base_unit_price_minor=base_minor,
            final_unit_price_minor=final_minor,
            line_total_minor=_line_total(final_minor, qty),
            price_source=source,
            base_price_source="b2c_standard",
            quantity_tier=tier_snapshot,
            subscription_discount_percent=discount_percent,
            subscription_discount_minor=discount_minor,
        )
        return quote

    async def quote_b2c_basket(
        self,
        items: Iterable[Mapping[str, Any]],
        settings: Mapping[str, Any],
        *,
        subscription: bool,
        basket_discount_percent: int = 0,
    ) -> BasketQuote:
        quantities: dict[str, Decimal] = {}
        for item in items:
            product_id = item.get("productId")
            if not isinstance(product_id, str) or not product_id:
                raise PricingError("Produktreferenz ist ungültig")
            quantities[product_id] = quantities.get(product_id, Decimal(0)) + _quantity(item.get("qty"))
        if not quantities:
            raise PricingError("Warenkorb ist leer")

        subscription_percent = 0
        if subscription:
            value = settings.get("subscriptionDiscountPercent")
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
                raise PricingError("Für das Monats-Abo ist kein gültiger Rabatt konfiguriert")
            subscription_percent = value
        product_rows = await self.access.products.find({
            "id": {"$in": list(quantities)},
            "active": {"$ne": False},
            "b2cAvailable": {"$ne": False},
        }).to_list(len(quantities))
        products = {row.get("id"): row for row in product_rows}
        if set(products) != set(quantities):
            raise PricingError("Produkt ist nicht verfügbar")

        lines = []
        merchandise_minor = 0
        tax_by_rate: dict[str, int] = {}
        for product_id, qty in quantities.items():
            product = products[product_id]
            quote = self.quote_b2c_product(
                product, qty,
                subscription_discount_percent=subscription_percent,
            )
            lines.append((product, quote))
            merchandise_minor += quote.line_total_minor
            try:
                require_minor(merchandise_minor)
            except MoneyError as exc:
                raise PricingError("Warenkorb überschreitet den unterstützten Wertebereich") from exc
            rate = str(quote.tax_rate)
            tax_by_rate[rate] = tax_by_rate.get(rate, 0) + included_tax_minor(
                quote.line_total_minor, quote.tax_rate
            )

        if isinstance(basket_discount_percent, bool) or not isinstance(basket_discount_percent, int) or not 0 <= basket_discount_percent <= 100:
            raise PricingError("Warenkorbrabatt ist ungültig")
        basket_discount_minor = (
            percentage_minor(merchandise_minor, basket_discount_percent)
            if basket_discount_percent else 0
        )
        payable_minor = merchandise_minor - basket_discount_minor
        if basket_discount_percent:
            tax_by_rate = {
                rate: value - percentage_minor(value, basket_discount_percent)
                for rate, value in tax_by_rate.items()
            }
        try:
            threshold_minor = amount_minor(
                settings, "freeShippingThreshold", expected_currency=self.currency
            )
            configured_shipping_minor = amount_minor(
                settings, "shippingFee", expected_currency=self.currency
            )
        except MoneyError as exc:
            raise PricingError("Versandkosten sind nicht vollständig konfiguriert") from exc
        shipping_minor = 0 if payable_minor >= threshold_minor else configured_shipping_minor
        total_minor = payable_minor + shipping_minor
        try:
            require_minor(total_minor)
        except MoneyError as exc:
            raise PricingError("Warenkorb überschreitet den unterstützten Wertebereich") from exc
        return BasketQuote(
            lines=tuple(lines),
            currency=self.currency,
            merchandise_minor=merchandise_minor,
            basket_discount_percent=basket_discount_percent,
            basket_discount_minor=basket_discount_minor,
            payable_merchandise_minor=payable_minor,
            shipping_minor=shipping_minor,
            total_minor=total_minor,
            tax_breakdown_minor=tax_by_rate,
            free_shipping_threshold_minor=threshold_minor,
        )
