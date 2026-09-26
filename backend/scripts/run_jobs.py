"""Run due tenant jobs in the modular-monolith worker process."""

from __future__ import annotations

import asyncio
import os
import secrets

from app.background_jobs import process_one_job
from app.core import client, db
from app.tenant_access import TenantBusinessAccess
from app.tenancy import MongoTenantDirectory, SingleTenantResolver, TenancySettings


async def main() -> int:
    if (os.getenv("BACKGROUND_JOBS_ENABLED") or "").strip().lower() not in {"1", "true", "yes"}:
        raise RuntimeError("BACKGROUND_JOBS_ENABLED must be explicit")
    worker_id = "worker_" + secrets.token_hex(8)
    resolver = SingleTenantResolver(
        TenancySettings.from_environment(), MongoTenantDirectory(db),
    )
    context = await resolver.resolve()
    result = await process_one_job(TenantBusinessAccess(db, context), worker_id=worker_id)
    processed = 1 if result is not None else 0
    print(f"JOBS_PROCESSED={processed}")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
