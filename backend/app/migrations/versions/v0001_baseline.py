"""Record the pre-migration ORDO schema without changing business data."""

from __future__ import annotations

from ..models import Migration, MigrationPlan, checksum_file


EXPECTED_COLLECTIONS = {
    "companies",
    "contracts",
    "counters",
    "customer_prices",
    "invoices",
    "offers",
    "orders",
    "password_resets",
    "products",
    "users",
}
TECHNICAL_COLLECTIONS = {"schema_migrations", "schema_migration_lock"}


def _inventory(database) -> dict:
    collections = sorted(set(database.list_collection_names()) - TECHNICAL_COLLECTIONS)
    counts = {name: database[name].count_documents({}) for name in collections}
    found = set(collections)
    return {
        "collections": collections,
        "documentCounts": counts,
        "totalBusinessDocuments": sum(counts.values()),
        "missingExpectedCollections": sorted(EXPECTED_COLLECTIONS - found),
        "additionalCollections": sorted(found - EXPECTED_COLLECTIONS),
    }


def inspect(database) -> MigrationPlan:
    inventory = _inventory(database)
    return MigrationPlan(
        preconditions=(
            "MongoDB connection is available",
            "The target database was selected explicitly",
            "No business document is modified by the baseline",
        ),
        expected_changes={
            "businessDocumentsModified": 0,
            "frameworkWrites": [
                "schema_migrations metadata record",
                "unique schema_migrations.version index",
                "renewable schema_migration_lock lease",
            ],
            **inventory,
        },
    )


def apply(database, _context) -> dict:
    return {
        "businessDocumentsModified": 0,
        "baselineInventory": _inventory(database),
    }


MIGRATION = Migration(
    version=1,
    name="baseline_current_schema",
    checksum=checksum_file(__file__),
    inspect=inspect,
    apply=apply,
)
