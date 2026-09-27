"""Add indexes for prospect offers, duplicate hints and offer conversion."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    (
        "offers",
        (("tenantId", ASCENDING), ("companyId", ASCENDING), ("createdBy", ASCENDING),
         ("createdAt", DESCENDING), ("id", DESCENDING)),
        "idx_tenant_prospect_offer_recent",
        {},
    ),
    (
        "companies",
        (("sourceOfferKey", ASCENDING),),
        "uniq_company_source_offer_key",
        {
            "unique": True,
            "sparse": True,
        },
    ),
    (
        "companies",
        (("tenantId", ASCENDING), ("email", ASCENDING)),
        "idx_tenant_company_email",
        {},
    ),
    (
        "companies",
        (("tenantId", ASCENDING), ("vatId", ASCENDING)),
        "idx_tenant_company_vat",
        {},
    ),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive indexes are created",
            "No offer, company, identity or membership document is changed",
            "Historical offer recipient snapshots remain immutable",
        ),
        expected_changes={"indexesToEnsure": len(INDEXES), "documentsChanged": 0},
    )


def apply(database, context) -> dict:
    for collection, keys, name, options in INDEXES:
        context.checkpoint()
        database[collection].create_index(list(keys), name=name, **options)
    context.checkpoint()
    return {"indexesEnsured": len(INDEXES), "documentsChanged": 0}


MIGRATION = Migration(
    version=14,
    name="final_product_review",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
