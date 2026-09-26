"""Tenant-admin operations center, reconciliation and safe recovery controls."""

from typing import Annotated, Literal

from fastapi import Depends, Header

from ..background_jobs import BackgroundJobQueue
from ..capabilities import capability_snapshot
from ..core import api_router, db, strip_id
from ..deps import require_roles, tenant_business_access
from ..idempotency import require_idempotency_key
from ..reconciliation import run_reconciliation
from ..tenant_access import TenantBusinessAccess


def _public_row(row: dict, allowed: set[str]) -> dict:
    return {key: value for key, value in strip_id(row).items() if key in allowed}


@api_router.get("/operations/status")
async def operations_status(user: Annotated[dict, Depends(require_roles("admin"))]):
    result = await capability_snapshot(db)
    context = user.get("_tenant_context")
    result["tenantId"] = context.tenant_id if context else None
    return result


@api_router.get("/operations")
async def list_commercial_operations(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    status: Literal["processing", "failed_retryable", "failed_terminal", "completed"] | None = None,
):
    query = {"status": status} if status else {"status": {"$ne": "completed"}}
    rows = await access.idempotency_records.find(query).sort("updatedAt", -1).to_list(500)
    allowed = {"id", "operation", "actorId", "status", "workflowState", "attempts", "resourceRefs", "events", "createdAt", "updatedAt", "completedAt", "lastErrorCode"}
    return [_public_row(row, allowed) for row in rows]


@api_router.get("/operations/jobs")
async def list_background_jobs(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.background_jobs.find({}).sort("updatedAt", -1).to_list(200)
    allowed = {"id", "jobType", "status", "attempts", "createdAt", "startedAt", "finishedAt", "nextRetryAt", "lastErrorReference", "retryAllowed", "reviewRequired", "result"}
    return [_public_row(row, allowed) for row in rows]


@api_router.get("/operations/communications")
async def communication_status(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    pending_mail = await access.email_outbox.count_documents({"status": {"$in": ["pending", "processing"]}})
    failed_mail = await access.email_outbox.count_documents({"status": "failed"})
    dead_mail = await access.email_outbox.count_documents({"status": "dead"})
    dead_jobs = await access.background_jobs.count_documents({"status": "dead"})
    storage_errors = await access.technical_errors.count_documents({"category": "storage"})
    workers = await access.worker_heartbeats.find({}).sort("lastSeenAt", -1).to_list(1)
    worker = workers[0] if workers else None
    return {
        "mail": {"pending": pending_mail, "failed": failed_mail, "dead": dead_mail},
        "jobs": {"dead": dead_jobs},
        "storage": {"errors": storage_errors},
        "worker": {
            "status": worker.get("status") if worker else "unknown",
            "lastSeenAt": worker.get("lastSeenAt") if worker else None,
        },
    }


@api_router.post("/operations/jobs/{job_id}/retry")
async def retry_background_job(
    job_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    return await BackgroundJobQueue(access).retry(job_id)


@api_router.post("/operations/reconciliation/jobs")
async def enqueue_reconciliation(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    key = require_idempotency_key(idempotency_key)
    row = await BackgroundJobQueue(access).enqueue(
        "reconcile.tenant", actor_id=user["id"], idempotency_key=key,
        payload={"actorId": user["id"]},
    )
    return {"id": row["id"], "status": row["status"]}


@api_router.post("/operations/reconciliation/run")
async def run_reconciliation_now(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    result = await run_reconciliation(access, actor_id=user["id"])
    return _public_row(result, {"id", "status", "startedAt", "finishedAt", "counts", "issues", "issueCount", "truncated", "scanTruncated", "documentsInspected"})


@api_router.get("/operations/reconciliation/latest")
async def latest_reconciliation(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.reconciliation_runs.find({}).sort("finishedAt", -1).to_list(1)
    if not rows:
        return None
    return _public_row(rows[0], {"id", "status", "startedAt", "finishedAt", "counts", "issues", "issueCount", "truncated", "scanTruncated", "documentsInspected"})


@api_router.get("/operations/problems")
async def list_operational_problems(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    problems: list[dict] = []
    operations = await access.idempotency_records.find({"status": {"$in": ["processing", "failed_retryable", "failed_terminal"]}}).sort("updatedAt", -1).to_list(100)
    problems.extend({"type": "operation", "id": row.get("id"), "status": row.get("status"), "occurredAt": row.get("updatedAt"), "errorReference": row.get("lastErrorCode"), "resource": row.get("resourceRefs") or {}, "retryAllowed": False, "reviewRequired": True} for row in operations)

    payment_events = await access.payment_provider_events.find({"status": {"$in": ["processing", "failed_retryable", "failed_terminal"]}}).sort("updatedAt", -1).to_list(100)
    problems.extend({"type": "payment", "id": row.get("id"), "status": row.get("status"), "occurredAt": row.get("updatedAt"), "errorReference": row.get("lastErrorCode"), "resource": {"type": row.get("resourceType"), "id": row.get("resourceId")}, "retryAllowed": row.get("status") == "failed_retryable", "reviewRequired": True} for row in payment_events)

    jobs = await access.background_jobs.find({"status": {"$in": ["failed", "dead"]}}).sort("updatedAt", -1).to_list(100)
    problems.extend({"type": "background_job", "id": row.get("id"), "status": row.get("status"), "occurredAt": row.get("updatedAt"), "errorReference": row.get("lastErrorReference"), "resource": {"type": row.get("jobType")}, "retryAllowed": bool(row.get("retryAllowed")), "reviewRequired": bool(row.get("reviewRequired"))} for row in jobs)

    mail = await access.email_outbox.find({"status": {"$in": ["failed", "dead"]}}).sort("updatedAt", -1).to_list(100)
    problems.extend({
        "type": "email", "id": row.get("id"), "status": row.get("status"),
        "occurredAt": row.get("updatedAt"), "errorReference": row.get("lastErrorReference"),
        "resource": {"type": row.get("resourceType"), "id": row.get("resourceId")},
        "retryAllowed": False, "reviewRequired": row.get("status") == "dead",
    } for row in mail)

    errors = await access.technical_errors.find({}).sort("occurredAt", -1).to_list(100)
    problems.extend({"type": "technical_error", "id": row.get("id"), "status": "failed", "occurredAt": row.get("occurredAt"), "errorReference": row.get("id"), "resource": {"operation": row.get("operation")}, "retryAllowed": False, "reviewRequired": True} for row in errors)

    latest = await access.reconciliation_runs.find({}).sort("finishedAt", -1).to_list(1)
    if latest:
        problems.extend({"type": "reconciliation", "id": issue.get("issueKey"), "status": issue.get("category"), "occurredAt": latest[0].get("finishedAt"), "errorReference": issue.get("code"), "resource": {"type": issue.get("resourceType"), "id": issue.get("resourceId")}, "message": issue.get("message"), "retryAllowed": False, "reviewRequired": bool(issue.get("reviewRequired"))} for issue in latest[0].get("issues", []))

    problems.sort(key=lambda row: str(row.get("occurredAt") or ""), reverse=True)
    return problems[:300]
