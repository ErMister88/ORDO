"""Print a secret-free runtime readiness report and return a useful exit code."""

from __future__ import annotations

import asyncio
import json
import os

from motor.motor_asyncio import AsyncIOMotorClient

from app.capabilities import capability_snapshot
from app.production_config import validate_runtime_configuration


PRODUCTION_PROVIDER_CAPABILITIES = (
    "payments", "storage", "email", "background_jobs", "accounting", "backups",
)
PRODUCTION_CONFIGURATION_CHECKS = {
    "payments": "payments",
    "storage": "storage",
    "email": "email",
    "background_jobs": "worker",
    "accounting": "accounting",
}


def _enabled(name: str, environment: dict[str, str]) -> bool:
    return (environment.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def build_release_gate(configuration: dict, runtime: dict, environment: dict[str, str] | None = None) -> dict:
    env = dict(os.environ if environment is None else environment)
    app_env = (env.get("APP_ENV") or "").strip().lower()
    base_ready = bool(configuration["ready"] and runtime["ready"])
    staging_ready = app_env in {"stage", "staging"} and base_ready
    capabilities = runtime.get("capabilities", {})
    configuration_checks = {
        row.get("name"): row.get("status")
        for row in configuration.get("checks", [])
        if isinstance(row, dict)
    }
    missing_production = [
        name for name in PRODUCTION_PROVIDER_CAPABILITIES
        if capabilities.get(name, {}).get("status") != "available"
        or (
            name in PRODUCTION_CONFIGURATION_CHECKS
            and configuration_checks.get(PRODUCTION_CONFIGURATION_CHECKS[name]) != "configured"
        )
    ]
    acknowledgements = {
        "accountingDecisions": _enabled("PRODUCTION_ACCOUNTING_DECISIONS_CONFIRMED", env),
        "isolatedRestore": _enabled("PRODUCTION_RESTORE_VERIFIED", env),
        "monitoring": _enabled("PRODUCTION_MONITORING_ENABLED", env),
    }
    production_ready = bool(
        app_env in {"prod", "production", "live"}
        and base_ready
        and not missing_production
        and all(acknowledgements.values())
    )
    return {
        "staging": {"status": "READY" if staging_ready else "NOT_READY", "ready": staging_ready},
        "production": {
            "status": "READY" if production_ready else "NOT_READY",
            "ready": production_ready,
            "missingCapabilities": missing_production,
            "acknowledgements": acknowledgements,
        },
    }


async def build_report(database) -> dict:
    configuration = validate_runtime_configuration()
    runtime = await capability_snapshot(database)
    ready = bool(configuration["ready"] and runtime["ready"])
    release_gate = build_release_gate(configuration, runtime)
    return {
        "status": "READY" if ready else "NOT_READY",
        "ready": ready,
        "releaseGate": release_gate,
        "configuration": configuration,
        "runtime": runtime,
    }


def report_is_ready_for_environment(
    report: dict, environment: dict[str, str] | None = None,
) -> bool:
    env = os.environ if environment is None else environment
    app_env = (env.get("APP_ENV") or "").strip().lower()
    if app_env in {"stage", "staging"}:
        return bool(report["releaseGate"]["staging"]["ready"])
    if app_env in {"prod", "production", "live"}:
        return bool(report["releaseGate"]["production"]["ready"])
    return bool(report["ready"])


def main() -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5_000)
    try:
        result = asyncio.run(build_report(client[os.environ["DB_NAME"]]))
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if report_is_ready_for_environment(result) else 1


if __name__ == "__main__":
    raise SystemExit(main())
