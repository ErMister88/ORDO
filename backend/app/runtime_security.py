"""Shared abuse controls and HTTP hardening for the API boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os

from starlette.responses import JSONResponse

from .auth_security import AuthRateLimitExceeded, MongoAuthRateLimiter, RateLimitKey


DEFAULT_BODY_LIMIT = 2 * 1024 * 1024
UPLOAD_BODY_LIMIT = 12 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class AbuseRule:
    method: str
    path: str
    limit: int
    window_seconds: int
    prefix: bool = False

    def matches(self, method: str, path: str) -> bool:
        return method == self.method and (path.startswith(self.path) if self.prefix else path == self.path)


ABUSE_RULES = (
    AbuseRule("GET", "/api/shop/products", 240, 60),
    AbuseRule("POST", "/api/shop/orders", 30, 15 * 60),
    AbuseRule("POST", "/api/shop/equipment-requests", 20, 60 * 60),
    AbuseRule("POST", "/api/newsletter/subscribe", 10, 60 * 60),
    AbuseRule("POST", "/api/shop/orders/", 40, 15 * 60, prefix=True),
    AbuseRule("POST", "/api/operations/reconciliation/run", 6, 60 * 60),
)


def _body_limit(path: str) -> int:
    return UPLOAD_BODY_LIMIT if path == "/api/upload" else DEFAULT_BODY_LIMIT


def _security_headers() -> list[tuple[bytes, bytes]]:
    headers = [
        (b"x-content-type-options", b"nosniff"),
        (b"x-frame-options", b"DENY"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
        (b"content-security-policy", b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
    ]
    environment = (os.getenv("APP_ENV") or "").strip().lower()
    app_url = (os.getenv("APP_URL") or "").strip().lower()
    if environment in {"stage", "staging", "prod", "production", "live"} and app_url.startswith("https://"):
        headers.append((b"strict-transport-security", b"max-age=31536000; includeSubDomains"))
    return headers


class RuntimeSecurityMiddleware:
    """Bound request bodies, add safe headers and throttle selected abuse paths."""

    def __init__(self, app, *, database) -> None:
        self.app = app
        self.database = database

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = str(scope.get("method") or "").upper()
        path = str(scope.get("path") or "")
        raw_headers = {key.lower(): value for key, value in scope.get("headers", [])}

        async def hardened_send(message):
            if message.get("type") == "http.response.start":
                existing = {key.lower() for key, _value in message.get("headers", [])}
                message["headers"] = list(message.get("headers", [])) + [
                    header for header in _security_headers() if header[0] not in existing
                ]
            await send(message)

        content_length = raw_headers.get(b"content-length")
        maximum = _body_limit(path)
        if content_length:
            try:
                if int(content_length) > maximum:
                    await JSONResponse(status_code=413, content={"detail": "Anfrage ist zu groß"})(scope, receive, hardened_send)
                    return
            except ValueError:
                await JSONResponse(status_code=400, content={"detail": "Ungültige Content-Length"})(scope, receive, hardened_send)
                return

        rule = next((candidate for candidate in ABUSE_RULES if candidate.matches(method, path)), None)
        if rule is not None:
            peer = scope.get("client")
            client_ip = str(peer[0]) if isinstance(peer, (tuple, list)) and peer else "unknown"
            key = RateLimitKey("ip", client_ip, rule.limit, rule.window_seconds)
            try:
                limiter = MongoAuthRateLimiter(self.database, now=lambda: datetime.now(timezone.utc))
                await limiter.ensure_allowed("api_abuse", (key,))
                await limiter.record("api_abuse", (key,))
            except AuthRateLimitExceeded as exc:
                await JSONResponse(
                    status_code=429,
                    content={"detail": "Zu viele Anfragen. Bitte später erneut versuchen."},
                    headers={"Retry-After": str(exc.retry_after_seconds)},
                )(scope, receive, hardened_send)
                return
            except Exception:
                await JSONResponse(
                    status_code=503,
                    content={"detail": "Anfrageschutz ist vorübergehend nicht verfügbar"},
                )(scope, receive, hardened_send)
                return

        # Buffer at most the configured limit. This also handles chunked bodies
        # whose size cannot be trusted from Content-Length and returns 413 before
        # any route/provider sees partial input.
        body_messages = []
        consumed = 0
        while True:
            message = await receive()
            body_messages.append(message)
            if message.get("type") != "http.request":
                break
            consumed += len(message.get("body", b""))
            if consumed > maximum:
                await JSONResponse(status_code=413, content={"detail": "Anfrage ist zu groß"})(scope, receive, hardened_send)
                return
            if not message.get("more_body", False):
                break
        index = 0

        async def replay_receive():
            nonlocal index
            if index < len(body_messages):
                message = body_messages[index]
                index += 1
                return message
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay_receive, hardened_send)
