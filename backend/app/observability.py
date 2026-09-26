"""Request correlation and provider-neutral structured operational logging."""

from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
import os
import re
import secrets
import time
from typing import Any

from starlette.responses import JSONResponse

from .tenant_access import TenantScopedCollection
from .tenancy import TenantContext


_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_request_id: ContextVar[str | None] = ContextVar("ordo_request_id", default=None)
_tenant_context: ContextVar[TenantContext | None] = ContextVar("ordo_tenant_context", default=None)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_request_id() -> str | None:
    return _request_id.get()


def bind_tenant_context(context: TenantContext) -> None:
    """Bind only a server-resolved context to the current request or job."""

    if not isinstance(context, TenantContext):
        raise TypeError("Operational tenant context must be a TenantContext")
    _tenant_context.set(context)


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line without serialising arbitrary payloads."""

    def format(self, record: logging.LogRecord) -> str:
        context = _tenant_context.get()
        fields: dict[str, Any] = {
            "timestamp": utc_iso(),
            "level": record.levelname.lower(),
            "service": "ordo-api",
            "environment": (os.getenv("APP_ENV") or "unknown").strip().lower() or "unknown",
            "message": record.getMessage(),
        }
        if current_request_id() is not None:
            fields["request_id"] = current_request_id()
        if context is not None:
            fields["tenant_id"] = context.tenant_id
            if context.actor_user_id is not None:
                fields["actor_id"] = context.actor_user_id
        for name in (
            "request_id", "tenant_id", "actor_id", "operation", "duration_ms",
            "status", "error_id", "category",
        ):
            value = getattr(record, name, None)
            if value is not None:
                fields[name] = value
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_structured_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    # Uvicorn's default access line contains the complete query string. ORDO
    # uses signed newsletter links, so the middleware's path-only request log
    # is the safe access log and the duplicate Uvicorn access logger is off.
    logging.getLogger("uvicorn.access").disabled = True


async def report_operational_failure(
    access,
    logger: logging.Logger,
    *,
    operation: str,
    category: str,
) -> str:
    """Record a provider failure without accepting or exposing exception details."""

    error_id = "err_" + secrets.token_hex(12)
    context = _tenant_context.get()
    logger.warning(
        "operational_failure",
        extra={
            "request_id": current_request_id(),
            "tenant_id": context.tenant_id if context else None,
            "actor_id": context.actor_user_id if context else None,
            "operation": operation,
            "error_id": error_id,
            "category": category,
        },
    )
    if access is None:
        return error_id
    document = {
        "id": error_id,
        "category": category,
        "operation": operation,
        "status": 502,
        "requestId": current_request_id(),
        "actorId": context.actor_user_id if context else None,
        "occurredAt": utc_iso(),
        "reviewRequired": True,
        "retentionClass": "technical_log",
    }
    try:
        await access.technical_errors.insert_one(document)
    except Exception:
        logger.error(
            "technical_error_persistence_failed",
            extra={"error_id": error_id, "operation": operation},
        )
    return error_id


async def _record_error(database, *, error_id: str, category: str, operation: str, status: int) -> None:
    context = _tenant_context.get()
    if context is None:
        return
    document = {
        "id": error_id,
        "category": category,
        "operation": operation,
        "status": status,
        "requestId": current_request_id(),
        "actorId": context.actor_user_id,
        "occurredAt": utc_iso(),
        "reviewRequired": True,
        "retentionClass": "technical_log",
    }
    try:
        await TenantScopedCollection(database, "technical_errors", context).insert_one(document)
    except Exception:
        logging.getLogger("ordo.operations").error(
            "technical_error_persistence_failed",
            extra={"error_id": error_id, "operation": operation},
        )


class OperationalContextMiddleware:
    """Pure ASGI middleware so downstream context remains visible on completion."""

    def __init__(self, app, *, database) -> None:
        self.app = app
        self.database = database
        self.logger = logging.getLogger("ordo.requests")

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        supplied = headers.get(b"x-request-id", b"").decode("ascii", "ignore").strip()
        request_id = supplied if _SAFE_REFERENCE.fullmatch(supplied) else "req_" + secrets.token_hex(12)
        request_token = _request_id.set(request_id)
        tenant_token = _tenant_context.set(None)
        start = time.monotonic()
        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "")
        operation = f"{method} {path}"
        response_status = 500
        error_id: str | None = None
        error_recorded = False

        async def send_with_context(message) -> None:
            nonlocal response_status, error_id
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
                response_headers = list(message.get("headers", []))
                response_headers.append((b"x-request-id", request_id.encode("ascii")))
                if response_status >= 500:
                    error_id = "err_" + secrets.token_hex(12)
                    response_headers.append((b"x-error-id", error_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        except Exception:
            error_id = "err_" + secrets.token_hex(12)
            response_status = 500
            self.logger.error(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "operation": operation,
                    "status": response_status,
                    "error_id": error_id,
                    "category": "unhandled_exception",
                },
            )
            await _record_error(
                self.database, error_id=error_id, category="unhandled_exception",
                operation=operation, status=response_status,
            )
            error_recorded = True
            response = JSONResponse(
                status_code=500,
                content={"detail": "Technischer Fehler", "errorId": error_id, "requestId": request_id},
                headers={"X-Request-ID": request_id, "X-Error-ID": error_id},
            )
            await response(scope, receive, send)
            return
        finally:
            duration_ms = round((time.monotonic() - start) * 1000, 2)
            context = _tenant_context.get()
            self.logger.info(
                "request_completed",
                extra={
                    "request_id": request_id,
                    "tenant_id": context.tenant_id if context else None,
                    "actor_id": context.actor_user_id if context else None,
                    "operation": operation,
                    "duration_ms": duration_ms,
                    "status": response_status,
                    "error_id": error_id,
                },
            )
            if error_id and response_status >= 500 and not error_recorded:
                await _record_error(
                    self.database, error_id=error_id, category="http_5xx",
                    operation=operation, status=response_status,
                )
            _tenant_context.reset(tenant_token)
            _request_id.reset(request_token)
