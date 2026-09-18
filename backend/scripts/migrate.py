#!/usr/bin/env python3
"""Run ORDO MongoDB migrations independently of FastAPI startup."""

from __future__ import annotations

import argparse
from hmac import compare_digest
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from pymongo import MongoClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.migrations.models import MigrationError, safe_error_message  # noqa: E402
from app.migrations.runner import (  # noqa: E402
    KNOWN_ENVIRONMENTS,
    MigrationRunner,
    PRODUCTION_ENVIRONMENTS,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run ORDO MongoDB schema migrations")
    parser.add_argument("--dry-run", action="store_true", help="Plan migrations without any writes")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="Allow production only with matching MIGRATION_PRODUCTION_APPROVAL",
    )
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=os.getenv("MIGRATION_LEASE_SECONDS", "60"),
        help="Migration lease duration; the runner renews it automatically",
    )
    return parser.parse_args()


def production_approval_for_target(
    app_env: str,
    database_name: str,
    flag: bool,
) -> str | None:
    app_env = app_env.strip().lower()
    database_name = database_name.strip()
    if app_env not in PRODUCTION_ENVIRONMENTS:
        return None
    expected = f"{app_env}:{database_name}"
    supplied = os.getenv("MIGRATION_PRODUCTION_APPROVAL", "")
    if flag and compare_digest(supplied, expected):
        return supplied
    return None


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")
    args = parse_args()
    app_env = os.getenv("APP_ENV", "").strip().lower()
    database_name = os.getenv("DB_NAME", "").strip()

    print(f"APP_ENV={app_env or '<missing>'}")
    print(f"DB_NAME={database_name or '<missing>'}")

    if not app_env:
        print("Migration error: APP_ENV must be configured explicitly", file=sys.stderr)
        return 2
    if app_env not in KNOWN_ENVIRONMENTS:
        print(
            f"Migration error: unknown APP_ENV {app_env!r}; refusing unsafe default",
            file=sys.stderr,
        )
        return 2
    if not database_name:
        print("Migration error: DB_NAME must be configured explicitly", file=sys.stderr)
        return 2
    mongo_url = os.getenv("MONGO_URL", "").strip()
    if not mongo_url:
        print("Migration error: MONGO_URL must be configured", file=sys.stderr)
        return 2

    production_approval = production_approval_for_target(
        app_env,
        database_name,
        args.allow_production,
    )
    if app_env in PRODUCTION_ENVIRONMENTS and not args.dry_run and not production_approval:
        print(
            "Migration error: production requires --allow-production and "
            "MIGRATION_PRODUCTION_APPROVAL=<APP_ENV>:<DB_NAME>",
            file=sys.stderr,
        )
        return 3

    application_version = (
        os.getenv("APP_VERSION")
        or os.getenv("RENDER_GIT_COMMIT")
        or "unknown"
    )
    client = MongoClient(
        mongo_url,
        tz_aware=True,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
    )
    try:
        client.admin.command("ping")
        runner = MigrationRunner(
            client[database_name],
            app_env=app_env,
            database_name=database_name,
            application_version=application_version,
            production_approval=production_approval,
            lease_seconds=args.lease_seconds,
        )
        report = runner.run(dry_run=args.dry_run)
        print(json.dumps(report.as_dict(), indent=2, sort_keys=True, default=str))
        return 0
    except MigrationError as exc:
        print(f"Migration error: {safe_error_message(exc)}", file=sys.stderr)
        return 4
    except Exception as exc:
        print(
            f"Migration failed ({exc.__class__.__name__}): {safe_error_message(exc)}",
            file=sys.stderr,
        )
        return 5
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
