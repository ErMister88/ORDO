"""Tenant-admin user and membership management."""
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Annotated

from fastapi import Depends, HTTPException

from ..audit_service import global_audit, tenant_audit
from ..auth_security import (
    AuthRateLimitExceeded,
    MongoAuthRateLimiter,
    RateLimitKey,
    retry_minutes,
    rotate_credentials,
)
from ..core import api_router, db, gen_reset_code, hash_pw, random_password
from ..deps import require_roles, tenant_business_access
from ..models import CreateUserIn
from ..tenant_access import TenantBusinessAccess
from ..tenancy import MongoMembershipDirectory
from ..emailer import email_shell, send_email


async def _queue_account_invitation(
    access: TenantBusinessAccess,
    identity: dict,
    *,
    reason: str,
) -> dict:
    """Issue the existing password-reset credential without returning it to UI."""
    code, digest = gen_reset_code()
    expires = datetime.now(timezone.utc) + timedelta(minutes=30)
    await db.password_resets.delete_many({"userId": identity["id"]})
    await db.password_resets.insert_one({
        "userId": identity["id"], "codeHash": digest,
        "expiresAt": expires, "used": False,
    })
    inner = (
        f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(identity.get('name', ''))},<br>"
        "Ihr ORDO-Konto wurde eingerichtet. Mit diesem Sicherheitscode legen Sie Ihr Passwort fest:</p>"
        f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;color:#0B1B3D;margin:0 0 12px'>{code}</p>"
        "<p style='margin:0;color:#8A90A2;font-size:13px'>Der Code ist 30 Minuten gültig. "
        "Teilen Sie ihn nicht mit anderen Personen.</p>"
    )
    return await send_email(
        access=access,
        to=identity["email"],
        subject="Ihr Zugang zu ORDO",
        html=email_shell("ORDO-Zugang einrichten", "Willkommen bei ORDO.", inner),
        idempotency_key=f"identity:{identity['id']}:invite:{reason}:{digest[:20]}",
        template_key="auth.account_invitation",
        resource_type="identity",
        resource_id=identity["id"],
    )


