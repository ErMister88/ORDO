"""Auth: login, me, password forgot/reset/change."""
import hashlib
from html import escape
from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import (api_router, db, hash_pw, verify_pw, DUMMY_HASH,
                    strip_id, gen_reset_code, logger)
from ..audit_service import global_audit
from ..auth_security import (
    AuthRateLimitExceeded,
    AuthStateError,
    MongoAuthRateLimiter,
    account_ip_keys,
    identity_is_active,
    issue_tenant_token,
    login_rate_keys,
    retry_minutes,
    rotate_credentials,
)
from ..deps import (
    authenticated_tenant_principal,
    membership_principal,
    resolve_membership_context,
)
from ..models import Token, PublicUser, ForgotPwIn, ResetPwIn, ChangePwIn
from ..emailer import send_email, email_shell
from ..tenancy import TenantResolutionError


RECOVERY_WINDOW_SECONDS = 60 * 60
RECOVERY_ACCOUNT_LIMIT = 6
RECOVERY_IP_LIMIT = 30
CHANGE_WINDOW_SECONDS = 15 * 60
CHANGE_ACCOUNT_LIMIT = 8
CHANGE_IP_LIMIT = 40


def _client_ip(request: Request | None) -> str:
    # Do not trust a client-controlled forwarded header. The deployment proxy
    # must expose the effective peer through ASGI's request.client contract.
    return request.client.host if request and request.client else "unknown"


def _limit_error(exc: AuthRateLimitExceeded) -> HTTPException:
    minutes = retry_minutes(exc.retry_after_seconds)
    return HTTPException(
        status_code=429,
        detail=f"Zu viele Authentifizierungsversuche. Bitte in {minutes} Minuten erneut versuchen.",
        headers={"Retry-After": str(exc.retry_after_seconds)},
    )


async def _ensure_allowed(group: str, keys) -> None:
    try:
        await MongoAuthRateLimiter(db).ensure_allowed(group, keys)
    except AuthRateLimitExceeded as exc:
        raise _limit_error(exc) from exc


async def _record_attempt(group: str, keys) -> None:
    try:
        await MongoAuthRateLimiter(db).record(group, keys)
    except AuthRateLimitExceeded as exc:
        raise _limit_error(exc) from exc


@api_router.post("/auth/login", response_model=Token)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request = None,
    requested_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
):
    email = form.username.strip().lower()
    keys = login_rate_keys(email, _client_ip(request))
    await _ensure_allowed("credential_login", keys)
    user = await db.users.find_one({"email": email})
    password_ok = verify_pw(
        form.password,
        user.get("hashed_password", DUMMY_HASH) if user else DUMMY_HASH,
    )
    if (
        not user
        or not identity_is_active(user)
        or not password_ok
    ):
        await _record_attempt("credential_login", keys)
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    try:
        context = await resolve_membership_context(
            user["id"],
            requested_tenant_id=requested_tenant_id,
        )
    except (TenantResolutionError, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=403,
            detail="Keine eindeutige aktive Tenant-Mitgliedschaft",
        ) from exc
    try:
        token = issue_tenant_token(user, context)
    except AuthStateError as exc:
        raise HTTPException(status_code=403, detail="Konto nicht verfügbar") from exc
    await MongoAuthRateLimiter(db).clear("credential_login", keys[:1])
    principal = membership_principal(user, context)
    await global_audit(principal, "login")
    return {
        "access_token": token,
        "user": PublicUser(**strip_id(principal)),
    }


@api_router.get("/auth/me", response_model=PublicUser)
async def me(user: Annotated[dict, Depends(authenticated_tenant_principal)]):
    return PublicUser(**strip_id(user))


