"""Safe liveness, readiness and technical capability reporting."""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Any

from .migrations.registry import get_migrations
from .tenancy import MongoTenantDirectory, SingleTenantResolver, TenancyConfigurationError, TenancySettings
from .tenant_access import TenantBusinessAccess
from .email_provider import email_configuration_status
from .storage import storage_configuration_status
from .production_config import validate_runtime_configuration


KNOWN_ENVIRONMENTS = {
    "dev", "development", "test", "testing", "stage", "staging", "prod", "production", "live",
}


def _status(status: str, message: str) -> dict[str, str]:
    return {"status": status, "message": message}


async def capability_snapshot(database) -> dict[str, Any]:
    capabilities: dict[str, dict[str, Any]] = {}
    database_available = True
    try:
        await database.command("ping")
        capabilities["database"] = _status("available", "Datenbank erreichbar")
    except Exception:
        database_available = False
        capabilities["database"] = _status("unavailable", "Datenbank nicht erreichbar")

    latest_expected = max(migration.version for migration in get_migrations())
    latest_applied = None
    schema_ready = False
    if database_available:
        try:
            rows = await database.schema_migrations.find({"status": "completed"}).to_list(1000)
            latest_applied = max((row.get("version", 0) for row in rows), default=0)
            registry = {migration.version: migration.checksum for migration in get_migrations()}
            applied = {
                row.get("version"): row.get("checksum")
                for row in rows
                if isinstance(row.get("version"), int)
            }
            schema_ready = (
                latest_applied == latest_expected
                and len(rows) == len(registry)
                and set(applied) == set(registry)
                and all(applied[version] == checksum for version, checksum in registry.items())
            )
        except Exception:
            schema_ready = False
    capabilities["schema"] = {
        "status": "available" if schema_ready else "unavailable",
        "message": "Schema aktuell" if schema_ready else "Migrationen ausstehend oder nicht prüfbar",
        "expectedVersion": latest_expected,
        "appliedVersion": latest_applied,
    }

    stripe_key = (os.getenv("STRIPE_API_KEY") or "").strip()
    webhook_secret = (os.getenv("STRIPE_WEBHOOK_SECRET") or "").strip()
    if not stripe_key and not webhook_secret:
        capabilities["payments"] = _status("not_configured", "Zahlungen nicht konfiguriert")
    elif stripe_key.startswith(("sk_test_", "sk_live_")) and webhook_secret.startswith("whsec_"):
        capabilities["payments"] = _status("available", "Zahlungsanbindung konfiguriert")
    else:
        capabilities["payments"] = _status("degraded", "Zahlungskonfiguration unvollständig")

    email_status, email_message = email_configuration_status()
    capabilities["email"] = _status(email_status, email_message)
    storage_status, storage_message = storage_configuration_status()
    capabilities["storage"] = _status(storage_status, storage_message)

    jobs_enabled = (os.getenv("BACKGROUND_JOBS_ENABLED") or "").strip().lower() in {"1", "true", "yes"}
    worker_active = False
    if jobs_enabled and database_available:
        threshold = datetime.now(timezone.utc) - timedelta(seconds=90)
        try:
            context = await SingleTenantResolver(
                TenancySettings.from_environment(), MongoTenantDirectory(database),
            ).resolve()
            access = TenantBusinessAccess(database, context)
            worker_active = bool(await access.worker_heartbeats.find_one({
                "status": "active", "lastSeenAt": {"$gte": threshold},
            }))
        except Exception:
            worker_active = False
    capabilities["background_jobs"] = _status(
        "available" if jobs_enabled and database_available and schema_ready and worker_active else
        "not_configured" if not jobs_enabled else "degraded",
        "Background-Worker aktiv" if worker_active and schema_ready else
        "Background-Worker nicht aktiviert" if not jobs_enabled else
        "Kein aktueller Worker-Heartbeat",
    )
    backup_tools = bool(shutil.which("mongodump") and shutil.which("mongorestore"))
    backup_destination = bool((os.getenv("BACKUP_DIRECTORY") or "").strip())
    capabilities["backups"] = _status(
        "available" if backup_tools and backup_destination else "degraded" if backup_tools else "unavailable",
        "Backup-Werkzeuge und Ziel konfiguriert" if backup_tools and backup_destination else
        "Backup-Ziel nicht konfiguriert" if backup_tools else "MongoDB-Backup-Werkzeuge fehlen",
    )
    capabilities["reconciliation"] = _status(
        "available" if database_available and schema_ready else "degraded",
        "Konsistenzprüfung verfügbar" if database_available and schema_ready else "Konsistenzprüfung nicht einsatzbereit",
    )

    environment = (os.getenv("APP_ENV") or "").strip().lower()
    runtime_configuration = validate_runtime_configuration()
    config_ready = environment in KNOWN_ENVIRONMENTS and bool(runtime_configuration["ready"])
    try:
        TenancySettings.from_environment()
    except TenancyConfigurationError:
        config_ready = False
    capabilities["configuration"] = _status(
        "available" if config_ready else "unavailable",
        "Kritische Konfiguration gültig" if config_ready else "Kritische Konfiguration ungültig",
    )
    capabilities["configuration"]["checks"] = runtime_configuration["checks"]

    ready = database_available and schema_ready and config_ready
    return {
        "status": "ready" if ready else "not_ready",
        "ready": ready,
        "capabilities": capabilities,
    }
