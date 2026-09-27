"""Offers."""
from html import escape
from fastapi import Depends, Header, HTTPException, Query
from typing import Annotated, Optional
from datetime import datetime, timezone
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..core import api_router, strip_id, next_seq, logger
from ..audit_service import tenant_audit
from ..customer_master import company_snapshot, resolve_address_snapshot
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import (
    AcceptOfferIn, CompanyCreateIn, CustomerAddressIn, CustomerContactIn,
    DecisionIn, OfferCreate, OfferCustomerCreateIn,
)
from ..emailer import send_email, email_shell, company_recipient, items_html
from ..money import amount_minor, from_minor, line_total_minor, to_minor
from ..observability import report_operational_failure
from ..pricing_engine import PricingEngine, PricingError
from ..snapshots import clone_snapshot_items, items_total_minor, product_item_snapshot, redact_internal_snapshot_fields
from ..tenant_access import TenantBusinessAccess
from ..idempotency import IdempotencyService
from ..pagination import bounded_list
from .companies import create_company_record


def _offer_response(offer: dict, user: dict) -> dict:
    payload = offer if user.get("role") == "admin" else redact_internal_snapshot_fields(offer)
    return strip_id(payload)


async def offer_references_visible(access: TenantBusinessAccess, offer: dict) -> bool:
    company_id = offer.get("companyId")
    if company_id is None:
        recipient = offer.get("recipientSnapshot")
        if not isinstance(recipient, dict) or not str(recipient.get("name", "")).strip():
            return False
    elif not isinstance(company_id, str) or not await access.companies.find_one({"id": company_id}):
        return False
    for item in offer.get("items", []):
        if item.get("snapshotVersion") == 1 and item.get("currency") == offer.get("currency"):
            continue
        product_id = item.get("productId")
        if not isinstance(product_id, str) or not await access.products.find_one({"id": product_id}):
            return False
    return True


@api_router.get("/offers")
async def get_offers(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
):
    ids = await visible_company_ids(user, access)
    query: dict = {"companyId": {"$in": ids}}
    if user.get("role") == "admin":
        query = {"$or": [query, {"companyId": None, "offerKind": "prospect"}]}
    elif user.get("role") == "sales":
        query = {"$or": [
            query,
            {"companyId": None, "offerKind": "prospect", "createdBy": user["id"]},
        ]}
    offers = await bounded_list(
        access.offers.find(query).sort([("createdAt", -1), ("id", -1)]),
        limit=limit, offset=offset,
    )
    return [_offer_response(o, user) for o in offers]


