"""Create a verified ORDO MongoDB backup archive."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from pymongo import MongoClient

from app.backup_restore import create_backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--production-approval")
    args = parser.parse_args()
    mongo_url = os.environ["MONGO_URL"]
    database_name = os.environ["DB_NAME"]
    environment = os.environ["APP_ENV"]
    client = MongoClient(mongo_url)
    archive, manifest = create_backup(
        client, mongo_url=mongo_url, database_name=database_name, environment=environment,
        output_directory=args.output, production_approval=args.production_approval,
    )
    print(f"BACKUP_ARCHIVE={archive.name}")
    print(f"BACKUP_MANIFEST={manifest.name}")
    print("BACKUP_STATUS=complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
