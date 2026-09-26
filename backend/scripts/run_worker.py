"""Start the persistent ORDO background worker."""

from __future__ import annotations

import asyncio
import os
import secrets

from app.core import client, db
from app.tenant_access import TenantBusinessAccess
from app.tenancy import MongoTenantDirectory, SingleTenantResolver, TenancySettings
from app.worker import run_worker


async def main() -> int:
    if (os.getenv("BACKGROUND_JOBS_ENABLED") or "").strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError("BACKGROUND_JOBS_ENABLED must be explicit")
    context = await SingleTenantResolver(
        TenancySettings.from_environment(), MongoTenantDirectory(db),
    ).resolve()
    worker_id = (os.getenv("WORKER_ID") or "").strip() or "worker_" + secrets.token_hex(8)
    try:
        processed = await run_worker(TenantBusinessAccess(db, context), worker_id=worker_id)
        print(f"JOBS_PROCESSED={processed}")
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
