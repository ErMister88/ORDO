"""Auth + visibility dependencies."""
import jwt
from fastapi import Depends, HTTPException, status
from typing import Annotated, List

from .core import db, oauth2_scheme, JWT_SECRET, JWT_ALGORITHM, Role


async def current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige oder abgelaufene Anmeldung",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            raise err
    except Exception:
        raise err
    user = await db.users.find_one({"id": uid})
    if not user:
        raise err
    return user


def require_roles(*allowed: Role):
    async def dep(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user

    return dep


async def visible_company_ids(user: dict) -> List[str]:
    if user["role"] == "admin":
        companies = await db.companies.find({"active": True}).to_list(1000)
        return [c["id"] for c in companies]
    if user["role"] == "sales":
        companies = await db.companies.find(
            {"assignedSalesRepId": user["id"], "active": True}
        ).to_list(1000)
        return [c["id"] for c in companies]
    return [user["companyId"]] if user.get("companyId") else []
