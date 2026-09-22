"""Customer prices + price history."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..audit_service import tenant_audit
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..models import CustomerPriceIn
from ..money import amount_minor, from_minor, to_minor
from ..tenant_access import TenantBusinessAccess


@api_router.post("/customer-prices")
async def upsert_customer_price(
    body: CustomerPriceIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    company = await access.companies.find_one({"id": body.companyId})
    product = await access.products.find_one({"id": body.productId})
    if not company or not product:
        raise HTTPException(status_code=404, detail="Kunde oder Produkt nicht gefunden")
    existing = await access.customer_prices.find_one(
        {"companyId": body.companyId, "productId": body.productId}
    )
    currency = access.context.default_currency
    new_minor = to_minor(body.price)
    old_minor = amount_minor(existing, "price", expected_currency=currency) if existing else None
    old_price = from_minor(old_minor) if old_minor is not None else None
    if old_minor != new_minor:
        await access.price_history.insert_one({
            "companyId": body.companyId,
            "productId": body.productId,
            "oldPrice": old_price,
            "oldPriceMinor": old_minor,
            "newPrice": from_minor(new_minor),
            "newPriceMinor": new_minor,
            "currency": currency,
            "changedBy": user["id"],
            "changedByName": user.get("name", ""),
            "changedAt": datetime.now(timezone.utc).isoformat(),
        })
    await access.customer_prices.update_one(
        {"companyId": body.companyId, "productId": body.productId},
        {"$set": {"price": from_minor(new_minor), "priceMinor": new_minor, "currency": currency}},
        upsert=True,
    )
    await tenant_audit(
        access,
        user,
        "price.set",
        body.companyId,
        {"productId": body.productId, "price": from_minor(new_minor), "priceMinor": new_minor, "currency": currency},
    )
    return {"ok": True, **body.model_dump(), "price": from_minor(new_minor), "priceMinor": new_minor, "currency": currency}


@api_router.get("/companies/{company_id}/price-history")
async def get_price_history(
    company_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await access.companies.find_one({"id": company_id}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    ids = await visible_company_ids(user, access)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    rows = await access.price_history.find({"companyId": company_id}).sort("changedAt", -1).to_list(500)
    return [strip_id(r) for r in rows]


@api_router.delete("/customer-prices")
async def delete_customer_price(
    companyId: str,
    productId: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    result = await access.customer_prices.delete_one(
        {"companyId": companyId, "productId": productId}
    )
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kundenpreis nicht gefunden")
    return {"ok": True}
