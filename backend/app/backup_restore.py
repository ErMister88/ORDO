"""MongoDB backup, isolated restore and integrity verification primitives."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from typing import Any, Iterator

from .migrations.models import safe_error_message
from .migrations.registry import get_migrations


MANIFEST_VERSION = 1
RESTORE_PREFIX = "ordo_restore_"
PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_environment(environment: str, database_name: str, production_approval: str | None = None) -> str:
    normalized = environment.strip().lower()
    if normalized not in {"dev", "development", "test", "testing", "stage", "staging", *PRODUCTION_ENVIRONMENTS}:
        raise ValueError("APP_ENV must be explicit and recognised")
    if normalized in PRODUCTION_ENVIRONMENTS and production_approval != f"backup:{database_name}":
        raise ValueError("Production backup requires target-bound explicit approval")
    return normalized


@contextmanager
def mongo_tool_config(mongo_url: str) -> Iterator[Path]:
    """Provide the URI via a mode-0600 config file, never a process argument."""

    if not mongo_url or not mongo_url.strip():
        raise ValueError("MONGO_URL is required")
    fd, name = tempfile.mkstemp(prefix="ordo-mongo-tool-", suffix=".yml")
    path = Path(name)
    try:
        os.fchmod(fd, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("uri: ")
            handle.write(json.dumps(mongo_url.strip()))
            handle.write("\n")
        yield path
    finally:
        path.unlink(missing_ok=True)


def _run_tool(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        detail = safe_error_message(RuntimeError(result.stderr or result.stdout or "MongoDB tool failed"))
        raise RuntimeError(detail)


def create_backup(
    client,
    *,
    mongo_url: str,
    database_name: str,
    environment: str,
    output_directory: Path,
    production_approval: str | None = None,
) -> tuple[Path, Path]:
    """Create a compressed archive and only then publish an integrity manifest."""

    normalized_env = _validate_environment(environment, database_name, production_approval)
    source_db = client[database_name]
    source_db.command("ping")
    output_directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive = output_directory / f"{database_name}-{timestamp}.archive.gz"
    manifest_path = archive.with_suffix(archive.suffix + ".json")

    collection_names = sorted(source_db.list_collection_names())
    counts = {name: source_db[name].count_documents({}) for name in collection_names}
    indexes = {
        name: sorted(info.get("name", "") for info in source_db[name].list_indexes())
        for name in collection_names
    }
    migration_rows = list(source_db.schema_migrations.find({"status": "completed"}, {"_id": 0, "version": 1}))
    schema_version = max((row.get("version", 0) for row in migration_rows), default=0)

    try:
        with mongo_tool_config(mongo_url) as config:
            _run_tool([
                "mongodump", f"--config={config}", f"--db={database_name}",
                f"--archive={archive}", "--gzip",
            ])
        if not archive.is_file() or archive.stat().st_size <= 0:
            raise RuntimeError("Backup archive is empty")
        manifest = {
            "manifestVersion": MANIFEST_VERSION,
            "createdAt": _iso_now(),
            "environment": normalized_env,
            "databaseIdentifier": database_name,
            "schemaVersion": schema_version,
            "archiveFile": archive.name,
            "archiveSize": archive.stat().st_size,
            "archiveSha256": _sha256(archive),
            "collectionCounts": counts,
            "indexNames": indexes,
            "integrity": "complete",
        }
        temporary_manifest = manifest_path.with_suffix(manifest_path.suffix + ".tmp")
        temporary_manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        temporary_manifest.replace(manifest_path)
        return archive, manifest_path
    except Exception:
        manifest_path.unlink(missing_ok=True)
        raise


def load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {
        "manifestVersion", "databaseIdentifier", "schemaVersion", "archiveFile",
        "archiveSize", "archiveSha256", "collectionCounts", "indexNames", "integrity",
    }
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ValueError("Backup manifest is incomplete")
    if manifest["manifestVersion"] != MANIFEST_VERSION or manifest["integrity"] != "complete":
        raise ValueError("Backup manifest is unsupported or incomplete")
    archive_file = manifest["archiveFile"]
    if (
        not isinstance(archive_file, str)
        or not archive_file
        or Path(archive_file).is_absolute()
        or Path(archive_file).name != archive_file
    ):
        raise ValueError("Backup manifest archive path is invalid")
    return manifest


def verify_restored_database(restored_db, manifest: dict[str, Any]) -> dict[str, Any]:
    restored_db.command("ping")
    expected_counts = manifest["collectionCounts"]
    actual_names = set(restored_db.list_collection_names())
    missing_collections = sorted(set(expected_counts) - actual_names)
    count_mismatches = {
        name: {"expected": expected, "actual": restored_db[name].count_documents({})}
        for name, expected in expected_counts.items()
        if name in actual_names and restored_db[name].count_documents({}) != expected
    }
    missing_indexes: dict[str, list[str]] = {}
    for name, expected in manifest["indexNames"].items():
        if name not in actual_names:
            continue
        actual = {info.get("name") for info in restored_db[name].list_indexes()}
        absent = sorted(set(expected) - actual)
        if absent:
            missing_indexes[name] = absent

    registry = {migration.version: migration.checksum for migration in get_migrations()}
    ledger_errors = []
    applied = {}
    for row in restored_db.schema_migrations.find({"status": "completed"}):
        version = row.get("version")
        applied[version] = row.get("checksum")
        if version in registry and row.get("checksum") != registry[version]:
            ledger_errors.append({"version": version, "error": "checksum_mismatch"})
    latest = max((row.get("version", 0) for row in restored_db.schema_migrations.find({"status": "completed"})), default=0)
    if latest != manifest["schemaVersion"]:
        ledger_errors.append({"version": latest, "error": "schema_version_mismatch"})
    for version, checksum in registry.items():
        if version <= manifest["schemaVersion"] and version not in applied:
            ledger_errors.append({"version": version, "error": "migration_missing"})

    tenant_ids = {
        row.get("id")
        for row in restored_db.tenants.find({})
        if isinstance(row.get("id"), str)
    }
    membership_tenant_errors = sum(
        1 for row in restored_db.tenant_memberships.find({})
        if row.get("tenantId") not in tenant_ids
    )
    membership_user_errors = sum(
        1 for row in restored_db.tenant_memberships.find({})
        if not restored_db.users.find_one({"id": row.get("userId")})
    )
    membership_company_errors = sum(
        1 for row in restored_db.tenant_memberships.find({"companyId": {"$type": "string"}})
        if not restored_db.companies.find_one({
            "id": row.get("companyId"), "tenantId": row.get("tenantId"),
        })
    )
    commercial_reference_errors = 0
    for invoice in restored_db.invoices.find({"orderId": {"$type": "string"}}):
        if not restored_db.orders.find_one({
            "id": invoice.get("orderId"), "tenantId": invoice.get("tenantId"),
        }):
            commercial_reference_errors += 1
    for order in restored_db.orders.find({"invoiceId": {"$type": "string"}}):
        if not restored_db.invoices.find_one({
            "id": order.get("invoiceId"), "tenantId": order.get("tenantId"),
        }):
            commercial_reference_errors += 1

    reference_errors = (
        membership_tenant_errors + membership_user_errors
        + membership_company_errors + commercial_reference_errors
    )
    ok = not any((missing_collections, count_mismatches, missing_indexes, ledger_errors, reference_errors))
    return {
        "verified": ok,
        "databaseReachable": True,
        "missingCollections": missing_collections,
        "countMismatches": count_mismatches,
        "missingIndexes": missing_indexes,
        "migrationLedgerErrors": ledger_errors,
        "tenantReferenceErrors": membership_tenant_errors,
        "membershipUserReferenceErrors": membership_user_errors,
        "membershipCompanyReferenceErrors": membership_company_errors,
        "commercialReferenceErrors": commercial_reference_errors,
        "verifiedAt": _iso_now(),
    }


def restore_backup(
    client,
    *,
    mongo_url: str,
    manifest_path: Path,
    target_database: str,
    environment: str,
) -> dict[str, Any]:
    normalized_env = environment.strip().lower()
    if normalized_env in PRODUCTION_ENVIRONMENTS:
        raise ValueError("Restore into a production environment is forbidden")
    if not target_database.startswith(RESTORE_PREFIX):
        raise ValueError(f"Restore target must start with {RESTORE_PREFIX}")
    manifest = load_manifest(manifest_path)
    if target_database == manifest["databaseIdentifier"]:
        raise ValueError("Restore target must differ from source database")
    target = client[target_database]
    if target.list_collection_names():
        raise ValueError("Restore target database must be empty")
    archive = manifest_path.parent / manifest["archiveFile"]
    if not archive.is_file() or archive.stat().st_size != manifest["archiveSize"] or _sha256(archive) != manifest["archiveSha256"]:
        raise ValueError("Backup archive does not match its manifest")

    with mongo_tool_config(mongo_url) as config:
        _run_tool([
            "mongorestore", f"--config={config}", f"--archive={archive}", "--gzip",
            f"--nsFrom={manifest['databaseIdentifier']}.*", f"--nsTo={target_database}.*",
        ])
    verification = verify_restored_database(target, manifest)
    verification_path = manifest_path.parent / f"{target_database}-verification.json"
    verification_path.write_text(json.dumps(verification, indent=2, sort_keys=True), encoding="utf-8")
    if not verification["verified"]:
        raise RuntimeError(f"Restore verification failed; see {verification_path.name}")
    return verification
