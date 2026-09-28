"""Add tenant-bound indexes for secure offer delivery without rewriting offers."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    (
        "offers",
        (("publicAccess.tokenHash", ASCENDING),),
        "uniq_offer_public_token",
        {"unique": True, "sparse": True},
    ),
    (
        "offers",
        (("tenantId", ASCENDING), ("responseStatus", ASCENDING), ("createdAt", DESCENDING)),
        "idx_tenant_offer_response_status",
        {},
    ),
)


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive offer indexes are introduced",
            "No offer, customer, price, order, invoice or payment document is changed",
            "Legacy offers remain untouched and receive document/link state only on explicit use",
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
    version=17,
    name="offer_delivery",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
