"""Restore an ORDO backup into a new isolated database and verify it."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pymongo import MongoClient

from app.backup_restore import restore_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target-db", required=True)
    args = parser.parse_args()
    mongo_url = os.environ["MONGO_URL"]
    client = MongoClient(mongo_url)
    result = restore_backup(
        client, mongo_url=mongo_url, manifest_path=args.manifest,
        target_database=args.target_db, environment=os.environ["APP_ENV"],
    )
    print(f"RESTORE_TARGET={args.target_db}")
    print(f"RESTORE_VERIFIED={'yes' if result['verified'] else 'no'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