@api_router.post("/auth/password/forgot")
async def forgot_password(body: ForgotPwIn, request: Request = None):
    email = body.email.strip().lower()
    keys = account_ip_keys(
        email,
        _client_ip(request),
        account_limit=RECOVERY_ACCOUNT_LIMIT,
        ip_limit=RECOVERY_IP_LIMIT,
        window_seconds=RECOVERY_WINDOW_SECONDS,
    )
    await _ensure_allowed("password_recovery_request", keys)
    await _record_attempt("password_recovery_request", keys)
    u = await db.users.find_one({"email": email})
    if u and identity_is_active(u):
        code, digest = gen_reset_code()
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.password_resets.delete_many({"userId": u["id"]})
        await db.password_resets.insert_one({"userId": u["id"], "codeHash": digest, "expiresAt": expires, "used": False})
        try:
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(u.get('name', ''))},<br>"
                "Ihr Sicherheitscode zum Zur&uuml;cksetzen des Passworts lautet:</p>"
                f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;color:#0B1B3D;margin:0 0 12px'>{code}</p>"
                "<p style='margin:0;color:#8A90A2;font-size:13px'>G&uuml;ltig f&uuml;r 30 Minuten. "
                "Falls Sie das nicht angefordert haben, ignorieren Sie diese E-Mail.</p>"
            )
            html = email_shell("Passwort zur&uuml;cksetzen", "Sie haben einen Sicherheitscode angefordert.", inner)
            await send_email(to=email, subject="Ihr Code zum Zurücksetzen des Passworts", html=html)
        except Exception as e:
            logger.warning(f"E-Mail (Reset-Code) fehlgeschlagen: {e}")
    return {"ok": True, "message": "Falls die E-Mail existiert, wurde ein Code gesendet."}


@api_router.post("/auth/password/reset")
async def reset_password(body: ResetPwIn, request: Request = None):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    email = body.email.strip().lower()
    keys = account_ip_keys(
        email,
        _client_ip(request),
        account_limit=RECOVERY_ACCOUNT_LIMIT,
        ip_limit=RECOVERY_IP_LIMIT,
        window_seconds=RECOVERY_WINDOW_SECONDS,
    )
    await _ensure_allowed("password_recovery_verify", keys)
    u = await db.users.find_one({"email": email})
    if not u or not identity_is_active(u):
        await _record_attempt("password_recovery_verify", keys)
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    digest = hashlib.sha256(body.code.strip().upper().encode()).hexdigest()
    rec = await db.password_resets.find_one({"userId": u["id"], "used": False})
    if not rec:
        await _record_attempt("password_recovery_verify", keys)
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    # Lock the reset after too many wrong attempts to stop brute forcing.
    if rec.get("attempts", 0) >= 5:
        await db.password_resets.delete_many({"userId": u["id"]})
        await _record_attempt("password_recovery_verify", keys)
        raise HTTPException(status_code=429, detail="Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an.")
    if rec.get("codeHash") != digest:
        await db.password_resets.update_one({"_id": rec["_id"]}, {"$inc": {"attempts": 1}})
        await _record_attempt("password_recovery_verify", keys)
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    exp = rec["expiresAt"]
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        await _record_attempt("password_recovery_verify", keys)
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    res = await db.password_resets.update_one({"_id": rec["_id"], "used": False}, {"$set": {"used": True}})
    if res.modified_count != 1:
        raise HTTPException(status_code=400, detail="Code wurde bereits verwendet")
    if not await rotate_credentials(
        db,
        u,
        hashed_password=hash_pw(body.newPassword),
        must_change_password=False,
    ):
        raise HTTPException(status_code=409, detail="Passwort wurde parallel geändert")
    await MongoAuthRateLimiter(db).clear("password_recovery_verify", keys[:1])
    return {"ok": True, "message": "Passwort geändert. Bitte neu anmelden."}


@api_router.post("/auth/password/change")
async def change_password(
    body: ChangePwIn,
    user: Annotated[dict, Depends(authenticated_tenant_principal)],
    request: Request = None,
):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    keys = account_ip_keys(
        user["id"],
        _client_ip(request),
        account_limit=CHANGE_ACCOUNT_LIMIT,
        ip_limit=CHANGE_IP_LIMIT,
        window_seconds=CHANGE_WINDOW_SECONDS,
    )
    await _ensure_allowed("password_change", keys)
    if not verify_pw(body.currentPassword, user["hashed_password"]):
        await _record_attempt("password_change", keys)
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    if not await rotate_credentials(
        db,
        user,
        hashed_password=hash_pw(body.newPassword),
        must_change_password=False,
    ):
        raise HTTPException(status_code=409, detail="Passwort wurde parallel geändert")
    await MongoAuthRateLimiter(db).clear("password_change", keys[:1])
    return {"ok": True, "message": "Passwort geändert. Bitte neu anmelden."}