@api_router.get("/users")
async def list_users(
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    memberships = await access.tenant_memberships.find({}).to_list(1000)
    user_ids = [membership["userId"] for membership in memberships]
    identities = {
        identity["id"]: identity
        for identity in await db.users.find({"id": {"$in": user_ids}}).to_list(1000)
    }
    companies = {
        company["id"]: company["name"]
        for company in await access.companies.find({}).to_list(1000)
    }
    sales_ids = [
        membership["userId"] for membership in memberships
        if membership.get("role") == "sales"
    ]
    assigned_companies = await access.companies.find({
        "assignedSalesRepId": {"$in": sales_ids}
    }).to_list(5000) if sales_ids else []
    assigned_counts: dict[str, int] = {}
    for company in assigned_companies:
        sales_id = company.get("assignedSalesRepId")
        if sales_id:
            assigned_counts[sales_id] = assigned_counts.get(sales_id, 0) + 1
    rows = []
    for membership in memberships:
        identity = identities.get(membership.get("userId"))
        if identity is None:
            continue
        company_id = membership.get("companyId")
        rows.append({
            "id": identity["id"],
            "membershipId": membership["id"],
            "name": identity.get("name"),
            "email": identity["email"],
            "role": membership["role"],
            "status": membership["status"],
            "identityActive": identity.get("active", True),
            "assignedCustomerCount": assigned_counts.get(identity["id"], 0),
            "companyId": company_id,
            "companyName": companies.get(company_id),
            "createdAt": membership.get("createdAt") or identity.get("createdAt"),
        })
    rows.sort(key=lambda row: row.get("createdAt") or "")
    return rows


@api_router.post("/users")
async def create_user(
    body: CreateUserIn,
    admin: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
    existing_identity = await db.users.find_one({"email": email})
    if existing_identity and existing_identity.get("active") is False:
        raise HTTPException(status_code=409, detail="Globale Identität ist deaktiviert")
    if existing_identity and await access.tenant_memberships.find_one({
        "userId": existing_identity["id"]
    }):
        raise HTTPException(status_code=409, detail="Benutzer ist diesem Tenant bereits zugeordnet")
    company_id = None
    created_company_id = None
    if body.role == "customer":
        if body.newCompany and body.newCompany.name.strip():
            company_id = "c" + secrets.token_hex(4)
            await access.companies.insert_one({
                "id": company_id,
                "name": body.newCompany.name.strip(),
                "city": body.newCompany.city.strip(),
                "email": body.newCompany.email.strip(),
                "phone": body.newCompany.phone.strip(),
                "vatId": "",
                "assignedSalesRepId": admin["id"],
                "active": True,
                "monthlyKg": 0,
                "orderCycleDays": 30,
            })
            created_company_id = company_id
        elif body.companyId:
            company = await access.companies.find_one({"id": body.companyId})
            if not company:
                raise HTTPException(status_code=400, detail="Firma nicht gefunden")
            company_id = body.companyId
        else:
            raise HTTPException(
                status_code=400,
                detail="Für ein Kundenkonto ist eine Firma erforderlich",
            )

    now = datetime.now(timezone.utc).isoformat()
    user_id = existing_identity["id"] if existing_identity else "u-" + secrets.token_hex(5)
    membership_id = "mbr-" + secrets.token_hex(8)
    identity = existing_identity or {
        "id": user_id,
        "name": body.name.strip() or email,
        "email": email,
        # Legacy fields remain non-authoritative until their later contraction.
        "role": body.role,
        "hashed_password": hash_pw(random_password(32)),
        "companyId": company_id,
        "salesRepId": user_id if body.role == "sales" else None,
        "active": True,
        "authVersion": 0,
        "must_change_password": True,
        "createdAt": now,
    }
    membership = {
        "id": membership_id,
        "userId": user_id,
        "role": body.role,
        "status": "active",
        "companyId": company_id,
        "createdAt": now,
        "updatedAt": now,
    }
    identity_created = False
    try:
        if existing_identity is None:
            await db.users.insert_one(identity)
            identity_created = True
        await access.tenant_memberships.insert_one(membership)
    except Exception:
        # User creation spans a global and tenant-scoped collection. Until the
        # later transaction package can make this atomic, compensate only the
        # documents created by this request so a failed membership write does
        # not leave an identity (or newly created company) without membership.
        if identity_created:
            await db.users.delete_one({"id": user_id})
        if created_company_id is not None:
            await access.companies.delete_one({"id": created_company_id})
        raise
    if existing_identity:
        await global_audit(admin, "identity.reuse", user_id, {"email": email})
    else:
        await global_audit(admin, "identity.create", user_id, {"email": email})
    await tenant_audit(
        access,
        admin,
        "membership.create",
        membership_id,
        {"userId": user_id, "role": body.role},
    )
    invitation = None
    if existing_identity is None:
        try:
            invitation = await _queue_account_invitation(
                access, identity, reason="created"
            )
        except Exception:
            invitation = None
    return {
        "id": user_id,
        "membershipId": membership_id,
        "name": identity["name"],
        "email": email,
        "role": body.role,
        "companyId": company_id,
        "accessMethod": (
            "existing_identity" if existing_identity
            else "invitation" if invitation
            else "invitation_failed"
        ),
        "invitationQueued": bool(invitation),
    }


@api_router.post("/users/{user_id}/reset")
async def admin_reset_password(
    user_id: str,
    admin: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    membership = await access.tenant_memberships.find_one({"userId": user_id})
    if not membership:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    identity = await db.users.find_one({"id": user_id})
    if not identity:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")

    all_memberships = await MongoMembershipDirectory(db).list_memberships_for_user(user_id)
    if (
        len(all_memberships) != 1
        or all_memberships[0].tenant_id != access.context.tenant_id
        or all_memberships[0].membership_id != membership["id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="Passwortreset für tenantübergreifende Identität nicht zulässig",
        )

    reset_limit = (
        RateLimitKey("actor", admin["id"], 30, 15 * 60),
    )
    limiter = MongoAuthRateLimiter(db)
    try:
        await limiter.ensure_allowed("admin_password_reset", reset_limit)
        await limiter.record("admin_password_reset", reset_limit)
    except AuthRateLimitExceeded as exc:
        raise HTTPException(
            status_code=429,
            detail=(
                "Zu viele Passwortresets. Bitte in "
                f"{retry_minutes(exc.retry_after_seconds)} Minuten erneut versuchen."
            ),
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    if not await rotate_credentials(
        db,
        identity,
        hashed_password=hash_pw(random_password(32)),
        must_change_password=True,
    ):
        raise HTTPException(status_code=409, detail="Benutzer wurde parallel geändert")
    await global_audit(admin, "identity.reset", user_id, {"email": identity["email"]})
    await tenant_audit(access, admin, "membership.identity_reset", membership["id"])
    try:
        invitation = await _queue_account_invitation(access, identity, reason="admin-reset")
    except Exception:
        invitation = None
    return {
        "id": user_id, "email": identity["email"],
        "invitationQueued": bool(invitation),
    }
