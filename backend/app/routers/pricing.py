"""Authoritative B2B quotes, customer prices, promotions and price history."""
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException

from ..audit_service import tenant_audit
from ..core import api_router, strip_id
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import B2BPromotionIn, CustomerPriceIn, PricingQuoteIn
from ..money import MoneyError, amount_minor, from_minor, require_minor, to_minor
from ..pricing_engine import PricingEngine, PricingError
from ..tenant_access import TenantBusinessAccess


def _pricing_http(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise HTTPException(status_code=400, detail="Start und Ende benötigen eine Zeitzone")
    return value.astimezone(timezone.utc).isoformat()


async def _can_manage_company(user: dict, access: TenantBusinessAccess, company_id: str) -> bool:
    if user.get("role") == "admin":
        return True
    return company_id in await visible_company_ids(user, access)


@api_router.post("/pricing/b2b/quote")
async def quote_b2b(
    body: PricingQuoteIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.companyId not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if not body.items:
        raise HTTPException(status_code=400, detail="Keine Artikel angegeben")
    engine = PricingEngine(access)
    try:
        lines = [(await engine.quote_b2b(body.companyId, item.productId, item.qty))[1].public()
                 for item in body.items]
        try:
            total_minor = require_minor(sum(line["lineTotalMinor"] for line in lines))
        except MoneyError as exc:
            raise PricingError("Preisvorschau überschreitet den unterstützten Wertebereich") from exc
    except (PricingError, MoneyError) as exc:
        raise _pricing_http(exc) from exc
    return {
        "pricingContext": "b2b", "currency": engine.currency, "priceSemantics": "net",
        "lines": lines, "totalMinor": total_minor, "total": from_minor(total_minor),
    }


@api_router.post("/customer-prices")
async def upsert_customer_price(
    body: CustomerPriceIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await _can_manage_company(user, access, body.companyId):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    company = await access.companies.find_one({"id": body.companyId})
    product = await access.products.find_one({"id": body.productId})
    if not company or not product:
        raise HTTPException(status_code=404, detail="Kunde oder Produkt nicht gefunden")
    existing_rows = await access.customer_prices.find(
        {"companyId": body.companyId, "productId": body.productId, "active": {"$ne": False}}
    ).to_list(2)
    if len(existing_rows) > 1:
        raise HTTPException(status_code=409, detail="Mehrere aktive Kundenpreise müssen vor der Änderung bereinigt werden")
    existing = existing_rows[0] if existing_rows else None
    currency = access.context.default_currency
    new_minor = to_minor(body.price)
    try:
        old_minor = amount_minor(existing, "price", expected_currency=currency) if existing else None
    except MoneyError as exc:
        raise HTTPException(status_code=409, detail="Bestehender Kundenpreis ist beschädigt") from exc
    if old_minor != new_minor:
        await access.price_history.insert_one({
            "companyId": body.companyId, "productId": body.productId,
            "oldPrice": from_minor(old_minor) if old_minor is not None else None,
            "oldPriceMinor": old_minor, "newPrice": from_minor(new_minor),
            "newPriceMinor": new_minor, "currency": currency,
            "changedBy": user["id"], "changedByName": user.get("name", ""),
            "changedAt": datetime.now(timezone.utc).isoformat(),
        })
    await access.customer_prices.update_one(
        {"companyId": body.companyId, "productId": body.productId},
        {"$set": {"price": from_minor(new_minor), "priceMinor": new_minor,
                  "currency": currency, "active": True}}, upsert=True,
    )
    await tenant_audit(access, user, "price.set", body.companyId,
                       {"productId": body.productId, "priceMinor": new_minor, "currency": currency})
    return {"ok": True, **body.model_dump(), "price": from_minor(new_minor),
            "priceMinor": new_minor, "currency": currency}


@api_router.get("/companies/{company_id}/price-history")
async def get_price_history(
    company_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await access.companies.find_one({"id": company_id}):
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    if company_id not in await visible_company_ids(user, access):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    rows = await access.price_history.find({"companyId": company_id}).sort("changedAt", -1).to_list(500)
    return [strip_id(r) for r in rows]


@api_router.delete("/customer-prices")
async def delete_customer_price(
    companyId: str, productId: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if not await _can_manage_company(user, access, companyId):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    result = await access.customer_prices.delete_one({"companyId": companyId, "productId": productId})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Kundenpreis nicht gefunden")
    return {"ok": True}


@api_router.get("/pricing/promotions")
async def list_promotions(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    query = {}
    if user.get("role") != "admin":
        query = {"companyId": {"$in": await visible_company_ids(user, access)}}
    return [strip_id(row) for row in await access.pricing_promotions.find(query).sort("startsAt", -1).to_list(1000)]


@api_router.post("/pricing/promotions")
async def create_promotion(
    body: B2BPromotionIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    starts, ends = _iso_utc(body.startsAt), _iso_utc(body.endsAt)
    if starts >= ends:
        raise HTTPException(status_code=400, detail="Ende muss nach dem Start liegen")
    if not await access.products.find_one({"id": body.productId, "active": {"$ne": False}}):
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    if body.companyId is None:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="Allgemeine Aktionen dürfen nur Admins anlegen")
    else:
        if not await access.companies.find_one({"id": body.companyId}):
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        if not await _can_manage_company(user, access, body.companyId):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
    overlapping = await access.pricing_promotions.find_one({
        "productId": body.productId, "companyId": body.companyId, "active": {"$ne": False},
        "startsAt": {"$lt": ends}, "endsAt": {"$gt": starts},
    })
    if overlapping:
        raise HTTPException(status_code=409, detail="Aktionszeiträume dürfen sich nicht überschneiden")
    currency = access.context.default_currency
    price_minor = to_minor(body.price)
    document = {
        "id": "promo-" + secrets.token_hex(6), "name": body.name.strip(),
        "productId": body.productId, "companyId": body.companyId,
        "price": from_minor(price_minor), "priceMinor": price_minor, "currency": currency,
        "startsAt": starts, "endsAt": ends, "active": True,
        "createdBy": user["id"], "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    await access.pricing_promotions.insert_one(document)
    await tenant_audit(access, user, "pricing.promotion.create", document["id"],
                       {"productId": body.productId, "companyId": body.companyId})
    return strip_id(document)


@api_router.delete("/pricing/promotions/{promotion_id}")
async def delete_promotion(
    promotion_id: str,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    promotion = await access.pricing_promotions.find_one({"id": promotion_id})
    if not promotion:
        raise HTTPException(status_code=404, detail="Aktionspreis nicht gefunden")
    company_id = promotion.get("companyId")
    if company_id is None and user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if company_id is not None and not await _can_manage_company(user, access, company_id):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await access.pricing_promotions.delete_one({"id": promotion_id})
    await tenant_audit(access, user, "pricing.promotion.delete", promotion_id, {})
    return {"ok": True}