async def create_offer(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    if bool(body.companyId) == bool(body.prospectRecipient):
        raise HTTPException(
            status_code=400,
            detail="Genau ein bestehender Kunde oder ein Angebotsempfänger ist erforderlich",
        )
    if operation_id:
        existing = await access.offers.find_one({"operationId": operation_id})
        if existing:
            return _offer_response(existing, user)
    company = None
    if body.companyId:
        ids = await visible_company_ids(user, access)
        if body.companyId not in ids:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        company = await access.companies.find_one({"id": body.companyId})
        if not company:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        recipient_snapshot = company_snapshot(company)
    else:
        prospect = body.prospectRecipient
        if prospect is None:
            raise HTTPException(status_code=400, detail="Angebotsempfänger fehlt")
        if not prospect.name.strip():
            raise HTTPException(status_code=400, detail="Angebotsempfänger fehlt")
        email = prospect.email.strip().lower()
        if email and "@" not in email:
            raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
        recipient_snapshot = {
            key: (value.strip() if isinstance(value, str) else value)
            for key, value in prospect.model_dump().items()
        }
        recipient_snapshot["email"] = email
    needs_approval = False
    currency = access.context.default_currency
    snapshots = []
    approvals_to_consume = []
    for it in body.items:
        approved_once = False
        try:
            engine = PricingEngine(access)
            if company is not None:
                prod, base_quote = await engine.quote_b2b(
                    body.companyId, it.productId, it.qty
                )
            else:
                prod, base_quote = await engine.quote_b2b_prospect(
                    it.productId, it.qty
                )
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        offer_minor = to_minor(it.price)
        if it.approvalId:
            if company is None:
                raise HTTPException(
                    status_code=409,
                    detail="Einmalige Kundenpreisfreigaben gelten nicht für Interessenten",
                )
            approval_filter = {
                "id": it.approvalId, "status": "approved", "persistence": "one_time",
                "companyId": body.companyId, "productId": it.productId,
                "currency": currency,
                "consumedAt": {"$exists": False},
            }
            if user.get("role") == "sales":
                approval_filter["requestedBy"] = user["id"]
            approval = await access.price_approvals.find_one(approval_filter)
            if not approval:
                raise HTTPException(status_code=409, detail="Preisfreigabe ist nicht verfügbar")
            approved_quantity = approval.get("quantity")
            if approved_quantity is not None and it.qty < approved_quantity:
                raise HTTPException(status_code=409, detail="Preisfreigabe ist für diese Menge nicht verfügbar")
            offer_minor = approval["requestedPriceMinor"]
            approvals_to_consume.append(approval["id"])
            approved_once = True
        if offer_minor < amount_minor(prod, "absoluteFloor", expected_currency=currency):
            detail = (
                "Preis unter absoluter Grenze – nicht zulässig"
                if user.get("role") == "admin"
                else "Dieser Preis benötigt eine Freigabe durch einen Administrator."
            )
            raise HTTPException(status_code=409, detail=detail)
        if not approved_once and offer_minor < amount_minor(prod, "salesFloor", expected_currency=currency):
            needs_approval = True
        snapshot = product_item_snapshot(
            prod,
            quantity=it.qty,
            unit_price_minor=offer_minor,
            currency=currency,
            price_source="offer_manual",
        )
        snapshot.update({
            "pricingContext": "b2b", "priceSemantics": "net",
            "baseUnitPriceMinor": base_quote.final_unit_price_minor,
            "basePriceSource": base_quote.price_source,
            "taxRate": base_quote.tax_rate,
        })
        snapshots.append(snapshot)
    now = datetime.now(timezone.utc)
    seq = await next_seq("offer")
    offer_no = f"A-{now.year}-{seq:04d}"
    offer = {
        "id": offer_no,
        "companyId": body.companyId,
        "offerKind": "customer" if company is not None else "prospect",
        "createdBy": user["id"],
        "status": "Freigabe nötig" if needs_approval else "Freigegeben",
        "items": snapshots,
        "currency": currency,
        "netTotalMinor": items_total_minor(snapshots, currency=currency),
        "snapshotVersion": 1,
        "salesAttribution": {
            "actorUserId": user["id"], "actorName": user.get("name", ""),
            "actorRole": user.get("role"), "membershipId": access.context.membership_id,
            "salesRepId": company.get("assignedSalesRepId") if company else (
                user["id"] if user.get("role") == "sales" else None
            ),
            "salesRepName": company.get("assignedSalesRepName", "") if company else (
                user.get("name", "") if user.get("role") == "sales" else ""
            ),
        },
        "companySnapshot": company_snapshot(company) if company else None,
        "recipientSnapshot": recipient_snapshot,
        "billingAddressSnapshot": (
            await resolve_address_snapshot(
                access, body.companyId, body.billingAddressId, preferred_type="billing"
            ) if company else _prospect_address_snapshot(recipient_snapshot, "billing")
        ),
        "deliveryAddressSnapshot": (
            await resolve_address_snapshot(
                access, body.companyId, body.deliveryAddressId, preferred_type="shipping"
            ) if company else _prospect_address_snapshot(recipient_snapshot, "shipping")
        ),
        "reason": body.reason or ("Preis unter Vertriebslimit" if needs_approval else ""),
        "termMonths": body.termMonths,
        "createdAt": now.isoformat(),
    }
    if operation_id:
        offer["operationId"] = operation_id
    claimed_approvals = []
    for approval_id in approvals_to_consume:
        claimed = await access.price_approvals.find_one_and_update(
            {"id": approval_id, "status": "approved", "consumedAt": {"$exists": False}},
            {"$set": {"consumedAt": now.isoformat(), "consumedByOfferId": offer_no}},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            for claimed_id in claimed_approvals:
                await access.price_approvals.update_one(
                    {"id": claimed_id, "consumedByOfferId": offer_no},
                    {"$unset": {"consumedAt": "", "consumedByOfferId": ""}},
                )
            raise HTTPException(status_code=409, detail="Preisfreigabe wurde bereits verwendet")
        claimed_approvals.append(approval_id)
    try:
        await access.offers.insert_one(offer)
    except Exception:
        for approval_id in claimed_approvals:
            await access.price_approvals.update_one(
                {"id": approval_id, "consumedByOfferId": offer_no},
                {"$unset": {"consumedAt": "", "consumedByOfferId": ""}},
            )
        raise
    return _offer_response(offer, user)


def _prospect_address_snapshot(recipient: dict, address_type: str) -> dict | None:
    if not any(recipient.get(key) for key in ("street", "zip", "city")):
        return None
    return {
        "addressId": None,
        "type": address_type,
        "label": recipient.get("name", ""),
        "street": recipient.get("street", ""),
        "houseNumber": recipient.get("houseNumber", ""),
        "zip": recipient.get("zip", ""),
        "city": recipient.get("city", ""),
        "country": recipient.get("country", "DE"),
    }


@api_router.post("/offers")
async def create_offer_endpoint(
    body: OfferCreate,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="offer.create",
        key=idempotency_key or "", payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_offer(
            body, user, access, operation_id=claim.record_id
        )
        await service.complete(claim, response, {"offerId": response["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="offer_create_failed", exception=exc)
        raise


@api_router.post("/offers/{offer_id}/approve")
async def approve_offer(
    offer_id: str,
    body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Freigegeben", "decisionNote": body.note}})
    try:
        if o.get("companyId"):
            email, cname = await company_recipient(access, o["companyId"])
        else:
            recipient = o.get("recipientSnapshot") or {}
            email, cname = recipient.get("email"), recipient.get("name", "")
        if email:
            rows = await items_html(access, o["items"])
            total = from_minor(o.get(
                "netTotalMinor",
                sum(line_total_minor(to_minor(it["price"]), it["qty"]) for it in o["items"]),
            ))
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(cname)},<br>"
                f"Ihr Angebot <strong>{escape(o['id'])}</strong> wurde freigegeben. Sie k&ouml;nnen es jetzt "
                f"direkt in der App in eine Bestellung umwandeln.</p>"
                "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                f"{rows}"
                f"<tr><td style='padding:8px 8px 0;font-weight:bold' colspan='2'>Gesamt netto</td>"
                f"<td style='padding:8px 8px 0;text-align:right;font-weight:bold'>{total:.2f} &euro;</td></tr>"
                "</table>"
            )
            html = email_shell("Angebot freigegeben", "Gute Neuigkeiten!", inner)
            await send_email(
                access=access, to=email, subject=f"Angebot {o['id']} freigegeben", html=html,
                idempotency_key=f"offer:{o['id']}:approved",
                template_key="offer.approved", resource_type="offer", resource_id=o["id"],
            )
    except Exception:
        await report_operational_failure(
            access, logger, operation="offer.approval_email", category="email_delivery",
        )
    await tenant_audit(access, user, "offer.approve", offer_id, {})
    return {"ok": True, "status": "Freigegeben"}


async def accept_offer(
    offer_id: str,
    body: AcceptOfferIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    if not o.get("companyId"):
        raise HTTPException(
            status_code=409,
            detail="Interessentenangebot muss zuerst einem Kunden zugeordnet werden",
        )
    ids = await visible_company_ids(user, access)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    existing_order = await access.orders.find_one({"fromOffer": offer_id})
    if existing_order:
        await access.offers.update_one(
            {"id": offer_id},
            {"$set": {"status": "Angenommen", "orderId": existing_order["id"]}},
        )
        return strip_id(redact_internal_snapshot_fields(existing_order))
    if o["status"] != "Freigegeben":
        raise HTTPException(status_code=400, detail="Nur freigegebene Angebote können angenommen werden.")
    if o.get("orderId"):
        raise HTTPException(status_code=400, detail="Angebot wurde bereits in eine Bestellung umgewandelt.")
    now = datetime.now(timezone.utc)
    currency = o.get("currency")
    if not isinstance(currency, str):
        raise HTTPException(status_code=409, detail="Angebot besitzt keinen verlässlichen historischen Snapshot")
    try:
        items = clone_snapshot_items(o["items"], currency=currency)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Angebot besitzt keinen verlässlichen historischen Snapshot") from exc
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": o["companyId"],
        "createdBy": user["id"],
        "status": "Neu",
        "items": items,
        "currency": currency,
        "netTotalMinor": items_total_minor(items, currency=currency),
        "snapshotVersion": 1,
        "salesAttribution": o.get("salesAttribution") or {"actorUserId": o.get("createdBy")},
        "companySnapshot": o.get("companySnapshot"),
        "billingAddressSnapshot": o.get("billingAddressSnapshot"),
        "deliveryAddressSnapshot": o.get("deliveryAddressSnapshot"),
        "fromOffer": offer_id,
        "customerNote": (body.note or "").strip(),
        "createdAt": now.isoformat(),
    }
    if operation_id:
        order["operationId"] = operation_id
    order_created = True
    try:
        await access.orders.insert_one(order)
    except DuplicateKeyError:
        existing_order = await access.orders.find_one({"fromOffer": offer_id})
        if not existing_order:
            raise
        order = existing_order
        order_no = order["id"]
        order_created = False
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Angenommen", "orderId": order_no}})
    if order_created:
        await tenant_audit(access, user, "offer.accept", offer_id, {"orderId": order_no})
    return strip_id(redact_internal_snapshot_fields(order))


@api_router.post("/offers/{offer_id}/customer")
async def convert_offer_recipient_to_customer(
    offer_id: str,
    body: OfferCustomerCreateIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    offer = await access.offers.find_one({"id": offer_id})
    if not offer or not await offer_references_visible(access, offer):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    if offer.get("companyId"):
        company = await access.companies.find_one({"id": offer["companyId"]})
        if not company:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        return strip_id(company)
    if user.get("role") == "sales" and offer.get("createdBy") != user["id"]:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")

    if body.existingCompanyId:
        visible_ids = await visible_company_ids(user, access)
        if body.existingCompanyId not in visible_ids:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        existing_company = await access.companies.find_one({"id": body.existingCompanyId})
        if not existing_company:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        updated = await access.offers.update_one(
            {"id": offer_id, "companyId": None},
            {"$set": {
                "companyId": existing_company["id"],
                "companySnapshot": company_snapshot(existing_company),
                "convertedToCustomerAt": datetime.now(timezone.utc).isoformat(),
                "convertedToCustomerBy": user["id"],
            }},
        )
        if updated.matched_count == 0:
            current = await access.offers.find_one({"id": offer_id})
            if not current or current.get("companyId") != existing_company["id"]:
                raise HTTPException(status_code=409, detail="Angebot wurde parallel verändert")
        await tenant_audit(
            access, user, "offer.customer.link", offer_id,
            {"companyId": existing_company["id"]},
        )
        return strip_id(existing_company)

    recipient = offer.get("recipientSnapshot") or {}
    address = body.primaryAddress
    if address is None and all(
        str(recipient.get(key, "")).strip() for key in ("street", "zip", "city")
    ):
        address = CustomerAddressIn(
            type="main", label="Hauptadresse", street=recipient["street"],
            houseNumber=recipient.get("houseNumber", ""), zip=recipient["zip"],
            city=recipient["city"], country=recipient.get("country") or "DE",
        )
    contact = body.primaryContact
    contact_name = str(recipient.get("contactName", "")).strip()
    recipient_email = str(recipient.get("email", "")).strip()
    if contact is None and contact_name and recipient_email:
        parts = contact_name.split(None, 1)
        contact = CustomerContactIn(
            firstName=parts[0], lastName=parts[1] if len(parts) > 1 else "–",
            email=recipient_email, phone=recipient.get("phone", ""),
        )
    merged = CompanyCreateIn(
        **{
            **body.model_dump(exclude={"primaryAddress", "primaryContact", "existingCompanyId"}),
            "name": body.name.strip() or recipient.get("name", ""),
            "email": body.email.strip() or recipient_email,
            "phone": body.phone.strip() or recipient.get("phone", ""),
            "city": body.city.strip() or recipient.get("city", ""),
            "vatId": body.vatId.strip() or recipient.get("vatId", ""),
            "primaryAddress": address,
            "primaryContact": contact,
        }
    )
    existing = await access.companies.find_one({"sourceOfferId": offer_id})
    if existing:
        company = strip_id(existing)
    else:
        try:
            company = await create_company_record(
                merged, user, access, source_offer_id=offer_id
            )
        except HTTPException as exc:
            # A concurrent conversion may create the customer after the
            # initial idempotency check but before duplicate detection.
            existing = await access.companies.find_one({"sourceOfferId": offer_id})
            if not existing or exc.status_code != 409:
                raise
            company = strip_id(existing)
        except DuplicateKeyError:
            existing = await access.companies.find_one({"sourceOfferId": offer_id})
            if not existing:
                raise
            company = strip_id(existing)
    updated = await access.offers.update_one(
        {"id": offer_id, "companyId": None},
        {"$set": {
            "companyId": company["id"],
            "companySnapshot": company_snapshot(company),
            "convertedToCustomerAt": datetime.now(timezone.utc).isoformat(),
            "convertedToCustomerBy": user["id"],
        }},
    )
    if updated.matched_count == 0:
        current = await access.offers.find_one({"id": offer_id})
        if not current or current.get("companyId") != company["id"]:
            raise HTTPException(status_code=409, detail="Angebot wurde parallel verändert")
    await tenant_audit(
        access, user, "offer.customer.create", offer_id, {"companyId": company["id"]}
    )
    return company


@api_router.post("/offers/{offer_id}/accept")
async def accept_offer_endpoint(
    offer_id: str,
    body: AcceptOfferIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access,
        actor_id=user["id"],
        operation="offer.accept",
        key=idempotency_key or "",
        payload={"offerId": offer_id, **body.model_dump(mode="json")},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await accept_offer(
            offer_id, body, user, access, operation_id=claim.record_id
        )
        await service.complete(
            claim, response, {"offerId": offer_id, "orderId": response["id"]}
        )
        return response
    except Exception as exc:
        await service.fail(claim, error_code="offer_accept_failed", exception=exc)
        raise


@api_router.post("/offers/{offer_id}/reject")
async def reject_offer(
    offer_id: str,
    body: DecisionIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    o = await access.offers.find_one({"id": offer_id})
    if not o or not await offer_references_visible(access, o):
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await access.offers.update_one({"id": offer_id}, {"$set": {"status": "Abgelehnt", "decisionNote": body.note}})
    return {"ok": True, "status": "Abgelehnt"}
