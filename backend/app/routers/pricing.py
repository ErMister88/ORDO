"""Customer prices + price history."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, strip_id
from ..audit_service import tenant_audit
from ..deps import require_roles, tenant_business_access, visible_company_ids
from ..models import CustomerPriceIn
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
    old_price = existing["price"] if existing else None
    if old_price != body.price:
        await access.price_history.insert_one({
            "companyId": body.companyId,
            "productId": body.productId,
            "oldPrice": old_price,
            "newPrice": body.price,
            "changedBy": user["id"],
            "changedByName": user.get("name", ""),
            "changedAt": datetime.now(timezone.utc).isoformat(),
        })
    await access.customer_prices.update_one(
        {"companyId": body.companyId, "productId": body.productId},
        {"$set": {"price": body.price}},
        upsert=True,
    )
    await tenant_audit(
        access,
        user,
        "price.set",
        body.companyId,
        {"productId": body.productId, "price": body.price},
    )
    return {"ok": True, **body.model_dump()}


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
