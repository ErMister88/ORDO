"""Customer master-data helpers shared by binding commercial documents."""

from __future__ import annotations

from fastapi import HTTPException

from .tenant_access import TenantBusinessAccess


def company_snapshot(company: dict) -> dict:
    return {
        "companyId": company["id"],
        "name": company.get("name", ""),
        "email": company.get("email", ""),
        "phone": company.get("phone", ""),
        "vatId": company.get("vatId", ""),
        "taxNumber": company.get("taxNumber", ""),
        "city": company.get("city", ""),
    }


def address_snapshot(address: dict | None) -> dict | None:
    if not address:
        return None
    return {
        "addressId": address["id"],
        "type": address.get("type"),
        "label": address.get("label", ""),
        "street": address.get("street", ""),
        "houseNumber": address.get("houseNumber", ""),
        "zip": address.get("zip", ""),
        "city": address.get("city", ""),
        "country": address.get("country", ""),
    }


async def resolve_address_snapshot(
    access: TenantBusinessAccess,
    company_id: str,
    address_id: str | None,
    *,
    preferred_type: str,
) -> dict | None:
    if address_id:
        address = await access.customer_addresses.find_one({
            "id": address_id, "companyId": company_id, "active": {"$ne": False},
        })
        if not address:
            raise HTTPException(status_code=404, detail="Kundenadresse nicht gefunden")
        return address_snapshot(address)
    address = await access.customer_addresses.find_one({
        "companyId": company_id, "type": preferred_type, "active": {"$ne": False},
    })
    if not address and preferred_type != "main":
        address = await access.customer_addresses.find_one({
            "companyId": company_id, "type": "main", "active": {"$ne": False},
        })
    return address_snapshot(address)
