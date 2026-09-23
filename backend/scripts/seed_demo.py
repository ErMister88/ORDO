#!/usr/bin/env python3
"""Seed ORDO demo data through an explicit, non-production command."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.demo_seed import DemoSeedError, seed_demo, validate_demo_target  # noqa: E402
from app.migrations.models import safe_error_message  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed an explicitly selected ORDO demo database")
    parser.add_argument(
        "--confirm-target",
        required=True,
        help="Exact confirmation in the form <APP_ENV>:<DB_NAME>",
    )
    parser.add_argument(
        "--upgrade-known-legacy",
        action="store_true",
        help=(
            "Upgrade only the exact documented first ORDO demo inventory; "
            "fails before writes for any unknown or changed document"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run all target, tenant, conflict and inventory checks without writing",
    )
    return parser.parse_args()


async def run_seed(
    mongo_url: str,
    database_name: str,
    app_env: str,
    target_confirmation: str,
    passwords: dict[str, str],
    upgrade_known_legacy: bool = False,
    dry_run: bool = False,
) -> dict:
    client = AsyncIOMotorClient(
        mongo_url,
        serverSelectionTimeoutMS=10_000,
        connectTimeoutMS=10_000,
        tz_aware=True,
    )
    try:
        await client.admin.command("ping")
        return await seed_demo(
            client[database_name],
            app_env=app_env,
            target_confirmation=target_confirmation,
            passwords=passwords,
            upgrade_known_legacy=upgrade_known_legacy,
            dry_run=dry_run,
        )
    finally:
        client.close()


def main() -> int:
    load_dotenv(BACKEND_DIR / ".env")
    args = parse_args()
    app_env = os.getenv("APP_ENV", "")
    database_name = os.getenv("DB_NAME", "")

    try:
        normalized_env, normalized_database = validate_demo_target(
            app_env,
            database_name,
            args.confirm_target,
        )
        mongo_url = os.getenv("MONGO_URL", "").strip()
        if not mongo_url:
            raise DemoSeedError("MONGO_URL must be configured")
        passwords = {
            "admin": os.getenv("SEED_ADMIN_PASSWORD", ""),
            "sales": os.getenv("SEED_SALES_PASSWORD", ""),
            "customer": os.getenv("SEED_CUSTOMER_PASSWORD", ""),
        }
        if any(not value.strip() for value in passwords.values()):
            raise DemoSeedError("All SEED_*_PASSWORD values must be configured")

        print(f"APP_ENV={normalized_env}")
        print(f"DB_NAME={normalized_database}")
        result = asyncio.run(run_seed(
            mongo_url,
            normalized_database,
            normalized_env,
            args.confirm_target.strip(),
            passwords,
            **({"upgrade_known_legacy": True} if args.upgrade_known_legacy else {}),
            **({"dry_run": True} if args.dry_run else {}),
        ))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except DemoSeedError as exc:
        print(f"Demo seed blocked: {safe_error_message(exc)}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(
            f"Demo seed failed ({exc.__class__.__name__}): {safe_error_message(exc)}",
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
