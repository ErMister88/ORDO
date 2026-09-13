"""Admin user management."""
import secrets
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, db, hash_pw, random_password, audit
from ..deps import require_roles
from ..models import CreateUserIn


@api_router.get("/users")
async def list_users(user: Annotated[dict, Depends(require_roles("admin"))]):
    users = await db.users.find({}).to_list(1000)
    comps = {c["id"]: c["name"] for c in await db.companies.find({}).to_list(1000)}
    users.sort(key=lambda u: u.get("createdAt", ""))
    return [
        {
            "id": u["id"], "name": u.get("name"), "email": u["email"], "role": u["role"],
            "companyId": u.get("companyId"), "companyName": comps.get(u.get("companyId")),
            "createdAt": u.get("createdAt"),
        }
        for u in users
    ]


@api_router.post("/users")
async def create_user(body: CreateUserIn, admin: Annotated[dict, Depends(require_roles("admin"))]):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-Mail-Adresse ist bereits vergeben")
    company_id = None
    if body.role == "customer":
        if body.newCompany and body.newCompany.name.strip():
            company_id = "c" + secrets.token_hex(4)
            await db.companies.insert_one({
                "id": company_id, "name": body.newCompany.name.strip(), "city": body.newCompany.city.strip(),
                "email": body.newCompany.email.strip(), "phone": body.newCompany.phone.strip(), "vatId": "",
                "assignedSalesRepId": admin["id"], "active": True, "monthlyKg": 0, "orderCycleDays": 30,
            })
        elif body.companyId:
            c = await db.companies.find_one({"id": body.companyId})
            if not c:
                raise HTTPException(status_code=400, detail="Firma nicht gefunden")
            company_id = body.companyId
        else:
            raise HTTPException(status_code=400, detail="Für ein Kundenkonto ist eine Firma erforderlich")
    uid = "u-" + secrets.token_hex(5)
    pw = random_password()
    doc = {
        "id": uid, "name": body.name.strip() or email, "email": email, "role": body.role,
        "hashed_password": hash_pw(pw), "companyId": company_id,
        "salesRepId": uid if body.role == "sales" else None,
        "must_change_password": True,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    await audit(admin, "user.create", uid, {"role": body.role, "email": email})
    return {"id": uid, "name": doc["name"], "email": email, "role": body.role,
            "companyId": company_id, "initialPassword": pw}


@api_router.post("/users/{user_id}/reset")
async def admin_reset_password(user_id: str, admin: Annotated[dict, Depends(require_roles("admin"))]):
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    pw = random_password()
    await db.users.update_one({"id": user_id}, {"$set": {"hashed_password": hash_pw(pw), "must_change_password": True}})
    await audit(admin, "user.reset", user_id, {"email": u["email"]})
    return {"id": user_id, "email": u["email"], "initialPassword": pw}
