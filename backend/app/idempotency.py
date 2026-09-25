"""Tenant-scoped idempotency leases for retryable commercial mutations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import secrets
from typing import Any, Mapping

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .tenant_access import TenantBusinessAccess


KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
DEFAULT_TTL_DAYS = 30
DEFAULT_LEASE_SECONDS = 60


def _utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def request_hash(payload: Any) -> str:
    """Hash only canonical JSON so formatting/runtime differences are irrelevant."""
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_idempotency_key(value: str | None) -> str:
    key = (value or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise HTTPException(
            status_code=400,
            detail="Idempotency-Key fehlt oder besitzt ein ungültiges Format",
        )
    return key


@dataclass(frozen=True)
class IdempotencyClaim:
    record_id: str
    lease_token: str | None
    replay_response: dict[str, Any] | None = None
    recovered: bool = False

    @property
    def is_replay(self) -> bool:
        return self.replay_response is not None


class IdempotencyService:
    def __init__(
        self,
        access: TenantBusinessAccess,
        *,
        actor_id: str,
        operation: str,
        key: str,
        payload: Any,
    ) -> None:
        self.access = access
        self.actor_id = actor_id
        self.operation = operation
        self.key = require_idempotency_key(key)
        self.payload_hash = request_hash(payload)

    @property
    def _identity(self) -> dict[str, str]:
        return {
            "operation": self.operation,
            "actorId": self.actor_id,
            "key": self.key,
        }

    async def claim(self) -> IdempotencyClaim:
        now = datetime.now(timezone.utc)
        lease_token = secrets.token_urlsafe(18)
        record_id = "op-" + secrets.token_hex(12)
        document = {
            "id": record_id,
            **self._identity,
            "requestHash": self.payload_hash,
            "status": "processing",
            "workflowState": "started",
            "leaseToken": lease_token,
            "leaseUntil": now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
            "attempts": 1,
            "resourceRefs": {},
            "events": [{"state": "started", "at": now.isoformat()}],
            "createdAt": now.isoformat(),
            "updatedAt": now.isoformat(),
            "expiresAt": now + timedelta(days=DEFAULT_TTL_DAYS),
        }
        try:
            await self.access.idempotency_records.insert_one(document)
            return IdempotencyClaim(record_id, lease_token)
        except DuplicateKeyError:
            existing = await self.access.idempotency_records.find_one(self._identity)
            if not existing:
                raise HTTPException(status_code=409, detail="Vorgang wird bereits verarbeitet")
        if existing.get("requestHash") != self.payload_hash:
            raise HTTPException(
                status_code=409,
                detail="Idempotency-Key wurde bereits für einen anderen Inhalt verwendet",
            )
        if existing.get("status") == "completed":
            response = existing.get("response")
            if not isinstance(response, Mapping):
                raise HTTPException(status_code=409, detail="Abgeschlossener Vorgang besitzt kein Ergebnis")
            return IdempotencyClaim(existing["id"], None, dict(response), recovered=True)
        if existing.get("status") == "failed_terminal":
            raise HTTPException(
                status_code=409,
                detail="Vorgang wurde mit diesem Idempotency-Key bereits endgültig abgelehnt",
            )
        lease_until = _utc_datetime(existing.get("leaseUntil"))
        if existing.get("status") == "processing" and lease_until is not None and lease_until > now:
            raise HTTPException(
                status_code=409,
                detail="Vorgang wird bereits verarbeitet. Bitte kurz erneut versuchen.",
                headers={"Retry-After": str(DEFAULT_LEASE_SECONDS)},
            )
        claimed = await self.access.idempotency_records.find_one_and_update(
            {
                **self._identity,
                "requestHash": self.payload_hash,
                "$or": [
                    {"status": "failed_retryable"},
                    {"status": "processing", "leaseUntil": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "leaseToken": lease_token,
                    "leaseUntil": now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
                    "updatedAt": now.isoformat(),
                },
                "$inc": {"attempts": 1},
                "$push": {"events": {"state": "retry_started", "at": now.isoformat()}},
            },
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            raise HTTPException(status_code=409, detail="Vorgang kann derzeit nicht übernommen werden")
        return IdempotencyClaim(claimed["id"], lease_token, recovered=True)

    async def checkpoint(
        self,
        claim: IdempotencyClaim,
        state: str,
        resource_refs: Mapping[str, str] | None = None,
    ) -> None:
        if not claim.lease_token:
            return
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "$set": {
                "workflowState": state,
                "updatedAt": now.isoformat(),
                "leaseUntil": now + timedelta(seconds=DEFAULT_LEASE_SECONDS),
            },
            "$push": {"events": {"state": state, "at": now.isoformat()}},
        }
        if resource_refs:
            for name, value in resource_refs.items():
                update["$set"][f"resourceRefs.{name}"] = value
        result = await self.access.idempotency_records.update_one(
            {"id": claim.record_id, "status": "processing", "leaseToken": claim.lease_token},
            update,
        )
        if result.matched_count != 1:
            raise HTTPException(status_code=409, detail="Idempotenz-Lease wurde verloren")

    async def complete(
        self,
        claim: IdempotencyClaim,
        response: Mapping[str, Any],
        resource_refs: Mapping[str, str] | None = None,
    ) -> None:
        if not claim.lease_token:
            return
        now = datetime.now(timezone.utc)
        set_fields: dict[str, Any] = {
            "status": "completed",
            "workflowState": "completed",
            "response": dict(response),
            "completedAt": now.isoformat(),
            "updatedAt": now.isoformat(),
        }
        for name, value in (resource_refs or {}).items():
            set_fields[f"resourceRefs.{name}"] = value
        result = await self.access.idempotency_records.update_one(
            {"id": claim.record_id, "status": "processing", "leaseToken": claim.lease_token},
            {
                "$set": set_fields,
                "$unset": {"leaseToken": "", "leaseUntil": "", "lastErrorCode": ""},
                "$push": {"events": {"state": "completed", "at": now.isoformat()}},
            },
        )
        if result.matched_count != 1:
            raise HTTPException(status_code=409, detail="Vorgang wurde abgeschlossen, Status konnte aber nicht bestätigt werden")

    async def fail(
        self,
        claim: IdempotencyClaim,
        *,
        error_code: str,
        exception: BaseException | None = None,
        retryable: bool | None = None,
    ) -> None:
        if not claim.lease_token:
            return
        now = datetime.now(timezone.utc)
        if retryable is None:
            retryable = not isinstance(exception, HTTPException) or exception.status_code >= 500
        await self.access.idempotency_records.update_one(
            {"id": claim.record_id, "status": "processing", "leaseToken": claim.lease_token},
            {
                "$set": {
                    "status": "failed_retryable" if retryable else "failed_terminal",
                    "workflowState": "failed",
                    "lastErrorCode": error_code,
                    "updatedAt": now.isoformat(),
                },
                "$unset": {"leaseToken": "", "leaseUntil": ""},
                "$push": {"events": {"state": "failed", "at": now.isoformat(), "code": error_code}},
            },
        )
