"""Authoritative B2B quotes, customer prices, promotions and price history."""
import secrets
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException
from pymongo import ReturnDocument

from ..audit_service import tenant_audit
from ..core import api_router, strip_id
from ..customer_activity import record_customer_activity
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import B2BPromotionIn, CustomerPriceIn, PriceApprovalDecisionIn, PricingQuoteIn
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


def _conditions(body: CustomerPriceIn, *, include_internal: bool) -> dict:
    if body.validFrom is not None and body.validFrom.tzinfo is None:
        raise HTTPException(status_code=400, detail="Gültigkeitsbeginn benötigt eine Zeitzone")
    if body.validUntil is not None and body.validUntil.tzinfo is None:
        raise HTTPException(status_code=400, detail="Gültigkeitsende benötigt eine Zeitzone")
    if body.validFrom and body.validUntil and body.validFrom >= body.validUntil:
        raise HTTPException(status_code=400, detail="Gültigkeitsende muss nach dem Beginn liegen")
    payload = {
        "deliveryTerms": body.deliveryTerms.strip(),
        "transportModel": body.transportModel.strip(),
        "paymentTermDays": body.paymentTermDays,
        "minimumQuantity": body.minimumQuantity,
        "validFrom": body.validFrom.astimezone(timezone.utc).isoformat() if body.validFrom else None,
        "validUntil": body.validUntil.astimezone(timezone.utc).isoformat() if body.validUntil else None,
        "sourceReference": body.sourceReference.strip(),
    }
    if include_internal:
        payload["internalNote"] = body.internalNote.strip()
    return payload


async def _persist_customer_price(body: CustomerPriceIn, user: dict, access: TenantBusinessAccess) -> dict:
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
    conditions = _conditions(body, include_internal=user.get("role") == "admin")
    await access.customer_prices.update_one(
        {"companyId": body.companyId, "productId": body.productId},
        {"$set": {"price": from_minor(new_minor), "priceMinor": new_minor,
                  "currency": currency, "active": True, **conditions}}, upsert=True,
    )
    await tenant_audit(access, user, "price.set", body.companyId,
                       {"productId": body.productId, "priceMinor": new_minor, "currency": currency})
    await record_customer_activity(
        access, company_id=body.companyId, actor=user, activity_type="price_changed",
        title="Kundenpreis geändert", internal=True,
        reference={"type": "product", "id": body.productId},
    )
    public_conditions = {
        key: value for key, value in conditions.items()
        if key != "internalNote" and value not in (None, "")
    }
    result = {"ok": True, "companyId": body.companyId, "productId": body.productId,
              "price": from_minor(new_minor), "priceMinor": new_minor, "currency": currency,
              **public_conditions}
    if user.get("role") == "admin":
        if conditions.get("internalNote"):
            result["internalNote"] = conditions["internalNote"]
    return result


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
    if user.get("role") == "sales":
        try:
            floor_minor = amount_minor(product, "salesFloor", expected_currency=access.context.default_currency)
        except MoneyError:
            floor_minor = None
        if floor_minor is not None and to_minor(body.price) < floor_minor:
            now = datetime.now(timezone.utc).isoformat()
            approval = {
                "id": "price-approval-" + secrets.token_hex(8),
                "companyId": body.companyId, "productId": body.productId,
                "requestedPriceMinor": to_minor(body.price), "currency": access.context.default_currency,
                "conditions": _conditions(body, include_internal=False),
                "quantity": body.minimumQuantity,
                "status": "pending", "requestedBy": user["id"], "createdAt": now,
            }
            await access.price_approvals.insert_one(approval)
            await tenant_audit(access, user, "price.approval.request", approval["id"], {"companyId": body.companyId, "productId": body.productId})
            return {"ok": False, "approvalRequired": True,
                    "message": "Dieser Preis benötigt eine Freigabe durch einen Administrator."}
    return await _persist_customer_price(body, user, access)


