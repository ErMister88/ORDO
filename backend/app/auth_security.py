"""Central token, credential-rotation, and authentication throttling policy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import hmac
from math import ceil
from typing import Iterable, Literal

import jwt
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .core import JWT_ALGORITHM, JWT_SECRET, TOKEN_MINUTES


TokenKind = Literal["tenant", "shop"]
TOKEN_ISSUER = "ordo"
TOKEN_PROFILES = {
    "tenant": {"audience": "ordo-tenant", "scope": "tenant:access"},
    "shop": {"audience": "ordo-shop", "scope": "shop:access"},
}
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_ACCOUNT_LIMIT = 8
LOGIN_IP_LIMIT = 40


class AuthStateError(ValueError):
    """The persisted identity or token contains an unsafe authentication state."""


def identity_is_active(identity: dict) -> bool:
    """Keep legacy identities active, but reject every explicit non-boolean-true state."""

    return "active" not in identity or identity.get("active") is True


def password_change_required(identity: dict) -> bool:
    """Treat malformed persisted password state as restricted, never as usable."""

    value = identity.get("must_change_password", False)
    return value is not False


def auth_version(identity: dict) -> int:
    value = identity.get("authVersion", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AuthStateError("Identity has an invalid authentication version")
    return value


def _issue_token(identity: dict, kind: TokenKind, *, context=None) -> str:
    profile = TOKEN_PROFILES[kind]
    now = datetime.now(timezone.utc)
    payload = {
        "sub": identity["id"],
        "token_type": kind,
        "scope": profile["scope"],
        "auth_version": auth_version(identity),
        "iss": TOKEN_ISSUER,
        "aud": profile["audience"],
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_MINUTES),
    }
    if kind == "tenant":
        if context is None or context.membership_id is None or context.role is None:
            raise AuthStateError("Tenant token requires a validated membership context")
        payload.update({
            # Role is a display/debug hint. Authorization always re-reads the
            # membership and never trusts this claim.
            "role": context.role,
            "tenant_id": context.tenant_id,
            "membership_id": context.membership_id,
        })
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def issue_tenant_token(identity: dict, context) -> str:
    return _issue_token(identity, "tenant", context=context)


def issue_shop_token(identity: dict) -> str:
    if identity.get("role") != "shopuser":
        raise AuthStateError("Shop tokens may only be issued to shop identities")
    return _issue_token(identity, "shop")


def decode_access_token(token: str, kind: TokenKind) -> dict:
    profile = TOKEN_PROFILES[kind]
    payload = jwt.decode(
        token,
        JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        audience=profile["audience"],
        issuer=TOKEN_ISSUER,
        options={
            "require": [
                "sub",
                "token_type",
                "scope",
                "auth_version",
                "iss",
                "aud",
                "iat",
                "exp",
            ]
        },
    )
    if (
        payload.get("token_type") != kind
        or payload.get("scope") != profile["scope"]
        or payload.get("aud") != profile["audience"]
    ):
        raise jwt.InvalidTokenError("Token type or scope does not match")
    if not isinstance(payload.get("sub"), str) or not payload["sub"]:
        raise jwt.InvalidTokenError("Token subject is invalid")
    version = payload.get("auth_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise jwt.InvalidTokenError("Token authentication version is invalid")
    if kind == "tenant" and (
        not isinstance(payload.get("tenant_id"), str)
        or not payload["tenant_id"]
        or not isinstance(payload.get("membership_id"), str)
        or not payload["membership_id"]
    ):
        raise jwt.InvalidTokenError("Tenant token context is invalid")
    return payload


def decode_actor_token(token: str) -> tuple[TokenKind, dict]:
    """Decode either token profile without accepting cross-profile confusion."""

    errors = []
    for kind in ("shop", "tenant"):
        try:
            return kind, decode_access_token(token, kind)
        except jwt.PyJWTError as exc:
            errors.append(exc)
    raise jwt.InvalidTokenError("Token does not match an accepted profile") from errors[-1]


def validate_identity_token(identity: dict, payload: dict, kind: TokenKind) -> None:
    if not identity_is_active(identity):
        raise AuthStateError("Identity is inactive")
    if auth_version(identity) != payload.get("auth_version"):
        raise AuthStateError("Token was invalidated by a credential change")
    if kind == "shop" and identity.get("role") != "shopuser":
        raise AuthStateError("Shop token does not belong to a shop identity")


def credential_version_filter(user_id: str, expected_version: int) -> dict:
    if expected_version < 0:
        raise AuthStateError("Authentication version cannot be negative")
    query = {"id": user_id}
    if expected_version == 0:
        query["$or"] = [
            {"authVersion": 0},
            {"authVersion": {"$exists": False}},
        ]
    else:
        query["authVersion"] = expected_version
    return query


async def rotate_credentials(
    database,
    identity: dict,
    *,
    hashed_password: str,
    must_change_password: bool,
) -> bool:
    """Atomically change password state and invalidate every prior token."""

    result = await database.users.update_one(
        credential_version_filter(identity["id"], auth_version(identity)),
        {
            "$set": {
                "hashed_password": hashed_password,
                "must_change_password": must_change_password,
            },
            "$inc": {"authVersion": 1},
        },
    )
    return result.modified_count == 1


@dataclass(frozen=True, slots=True)
class RateLimitKey:
    kind: str
    value: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.kind or not self.value or self.limit < 1 or self.window_seconds < 1:
            raise ValueError("Rate-limit key must be complete and positive")


class AuthRateLimitExceeded(RuntimeError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Authentication rate limit exceeded")
        self.retry_after_seconds = max(1, retry_after_seconds)


class MongoAuthRateLimiter:
    """Shared fixed-window limits backed by MongoDB and safe across workers."""

    def __init__(self, database, *, now=None) -> None:
        self._collection = database.auth_rate_limits
        self._now = now or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _normalized(value: str) -> str:
        return value.strip().lower()

    def _bucket(self, group: str, key: RateLimitKey, now: datetime) -> tuple[str, int, datetime]:
        timestamp = int(now.timestamp())
        bucket_number = timestamp // key.window_seconds
        bucket_end_timestamp = (bucket_number + 1) * key.window_seconds
        bucket_end = datetime.fromtimestamp(bucket_end_timestamp, timezone.utc)
        material = "\0".join((group, key.kind, self._normalized(key.value), str(bucket_number)))
        identifier = hmac.new(
            JWT_SECRET.encode("utf-8"),
            material.encode("utf-8"),
            sha256,
        ).hexdigest()
        return identifier, max(1, bucket_end_timestamp - timestamp), bucket_end

    async def ensure_allowed(self, group: str, keys: Iterable[RateLimitKey]) -> None:
        now = self._now()
        for key in keys:
            identifier, retry_after, _ = self._bucket(group, key, now)
            document = await self._collection.find_one({"_id": identifier})
            if document and document.get("count", 0) >= key.limit:
                raise AuthRateLimitExceeded(retry_after)

    async def record(self, group: str, keys: Iterable[RateLimitKey]) -> None:
        now = self._now()
        exceeded = 0
        for key in keys:
            identifier, retry_after, bucket_end = self._bucket(group, key, now)
            update = {
                "$inc": {"count": 1},
                "$setOnInsert": {
                    "group": group,
                    "keyKind": key.kind,
                    # Raw account and IP identifiers are intentionally not stored.
                    "expiresAt": bucket_end + timedelta(seconds=key.window_seconds),
                },
            }
            try:
                document = await self._collection.find_one_and_update(
                    {"_id": identifier},
                    update,
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                document = await self._collection.find_one_and_update(
                    {"_id": identifier},
                    {"$inc": {"count": 1}},
                    return_document=ReturnDocument.AFTER,
                )
            # Exactly `limit` attempts are allowed. A concurrent request that
            # passed the pre-check but pushes the counter above the limit is
            # still rejected here.
            if document and document.get("count", 0) > key.limit:
                exceeded = max(exceeded, retry_after)
        if exceeded:
            raise AuthRateLimitExceeded(exceeded)

    async def clear(self, group: str, keys: Iterable[RateLimitKey]) -> None:
        now = self._now()
        for key in keys:
            identifier, _, _ = self._bucket(group, key, now)
            await self._collection.delete_one({"_id": identifier})


def account_ip_keys(
    account: str,
    ip_address: str,
    *,
    account_limit: int,
    ip_limit: int,
    window_seconds: int,
) -> tuple[RateLimitKey, RateLimitKey]:
    return (
        RateLimitKey("account", account or "<empty>", account_limit, window_seconds),
        RateLimitKey("ip", ip_address or "unknown", ip_limit, window_seconds),
    )


def login_rate_keys(account: str, ip_address: str) -> tuple[RateLimitKey, RateLimitKey]:
    return account_ip_keys(
        account,
        ip_address,
        account_limit=LOGIN_ACCOUNT_LIMIT,
        ip_limit=LOGIN_IP_LIMIT,
        window_seconds=LOGIN_WINDOW_SECONDS,
    )


def retry_minutes(seconds: int) -> int:
    return max(1, ceil(seconds / 60))
