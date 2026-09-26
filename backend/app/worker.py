"""Long-running Mongo-backed worker runtime with observable heartbeats."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import signal

from .background_jobs import BackgroundJobQueue, process_one_job
from .tenant_access import TenantBusinessAccess


IDLE_SECONDS = 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def record_worker_heartbeat(
    access: TenantBusinessAccess,
    *,
    worker_id: str,
    status: str,
    processed: int,
) -> None:
    await access.worker_heartbeats.update_one(
        {"workerId": worker_id},
        {"$set": {
            "workerId": worker_id, "status": status, "lastSeenAt": _now(),
            "processed": processed, "updatedAt": _now(),
        }, "$setOnInsert": {"startedAt": _now()}},
        upsert=True,
    )


async def schedule_pending_email_jobs(access: TenantBusinessAccess) -> int:
    """Recover the narrow gap between outbox persistence and job enqueue."""
    rows = await access.email_outbox.find({"status": "pending"}).sort("createdAt", 1).to_list(200)
    queue = BackgroundJobQueue(access)
    for row in rows:
        await queue.enqueue(
            "email.deliver",
            actor_id="system:email-outbox",
            idempotency_key=f"email:{row['id']}",
            payload={"outboxId": row["id"]},
        )
    return len(rows)


async def run_worker_once(access: TenantBusinessAccess, *, worker_id: str) -> bool:
    await schedule_pending_email_jobs(access)
    return await process_one_job(access, worker_id=worker_id) is not None


async def run_worker(
    access: TenantBusinessAccess,
    *,
    worker_id: str,
    stop_event: asyncio.Event | None = None,
) -> int:
    stop = stop_event or asyncio.Event()
    loop = asyncio.get_running_loop()
    if stop_event is None:
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
    processed = 0
    await record_worker_heartbeat(access, worker_id=worker_id, status="active", processed=processed)
    try:
        while not stop.is_set():
            try:
                did_work = await run_worker_once(access, worker_id=worker_id)
                processed += int(did_work)
            except Exception:
                # The job/outbox records retain safe error references. The
                # process stays alive so one provider outage cannot stop ORDO.
                did_work = False
            await record_worker_heartbeat(access, worker_id=worker_id, status="active", processed=processed)
            if not did_work:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=IDLE_SECONDS)
                except asyncio.TimeoutError:
                    pass
    finally:
        await record_worker_heartbeat(access, worker_id=worker_id, status="stopped", processed=processed)
    return processed