@api_router.get("/pricing/approvals")
async def list_price_approvals(user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    rows = await access.price_approvals.find({"status": "pending"}).sort("createdAt", -1).to_list(1000)
    result = []
    for row in rows:
        company = await access.companies.find_one({"id": row.get("companyId")})
        product = await access.products.find_one({"id": row.get("productId")})
        if not company or not product:
            continue
        public = strip_id(row)
        public.update({
            "companyName": company.get("name", ""),
            "productName": f"{product.get('brand', '')} {product.get('name', '')}".strip(),
            "basePriceMinor": amount_minor(product, "standardPrice", expected_currency=access.context.default_currency),
            "approvalFloorMinor": amount_minor(product, "salesFloor", expected_currency=access.context.default_currency),
            "absoluteFloorMinor": amount_minor(product, "absoluteFloor", expected_currency=access.context.default_currency),
            "costMinor": product.get("costMinor"),
            "internalNote": (row.get("conditions") or {}).get("internalNote", ""),
        })
        result.append(public)
    return result


@api_router.get("/pricing/approvals/mine")
async def list_my_price_approvals(
    user: Annotated[dict, Depends(require_roles("sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    rows = await access.price_approvals.find({
        "requestedBy": user["id"], "status": "approved", "persistence": "one_time",
        "consumedAt": {"$exists": False},
    }).sort("decidedAt", -1).to_list(1000)
    return [{
        "id": row["id"], "companyId": row["companyId"], "productId": row["productId"],
        "requestedPriceMinor": row["requestedPriceMinor"], "currency": row["currency"],
        "quantity": row.get("quantity"), "conditions": row.get("conditions") or {},
        "decidedAt": row.get("decidedAt"),
    } for row in rows]


@api_router.post("/pricing/approvals/{approval_id}/approve")
async def approve_price(approval_id: str, user: Annotated[dict, Depends(require_roles("admin"))], access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)]):
    return await decide_price_approval(
        approval_id, PriceApprovalDecisionIn(persistence="customer_price"), user, access
    )


@api_router.post("/pricing/approvals/{approval_id}/decision")
async def decide_price_approval(
    approval_id: str,
    body: PriceApprovalDecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    decision_at = datetime.now(timezone.utc).isoformat()
    approval = await access.price_approvals.find_one_and_update(
        {"id": approval_id, "status": "pending"},
        {"$set": {"status": "processing", "processingBy": user["id"], "processingAt": decision_at}},
        return_document=ReturnDocument.AFTER,
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Preisfreigabe nicht gefunden")
    result = {
        "ok": True, "approvalId": approval_id, "persistence": body.persistence,
        "companyId": approval["companyId"], "productId": approval["productId"],
        "price": from_minor(approval["requestedPriceMinor"]),
        "priceMinor": approval["requestedPriceMinor"], "currency": approval["currency"],
    }
    try:
        product = await access.products.find_one({"id": approval["productId"]})
        if not product or approval["requestedPriceMinor"] < amount_minor(
            product, "absoluteFloor", expected_currency=access.context.default_currency
        ):
            raise HTTPException(status_code=409, detail="Preis liegt unter der absoluten internen Preisgrenze")
        if body.persistence == "customer_price":
            price_body = CustomerPriceIn(
                companyId=approval["companyId"], productId=approval["productId"],
                price=from_minor(approval["requestedPriceMinor"]), **(approval.get("conditions") or {}),
            )
            result = await _persist_customer_price(price_body, user, access)
            result["approvalId"] = approval_id
            result["persistence"] = body.persistence
        finalized = await access.price_approvals.update_one(
            {"id": approval_id, "status": "processing", "processingBy": user["id"]},
            {"$set": {"status": "approved", "decidedBy": user["id"], "decidedAt": decision_at,
                      "decisionNote": body.note.strip(), "persistence": body.persistence},
             "$unset": {"processingBy": "", "processingAt": ""}},
        )
        if finalized.matched_count == 0:
            raise HTTPException(status_code=409, detail="Preisfreigabe konnte nicht sicher abgeschlossen werden")
    except Exception:
        await access.price_approvals.update_one(
            {"id": approval_id, "status": "processing", "processingBy": user["id"]},
            {"$set": {"status": "pending"}, "$unset": {"processingBy": "", "processingAt": ""}},
        )
        raise
    await tenant_audit(access, user, "price.approval.approve", approval_id,
                       {"persistence": body.persistence, "companyId": approval["companyId"], "productId": approval["productId"]})
    return result


@api_router.post("/pricing/approvals/{approval_id}/reject")
async def reject_price_approval(
    approval_id: str, body: PriceApprovalDecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    result = await access.price_approvals.update_one(
        {"id": approval_id, "status": "pending"}, {"$set": {
            "status": "rejected", "decidedBy": user["id"],
            "decidedAt": datetime.now(timezone.utc).isoformat(), "decisionNote": body.note.strip(),
        }}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Preisfreigabe nicht gefunden")
    await tenant_audit(access, user, "price.approval.reject", approval_id, {})
    return {"ok": True, "status": "rejected"}


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
