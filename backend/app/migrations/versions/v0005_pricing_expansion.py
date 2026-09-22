"""Prepare tenant pricing structures without inventing business values."""
from __future__ import annotations

from typing import Any
from pymongo import ASCENDING

from ..models import Migration, MigrationPlan, checksum_file

SS_TENANT_ID = "tnt_ss_0001"


def _gaps(database) -> dict[str, Any]:
    products = list(database["products"].find({"tenantId": {"$exists": True}}))
    return {
        "productsWithoutTaxRate": sum(1 for p in products if not isinstance(p.get("taxRate"), int)),
        "productsWithoutB2BStandardPrice": sum(1 for p in products if p.get("standardPriceMinor") is None),
        "productsWithoutB2CPrice": sum(1 for p in products if p.get("b2cPriceMinor") is None),
        "legacyDiscountTierProducts": sum(1 for p in products if p.get("discountTiers")),
        "shopSettingsMissing": database["settings"].count_documents({"tenantId": {"$exists": True}, "key": "shop"}) == 0,
        "note": "Legacy discountTiers are not reinterpreted as B2C tiers; ambiguous values require manual configuration.",
    }


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Pricing values remain unchanged and ambiguous legacy tiers are only reported",
            "Only the approved S&S shipping threshold and existing shipping/newsletter values seed missing S&S shop settings",
            "No subscription discount, product price, tier or tax value is invented",
            "Promotion indexes are created only for tenant-scoped documents",
        ),
        expected_changes={"indexesToEnsure": 2, "configurationGaps": _gaps(database),
                          "ssShopSettingsToInsert": database["settings"].count_documents(
                              {"tenantId": SS_TENANT_ID, "key": "shop"}) == 0},
    )


def apply(database, context) -> dict[str, Any]:
    context.checkpoint()
    database["pricing_promotions"].create_index(
        [("tenantId", ASCENDING), ("id", ASCENDING)], unique=True,
        partialFilterExpression={"tenantId": {"$type": "string"}, "id": {"$type": "string"}},
        name="uniq_tenant_pricing_promotion_id",
    )
    context.checkpoint()
    database["pricing_promotions"].create_index(
        [("tenantId", ASCENDING), ("productId", ASCENDING), ("companyId", ASCENDING),
         ("startsAt", ASCENDING), ("endsAt", ASCENDING)],
        partialFilterExpression={"tenantId": {"$type": "string"}, "productId": {"$type": "string"}},
        name="idx_tenant_pricing_promotion_resolution",
    )
    context.checkpoint()
    inserted = 0
    if database["settings"].count_documents({"tenantId": SS_TENANT_ID, "key": "shop"}) == 0:
        database["settings"].insert_one({
            "tenantId": SS_TENANT_ID, "key": "shop", "currency": "EUR",
            "freeShippingThreshold": 59.0, "freeShippingThresholdMinor": 5900,
            "shippingFee": 4.9, "shippingFeeMinor": 490,
            "newsletterDiscountPercent": 10, "newsletterDiscountEnabled": True,
        })
        inserted = 1
    context.checkpoint()
    return {"indexesEnsured": 2, "configurationGaps": _gaps(database),
            "configurationDocumentsInserted": inserted, "businessDocumentsChanged": 0}


MIGRATION = Migration(
    version=5,
    name="pricing_expansion",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
