"""Auth: login, me, password forgot/reset/change."""
import hashlib
from html import escape
from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from typing import Annotated
from datetime import datetime, timedelta, timezone

from ..core import (api_router, db, hash_pw, verify_pw, create_token, DUMMY_HASH,
                    strip_id, gen_reset_code, logger)
from ..audit_service import global_audit
from ..deps import current_user, membership_principal, resolve_membership_context
from ..models import Token, PublicUser, ForgotPwIn, ResetPwIn, ChangePwIn
from ..emailer import send_email, email_shell
from ..tenancy import TenantResolutionError


# Simple in-memory brute-force protection: lock an email after repeated failures.
_LOGIN_FAILS: dict = {}
_MAX_FAILS = 5
_LOCK_MINUTES = 15


def _login_locked(email: str):
    rec = _LOGIN_FAILS.get(email)
    if not rec:
        return None
    count, until = rec
    if until and until > datetime.now(timezone.utc):
        return int((until - datetime.now(timezone.utc)).total_seconds() // 60) + 1
    return None


def _register_fail(email: str):
    count, _ = _LOGIN_FAILS.get(email, (0, None))
    count += 1
    until = None
    if count >= _MAX_FAILS:
        until = datetime.now(timezone.utc) + timedelta(minutes=_LOCK_MINUTES)
    _LOGIN_FAILS[email] = (count, until)


@api_router.post("/auth/login", response_model=Token)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    requested_tenant_id: Annotated[str | None, Header(alias="X-Tenant-ID")] = None,
):
    email = form.username.strip().lower()
    mins = _login_locked(email)
    if mins:
        raise HTTPException(status_code=429, detail=f"Zu viele Versuche. Bitte in {mins} Minuten erneut versuchen.")
    user = await db.users.find_one({"email": email})
    if not user or not verify_pw(form.password, user["hashed_password"]):
        if not user:
            verify_pw(form.password, DUMMY_HASH)
        _register_fail(email)
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
    _LOGIN_FAILS.pop(email, None)
    principal = membership_principal(user, context)
    await global_audit(principal, "login")
    return {
        "access_token": create_token(principal, context),
        "user": PublicUser(**strip_id(principal)),
    }


@api_router.get("/auth/me", response_model=PublicUser)
async def me(user: Annotated[dict, Depends(current_user)]):
    return PublicUser(**strip_id(user))


@api_router.post("/auth/password/forgot")
async def forgot_password(body: ForgotPwIn):
    email = body.email.strip().lower()
    u = await db.users.find_one({"email": email})
    if u:
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
async def reset_password(body: ResetPwIn):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    email = body.email.strip().lower()
    u = await db.users.find_one({"email": email})
    if not u:
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    digest = hashlib.sha256(body.code.strip().upper().encode()).hexdigest()
    rec = await db.password_resets.find_one({"userId": u["id"], "used": False})
    if not rec:
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    # Lock the reset after too many wrong attempts to stop brute forcing.
    if rec.get("attempts", 0) >= 5:
        await db.password_resets.delete_many({"userId": u["id"]})
        raise HTTPException(status_code=429, detail="Zu viele Fehlversuche. Bitte fordern Sie einen neuen Code an.")
    if rec.get("codeHash") != digest:
        await db.password_resets.update_one({"_id": rec["_id"]}, {"$inc": {"attempts": 1}})
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    exp = rec["expiresAt"]
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    res = await db.password_resets.update_one({"_id": rec["_id"], "used": False}, {"$set": {"used": True}})
    if res.modified_count != 1:
        raise HTTPException(status_code=400, detail="Code wurde bereits verwendet")
    await db.users.update_one({"id": u["id"]}, {"$set": {"hashed_password": hash_pw(body.newPassword), "must_change_password": False}})
    return {"ok": True, "message": "Passwort geändert. Bitte neu anmelden."}


@api_router.post("/auth/password/change")
async def change_password(body: ChangePwIn, user: Annotated[dict, Depends(current_user)]):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    if not verify_pw(body.currentPassword, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    await db.users.update_one({"id": user["id"]}, {"$set": {"hashed_password": hash_pw(body.newPassword), "must_change_password": False}})
    return {"ok": True, "message": "Passwort geändert."}
