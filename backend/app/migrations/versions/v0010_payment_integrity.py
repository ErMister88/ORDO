"""Add the durable Stripe event ledger and provider-reference constraints."""

from __future__ import annotations

from pymongo import ASCENDING, DESCENDING

from ..models import Migration, MigrationPlan, checksum_file


INDEXES = (
    (
        "payment_provider_events",
        (("provider", ASCENDING), ("providerAccount", ASCENDING), ("eventId", ASCENDING)),
        "uniq_provider_account_event",
        {"unique": True},
    ),
    (
        "payment_provider_events",
        (("tenantId", ASCENDING), ("status", ASCENDING), ("updatedAt", DESCENDING)),
        "idx_tenant_payment_event_status",
        {},
    ),
    (
        "payment_provider_events",
        (("tenantId", ASCENDING), ("providerObjectId", ASCENDING)),
        "idx_tenant_provider_object",
        {},
    ),
)

for _collection in ("invoices", "shop_orders", "machine_requests"):
    INDEXES += (
        (
            _collection,
            (("stripeSessionId", ASCENDING),),
            f"uniq_{_collection}_stripe_session",
            {"unique": True, "sparse": True},
        ),
        (
            _collection,
            (("tenantId", ASCENDING), ("stripeCheckoutOperationId", ASCENDING)),
            f"idx_{_collection}_stripe_checkout_operation",
            {},
        ),
    )


def inspect(database) -> MigrationPlan:
    return MigrationPlan(
        preconditions=(
            "Only additive payment-provider ledger and reference indexes are introduced",
            "Existing payment and commercial documents are not rewritten",
            "No historical Stripe identifiers or payment states are invented",
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
    version=10,
    name="payment_integrity",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
