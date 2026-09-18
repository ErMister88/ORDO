"""Migration planning, execution, status tracking, and recovery."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hmac import compare_digest
import time
from typing import Any, Iterable, Mapping

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .lock import MigrationLease
from .models import (
    ChecksumMismatchError,
    DryRunWriteError,
    Migration,
    MigrationContext,
    MigrationDefinitionError,
    MigrationPlan,
    MigrationStateError,
    ProductionGuardError,
    safe_error_message,
)
from .registry import get_migrations


MIGRATION_COLLECTION = "schema_migrations"
VERSION_INDEX_NAME = "uniq_schema_migrations_version"
KNOWN_STATUSES = {"running", "completed", "failed"}
PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}
KNOWN_ENVIRONMENTS = {
    "dev",
    "development",
    "test",
    "testing",
    "stage",
    "staging",
    *PRODUCTION_ENVIRONMENTS,
}

READ_ONLY_COLLECTION_METHODS = {
    "count_documents",
    "distinct",
    "estimated_document_count",
    "find",
    "find_one",
    "index_information",
    "list_indexes",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _contains_write_stage(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in {"$out", "$merge"} or _contains_write_stage(nested)
            for key, nested in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_write_stage(item) for item in value)
    return False


class ReadOnlyCollection:
    """Expose MongoDB read operations and fail closed for every other method."""

    def __init__(self, collection) -> None:
        self.__collection = collection

    def __getattribute__(self, name: str):
        if name in {"_collection", "_ReadOnlyCollection__collection"}:
            raise DryRunWriteError("Dry-run cannot expose the writable collection handle")
        return object.__getattribute__(self, name)

    def _raw_collection(self):
        return object.__getattribute__(self, "_ReadOnlyCollection__collection")

    @property
    def name(self) -> str:
        return self._raw_collection().name

    def aggregate(self, pipeline, *args, **kwargs):
        pipeline = list(pipeline)
        if _contains_write_stage(pipeline):
            raise DryRunWriteError("Dry-run aggregation cannot use $out or $merge")
        return self._raw_collection().aggregate(pipeline, *args, **kwargs)

    def __getattr__(self, name: str):
        if name in READ_ONLY_COLLECTION_METHODS:
            return getattr(self._raw_collection(), name)
        raise DryRunWriteError(f"Dry-run blocked collection operation: {name}")


class ReadOnlyDatabase:
    """Restricted database view passed to migration inspect functions."""

    def __init__(self, database) -> None:
        self.__database = database

    def __getattribute__(self, name: str):
        if name in {"_database", "_ReadOnlyDatabase__database"}:
            raise DryRunWriteError("Dry-run cannot expose the writable database handle")
        return object.__getattribute__(self, name)

    def _raw_database(self):
        return object.__getattribute__(self, "_ReadOnlyDatabase__database")

    @property
    def name(self) -> str:
        return self._raw_database().name

    def list_collection_names(self, *args, **kwargs):
        return self._raw_database().list_collection_names(*args, **kwargs)

    def __getitem__(self, name: str) -> ReadOnlyCollection:
        return ReadOnlyCollection(self._raw_database()[name])

    def get_collection(self, name: str) -> ReadOnlyCollection:
        return self[name]

    def __getattr__(self, name: str):
        raise DryRunWriteError(f"Dry-run blocked database operation: {name}")


@dataclass
class RunReport:
    dryRun: bool
    appEnv: str
    databaseName: str
    detectedMigrations: list[dict[str, Any]] = field(default_factory=list)
    appliedMigrations: list[dict[str, Any]] = field(default_factory=list)
    plannedMigrations: list[dict[str, Any]] = field(default_factory=list)
    executedMigrations: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MigrationRunner:
    def __init__(
        self,
        database,
        *,
        app_env: str,
        application_version: str,
        database_name: str | None = None,
        migrations: Iterable[Migration] | None = None,
        production_approval: str | None = None,
        lease_seconds: float = 60,
    ) -> None:
        self.database = database
        self._metadata = database[MIGRATION_COLLECTION].with_options(
            read_concern=ReadConcern("majority"),
            write_concern=WriteConcern("majority"),
        )
        self.database_name = database_name or database.name
        if self.database_name != database.name:
            raise MigrationDefinitionError("Configured database name does not match the database handle")
        self.app_env = app_env.strip().lower()
        if not self.app_env:
            raise MigrationDefinitionError("APP_ENV must be explicit")
        if self.app_env not in KNOWN_ENVIRONMENTS:
            raise MigrationDefinitionError(
                f"Unknown APP_ENV {self.app_env!r}; refusing to assume it is non-production"
            )
        self.application_version = application_version.strip() or "unknown"
        self.production_approval = (production_approval or "").strip()
        self.lease_seconds = lease_seconds
        self.migrations = tuple(migrations if migrations is not None else get_migrations())
        self._validate_registry()

    def preview(self) -> RunReport:
        """Build a completely read-only migration plan."""

        existing = self._read_existing()
        self._validate_existing(existing)
        report = self._new_report(dry_run=True)
        for migration in self.migrations:
            document = existing.get(migration.version)
            if document and document["status"] == "completed":
                report.appliedMigrations.append(self._record_summary(document))
                continue
            report.plannedMigrations.append(self._planned_summary(migration, document))
        return report

    def run(self, *, dry_run: bool = False) -> RunReport:
        if dry_run:
            return self.preview()
        self._assert_production_allowed()
        # Fail on incompatible history before creating framework metadata or a lease.
        self._validate_existing(self._read_existing())

        executed: list[dict[str, Any]] = []
        lease = MigrationLease(
            self.database,
            application_version=self.application_version,
            lease_seconds=self.lease_seconds,
        )
        with lease:
            self._ensure_metadata_index()
            existing = self._read_existing()
            self._validate_existing(existing)
            for migration in self.migrations:
                document = existing.get(migration.version)
                if document and document["status"] == "completed":
                    continue
                # Re-evaluate read-only preconditions after acquiring the lease.
                self._planned_summary(migration, document)
                lease.assert_owned()
                result = self._apply_one(migration, lease)
                if result is not None:
                    executed.append(result)
                existing = self._read_existing()

        final_existing = self._read_existing()
        self._validate_existing(final_existing)
        report = self._new_report(dry_run=False)
        report.executedMigrations = executed
        for migration in self.migrations:
            document = final_existing.get(migration.version)
            if document and document["status"] == "completed":
                report.appliedMigrations.append(self._record_summary(document))
            else:
                report.plannedMigrations.append(self._planned_summary(migration, document))
        return report

    def _apply_one(
        self,
        migration: Migration,
        lease: MigrationLease,
    ) -> dict[str, Any] | None:
        collection = self._metadata
        started_at = utc_now()
        # Extend and verify the lease immediately before the atomic status claim.
        lease.renew()
        try:
            running = collection.find_one_and_update(
                {
                    "version": migration.version,
                    "status": {"$ne": "completed"},
                    "$or": [
                        {"leaseGeneration": {"$exists": False}},
                        {"leaseGeneration": {"$lte": lease.generation}},
                    ],
                },
                {
                    "$set": {
                        "name": migration.name,
                        "checksum": migration.checksum,
                        "status": "running",
                        "startedAt": started_at,
                        "completedAt": None,
                        "applicationVersion": self.application_version,
                        "resultSummary": {"message": "Migration started"},
                        "lockOwnerId": lease.owner_id,
                        "leaseGeneration": lease.generation,
                    },
                    "$setOnInsert": {"version": migration.version},
                    "$inc": {"attempts": 1},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError as exc:
            current = collection.find_one({"version": migration.version})
            if current and current.get("status") == "completed":
                self._validate_existing({migration.version: current})
                return None
            raise MigrationStateError(
                f"Migration {migration.version} could not be claimed under the active lease"
            ) from exc
        if not running:
            raise MigrationStateError(
                f"Migration {migration.version} could not be claimed under the active lease"
            )
        attempt = int(running.get("attempts", 1))
        monotonic_start = time.monotonic()
        try:
            context = MigrationContext(
                application_version=self.application_version,
                database_name=self.database_name,
                attempt=attempt,
                _lease_checkpoint=lease.assert_owned,
            )
            context.checkpoint()
            result = dict(migration.apply(self.database, context))
            # Keep a full lease window for the fenced completion write.
            lease.renew()
            completed_at = utc_now()
            summary = {
                **result,
                "message": "Migration completed",
                "attempt": attempt,
                "durationMs": round((time.monotonic() - monotonic_start) * 1000),
            }
            completion = collection.update_one(
                {
                    "version": migration.version,
                    "status": "running",
                    "lockOwnerId": lease.owner_id,
                    "leaseGeneration": lease.generation,
                },
                {
                    "$set": {
                        "status": "completed",
                        "completedAt": completed_at,
                        "resultSummary": summary,
                    }
                },
            )
            if completion.matched_count != 1:
                raise MigrationStateError(
                    f"Migration {migration.version} completion was not persisted under the active lease"
                )
            return {**summary, "version": migration.version, "name": migration.name}
        except Exception as exc:
            completed_at = utc_now()
            collection.update_one(
                {
                    "version": migration.version,
                    "status": "running",
                    "lockOwnerId": lease.owner_id,
                    "leaseGeneration": lease.generation,
                },
                {
                    "$set": {
                        "status": "failed",
                        "completedAt": completed_at,
                        "resultSummary": {
                            "message": "Migration failed",
                            "attempt": attempt,
                            "errorType": exc.__class__.__name__,
                            "error": safe_error_message(exc),
                            "durationMs": round((time.monotonic() - monotonic_start) * 1000),
                        },
                    }
                },
            )
            raise

    def _new_report(self, *, dry_run: bool) -> RunReport:
        return RunReport(
            dryRun=dry_run,
            appEnv=self.app_env,
            databaseName=self.database_name,
            detectedMigrations=[
                {"version": m.version, "name": m.name, "checksum": m.checksum}
                for m in self.migrations
            ],
        )

    def _planned_summary(self, migration: Migration, document: Mapping[str, Any] | None) -> dict[str, Any]:
        plan = migration.inspect(ReadOnlyDatabase(self.database))
        if not isinstance(plan, MigrationPlan):
            raise MigrationDefinitionError(
                f"Migration {migration.version} inspect() must return MigrationPlan"
            )
        return {
            "version": migration.version,
            "name": migration.name,
            "currentStatus": document.get("status") if document else "pending",
            "preconditions": list(plan.preconditions),
            "expectedChanges": dict(plan.expected_changes),
        }

    def _read_existing(self) -> dict[int, dict[str, Any]]:
        if MIGRATION_COLLECTION not in self.database.list_collection_names():
            return {}
        documents = list(self._metadata.find({}))
        result: dict[int, dict[str, Any]] = {}
        for document in documents:
            version = document.get("version")
            if type(version) is not int:
                raise MigrationStateError("schema_migrations contains a record without an integer version")
            if version in result:
                raise MigrationStateError(f"schema_migrations contains duplicate version {version}")
            result[version] = document
        return result

    def _validate_existing(self, existing: Mapping[int, Mapping[str, Any]]) -> None:
        known = {migration.version: migration for migration in self.migrations}
        unknown = sorted(set(existing) - set(known))
        if unknown:
            raise MigrationStateError(
                "Database contains migration versions unknown to this application: "
                + ", ".join(map(str, unknown))
            )
        for version, document in existing.items():
            migration = known[version]
            if document.get("checksum") != migration.checksum:
                raise ChecksumMismatchError(
                    f"Checksum mismatch for migration {version} ({migration.name})"
                )
            if document.get("name") != migration.name:
                raise MigrationStateError(f"Name mismatch for migration version {version}")
            if document.get("status") not in KNOWN_STATUSES:
                raise MigrationStateError(f"Unknown status for migration version {version}")

    def _record_summary(self, document: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "version": document["version"],
            "name": document["name"],
            "status": document["status"],
            "applicationVersion": document.get("applicationVersion", "unknown"),
        }

    def _ensure_metadata_index(self) -> None:
        self._metadata.create_index(
            [("version", ASCENDING)],
            unique=True,
            name=VERSION_INDEX_NAME,
        )

    def _assert_production_allowed(self) -> None:
        expected = f"{self.app_env}:{self.database_name}"
        approved = bool(self.production_approval) and compare_digest(
            self.production_approval,
            expected,
        )
        if self.app_env in PRODUCTION_ENVIRONMENTS and not approved:
            raise ProductionGuardError(
                "Production migration blocked; explicit target-bound approval is required"
            )

    def _validate_registry(self) -> None:
        versions: set[int] = set()
        names: set[str] = set()
        previous = 0
        for migration in self.migrations:
            migration.validate()
            if migration.version in versions:
                raise MigrationDefinitionError(f"Duplicate migration version {migration.version}")
            if migration.name in names:
                raise MigrationDefinitionError(f"Duplicate migration name {migration.name}")
            if migration.version <= previous:
                raise MigrationDefinitionError("Migrations must be registered in ascending order")
            versions.add(migration.version)
            names.add(migration.name)
            previous = migration.version
