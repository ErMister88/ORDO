"""Customer prices + price history."""
from fastapi import Depends, HTTPException
from typing import Annotated
from datetime import datetime, timezone

from ..core import api_router, db, strip_id, audit
from ..deps import current_user, require_roles, visible_company_ids
from ..models import CustomerPriceIn


@api_router.post("/customer-prices")
async def upsert_customer_price(body: CustomerPriceIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    existing = await db.customer_prices.find_one({"companyId": body.companyId, "productId": body.productId})
    old_price = existing["price"] if existing else None
    if old_price != body.price:
        await db.price_history.insert_one({
            "companyId": body.companyId,
            "productId": body.productId,
            "oldPrice": old_price,
            "newPrice": body.price,
            "changedBy": user["id"],
            "changedByName": user.get("name", ""),
            "changedAt": datetime.now(timezone.utc).isoformat(),
        })
    await db.customer_prices.update_one(
        {"companyId": body.companyId, "productId": body.productId},
        {"$set": {"price": body.price}},
        upsert=True,
    )
    await audit(user, "price.set", body.companyId, {"productId": body.productId, "price": body.price})
    return {"ok": True, **body.model_dump()}


@api_router.get("/companies/{company_id}/price-history")
async def get_price_history(company_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    rows = await db.price_history.find({"companyId": company_id}).sort("changedAt", -1).to_list(500)
    return [strip_id(r) for r in rows]


@api_router.delete("/customer-prices")
async def delete_customer_price(companyId: str, productId: str, user: Annotated[dict, Depends(require_roles("admin"))]):
    await db.customer_prices.delete_one({"companyId": companyId, "productId": productId})
    return {"ok": True}
