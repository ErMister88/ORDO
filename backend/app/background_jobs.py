"""Small MongoDB-backed tenant job queue with leases and safe retries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import secrets
from typing import Any, Mapping

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .reconciliation import run_reconciliation
from .tenant_access import TenantBusinessAccess


LEASE_SECONDS = 120
MAX_ATTEMPTS = 5
SAFE_RETRY_JOB_TYPES = frozenset({"reconcile.tenant", "email.deliver"})


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class JobClaim:
    job_id: str
    lease_token: str
    job_type: str
    attempts: int
    payload: Mapping[str, Any]


class BackgroundJobQueue:
    def __init__(self, access: TenantBusinessAccess) -> None:
        self.access = access

    async def enqueue(
        self,
        job_type: str,
        *,
        actor_id: str,
        idempotency_key: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if job_type not in SAFE_RETRY_JOB_TYPES:
            raise ValueError("Unknown or unsafe background job type")
        if not idempotency_key or len(idempotency_key) > 128:
            raise ValueError("Background job idempotency key is invalid")
        now = _now()
        document = {
            "id": "job_" + secrets.token_hex(10),
            "jobType": job_type,
            "actorId": actor_id,
            "idempotencyKey": idempotency_key,
            "payload": dict(payload or {}),
            "status": "pending",
            "attempts": 0,
            "createdAt": now,
            "updatedAt": now,
            "nextRetryAt": now,
            "retryAllowed": True,
            "reviewRequired": False,
            "retentionClass": "background_job",
        }
        try:
            await self.access.background_jobs.insert_one(document)
            return document
        except DuplicateKeyError:
            existing = await self.access.background_jobs.find_one({
                "jobType": job_type, "idempotencyKey": idempotency_key,
            })
            if not existing:
                raise
            return existing

    async def claim_next(self, *, worker_id: str) -> JobClaim | None:
        now = _now()
        lease_token = secrets.token_urlsafe(18)
        job = await self.access.background_jobs.find_one_and_update(
            {
                "$or": [
                    {"status": "pending", "nextRetryAt": {"$lte": now}},
                    {"status": "failed", "nextRetryAt": {"$lte": now}, "attempts": {"$lt": MAX_ATTEMPTS}},
                    {"status": "processing", "leaseUntil": {"$lte": now}},
                ]
            },
            {
                "$set": {
                    "status": "processing", "workerId": worker_id,
                    "leaseToken": lease_token, "leaseUntil": now + timedelta(seconds=LEASE_SECONDS),
                    "startedAt": now, "updatedAt": now, "reviewRequired": False,
                },
                "$inc": {"attempts": 1},
                "$unset": {"finishedAt": "", "lastErrorReference": ""},
            },
            sort=[("nextRetryAt", 1), ("createdAt", 1)],
            return_document=ReturnDocument.AFTER,
        )
        if not job:
            return None
        return JobClaim(
            job_id=job["id"], lease_token=lease_token, job_type=job["jobType"],
            attempts=job["attempts"], payload=job.get("payload") or {},
        )

    async def complete(self, claim: JobClaim, result: Mapping[str, Any]) -> None:
        now = _now()
        update = await self.access.background_jobs.update_one(
            {
                "id": claim.job_id,
                "status": "processing",
                "leaseToken": claim.lease_token,
                "leaseUntil": {"$gt": now},
            },
            {
                "$set": {"status": "completed", "result": dict(result), "finishedAt": now, "updatedAt": now},
                "$unset": {"leaseToken": "", "leaseUntil": "", "workerId": "", "lastErrorReference": ""},
            },
        )
        if update.matched_count != 1:
            raise RuntimeError("Background job lease was lost before completion")

    async def heartbeat(self, claim: JobClaim) -> None:
        now = _now()
        update = await self.access.background_jobs.update_one(
            {
                "id": claim.job_id,
                "status": "processing",
                "leaseToken": claim.lease_token,
                "leaseUntil": {"$gt": now},
            },
            {"$set": {"leaseUntil": now + timedelta(seconds=LEASE_SECONDS), "updatedAt": now}},
        )
        if update.matched_count != 1:
            raise RuntimeError("Background job lease was lost during heartbeat")

    async def fail(self, claim: JobClaim, *, error_reference: str) -> None:
        now = _now()
        dead = claim.attempts >= MAX_ATTEMPTS
        delay = min(3600, 30 * (2 ** max(0, claim.attempts - 1)))
        update = await self.access.background_jobs.update_one(
            {
                "id": claim.job_id,
                "status": "processing",
                "leaseToken": claim.lease_token,
                "leaseUntil": {"$gt": now},
            },
            {
                "$set": {
                    "status": "dead" if dead else "failed",
                    "lastErrorReference": error_reference,
                    "finishedAt": now,
                    "updatedAt": now,
                    "nextRetryAt": now + timedelta(seconds=delay),
                    "reviewRequired": dead,
                },
                "$unset": {"leaseToken": "", "leaseUntil": "", "workerId": ""},
            },
        )
        if update.matched_count != 1:
            raise RuntimeError("Background job lease was lost while recording failure")

    async def retry(self, job_id: str) -> dict[str, Any]:
        now = _now()
        job = await self.access.background_jobs.find_one({"id": job_id})
        if not job:
            raise HTTPException(status_code=404, detail="Job nicht gefunden")
        if job.get("jobType") not in SAFE_RETRY_JOB_TYPES or not job.get("retryAllowed"):
            raise HTTPException(status_code=409, detail="Dieser Vorgang kann nicht sicher wiederholt werden")
        if job.get("status") not in {"failed", "dead"}:
            raise HTTPException(status_code=409, detail="Dieser Job benötigt keinen erneuten Versuch")
        await self.access.background_jobs.update_one(
            {"id": job_id, "status": job["status"]},
            {
                "$set": {"status": "pending", "nextRetryAt": now, "updatedAt": now, "reviewRequired": False},
                "$unset": {"finishedAt": "", "lastErrorReference": ""},
            },
        )
        return {"id": job_id, "status": "pending"}


async def process_one_job(access: TenantBusinessAccess, *, worker_id: str) -> dict[str, Any] | None:
    queue = BackgroundJobQueue(access)
    claim = await queue.claim_next(worker_id=worker_id)
    if claim is None:
        return None
    try:
        if claim.job_type == "reconcile.tenant":
            result = await run_reconciliation(
                access,
                actor_id=str(claim.payload.get("actorId") or "system:worker"),
                run_id=f"rec_job_{claim.job_id}",
                checkpoint=lambda: queue.heartbeat(claim),
            )
            completion = {"resourceType": "reconciliation_run", "resourceId": result["id"]}
        elif claim.job_type == "email.deliver":
            from .emailer import deliver_outbox_email
            outbox_id = claim.payload.get("outboxId")
            if not isinstance(outbox_id, str) or not outbox_id:
                raise RuntimeError("Email job has no valid outbox reference")
            result = await deliver_outbox_email(access, outbox_id, attempt=claim.attempts)
            completion = result
        else:
            raise RuntimeError("Unsupported background job type")
        await queue.complete(claim, completion)
        return result
    except Exception:
        error_reference = "joberr_" + secrets.token_hex(10)
        await queue.fail(claim, error_reference=error_reference)
        raise
