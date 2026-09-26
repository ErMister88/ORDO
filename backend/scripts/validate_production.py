"""Print a secret-free runtime readiness report and return a useful exit code."""

from __future__ import annotations

import asyncio
import json
import os

from motor.motor_asyncio import AsyncIOMotorClient

from app.capabilities import capability_snapshot
from app.production_config import validate_runtime_configuration


async def build_report(database) -> dict:
    configuration = validate_runtime_configuration()
    runtime = await capability_snapshot(database)
    ready = bool(configuration["ready"] and runtime["ready"])
    return {
        "status": "READY" if ready else "NOT_READY",
        "ready": ready,
        "configuration": configuration,
        "runtime": runtime,
    }


def main() -> int:
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5_000)
    try:
        result = asyncio.run(build_report(client[os.environ["DB_NAME"]]))
    finally:
        client.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
