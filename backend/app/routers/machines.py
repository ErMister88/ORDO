"""B2B machines: admin catalog + acquisition (Kauf / Finanzierung / Leasing)."""
import uuid
import hashlib
import json
import stripe
from datetime import datetime, timezone
from html import escape
from typing import Annotated, Optional

from fastapi import Depends, Header, HTTPException
from pymongo.errors import DuplicateKeyError

from ..core import api_router, db, strip_id, next_seq, logger
from ..audit_service import tenant_audit
from ..customer_master import company_snapshot, resolve_address_snapshot
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import MachineIn, MachineRequestIn, MachineTermsIn, MachineRespondIn
from ..emailer import send_email, email_shell
from ..tenant_access import TenantBusinessAccess
from ..money import MoneyError, amount_minor, currency_code, from_minor, to_minor
from ..observability import report_operational_failure
from ..pricing_engine import PricingEngine, PricingError
from ..idempotency import IdempotencyService
from ..payment_integrity import checkout_return_urls, create_stripe_checkout, PaymentIntegrityError

TYPE_LABEL = {"kauf": "Kauf", "finanzierung": "Finanzierung", "leasing": "Leasing (Kaffeebindung)",
              "bereitstellung": "Bereitstellung"}

# ---------------- Catalog ----------------
@api_router.get("/machines")
async def list_machines(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    q = {} if user["role"] in ("admin", "sales") else {"active": True}
    rows = await access.machines.find(q).sort("price", 1).to_list(500)
    return [strip_id(r) for r in rows]


@api_router.post("/machines", status_code=201)
async def create_machine(
    body: MachineIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.productId and not await access.products.find_one({"id": body.productId}):
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    currency = access.context.default_currency
    doc = {"id": str(uuid.uuid4()),
           "currency": currency, "priceMinor": to_minor(body.price),
           "createdAt": datetime.now(timezone.utc).isoformat(), **body.model_dump()}
    await access.machines.insert_one(doc)
    await tenant_audit(access, user, "machine_create", doc["id"], {"name": body.name})
    return strip_id(doc)


@api_router.put("/machines/{machine_id}")
async def update_machine(
    machine_id: str,
    body: MachineIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.productId and not await access.products.find_one({"id": body.productId}):
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    payload = {**body.model_dump(), "currency": access.context.default_currency,
               "priceMinor": to_minor(body.price)}
    r = await access.machines.update_one({"id": machine_id}, {"$set": payload})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden")
    await tenant_audit(access, user, "machine_update", machine_id, {"name": body.name})
    return strip_id(await access.machines.find_one({"id": machine_id}))


@api_router.delete("/machines/{machine_id}")
async def delete_machine(
    machine_id: str,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    result = await access.machines.update_one({"id": machine_id}, {"$set": {"active": False}})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden")
    await tenant_audit(access, user, "machine_delete", machine_id)
    return {"ok": True}


# ---------------- Requests / acquisition ----------------
async def _customer_snapshot(user: dict, access: TenantBusinessAccess) -> dict:
    company = None
    company_id = user.get("companyId")
    if company_id is not None:
        if not isinstance(company_id, str) or not company_id:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
        company = await access.companies.find_one({"id": company_id})
        if not company:
            raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    return {"userId": user["id"], "companyId": company_id,
            "userName": user.get("name", ""), "email": user.get("email", ""),
            "companyName": company.get("name") if company else ""}


async def _machine_request_references_visible(
    request: dict,
    access: TenantBusinessAccess,
) -> bool:
    machine_id = request.get("machineId")
    machine = (
        await access.machines.find_one({"id": machine_id})
        if isinstance(machine_id, str)
        else None
    )
    if not machine:
        return False
    catalog_product_id = machine.get("productId")
    if catalog_product_id is not None and (
        not isinstance(catalog_product_id, str)
        or not await access.products.find_one({"id": catalog_product_id})
    ):
        return False
    customer = request.get("customer") or {}
    company_id = customer.get("companyId")
    if company_id is not None and (
        not isinstance(company_id, str)
        or not await access.companies.find_one({"id": company_id})
    ):
        return False
    product_id = (request.get("terms") or {}).get("productId")
    if product_id is not None and (
        not isinstance(product_id, str)
        or not await access.products.find_one({"id": product_id})
    ):
        return False
    contract_id = request.get("contractId")
    if contract_id is not None and (
        not isinstance(contract_id, str)
        or not await access.contracts.find_one({"id": contract_id})
    ):
        return False
    return True


async def create_machine_request(
    body: MachineRequestIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    if operation_id:
        existing = await access.machine_requests.find_one({"operationId": operation_id})
        if existing:
            return strip_id(existing)
    if body.type not in TYPE_LABEL:
        raise HTTPException(status_code=400, detail="Ungültiger Erwerbstyp")
    m = await access.machines.find_one({"id": body.machineId})
    if not m or not m.get("active", True):
        raise HTTPException(status_code=404, detail="Maschine nicht verfügbar")
    direct_quote = None
    if body.type == "kauf":
        try:
            m, direct_quote = await PricingEngine(access).quote_b2c_machine(body.machineId)
        except PricingError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    if body.productId and not await access.products.find_one({"id": body.productId, "active": {"$ne": False}}):
        raise HTTPException(status_code=404, detail="Kaffeesorte nicht gefunden")
    now = datetime.now(timezone.utc)
    seq = await next_seq("machinereq")
    rid = f"M-{now.year}-{seq:05d}"
    status = "Zahlung offen" if body.type == "kauf" else "Angefragt"
    doc = {
        "id": rid, "machineId": m["id"], "machineName": m["name"],
        "catalogProductId": m.get("productId"),
        "machineDescription": m.get("description", ""),
        "snapshotVersion": 1,
        "type": body.type, "termMonths": body.termMonths, "message": body.message,
        "requestedCoffeeProductId": body.productId,
        "expectedCoffeeKgMonth": body.expectedCoffeeKgMonth,
        "requestContact": {"companyName": body.companyName, "name": body.contactName,
                           "email": body.contactEmail, "phone": body.contactPhone},
        "customer": await _customer_snapshot(user, access),
        "status": status, "paymentStatus": "Offen",
        "terms": None, "createdAt": now.isoformat(),
    }
    if operation_id:
        doc["operationId"] = operation_id
    if direct_quote is not None:
        doc.update({
            "machinePrice": from_minor(direct_quote.final_unit_price_minor),
            "machinePriceMinor": direct_quote.final_unit_price_minor,
            "currency": direct_quote.currency, "taxRate": direct_quote.tax_rate,
            "pricingContext": direct_quote.pricing_context,
            "priceSemantics": direct_quote.price_semantics,
            "priceSource": direct_quote.price_source,
        })
    await access.machine_requests.insert_one(doc)

    # Notify staff about new requests (best-effort).
    if body.type != "kauf":
        try:
            inner = (
                f"<p style='margin:0 0 8px;color:#3A4256'>Neue Maschinen-Anfrage <strong>{escape(rid)}</strong></p>"
                f"<p style='margin:0;color:#3A4256'>Typ: {TYPE_LABEL[body.type]}<br/>"
                f"Maschine: {escape(m['name'])}<br/>Kunde: {escape(doc['customer'].get('companyName') or doc['customer']['userName'])}</p>"
            )
            await send_email(
                access=access, to=doc["customer"]["email"], subject=f"Ihre Maschinen-Anfrage {rid}",
                html=email_shell("Anfrage eingegangen", "Wir melden uns mit einem Angebot.", inner),
                idempotency_key=f"machine-request:{rid}:created", template_key="machine.request.created",
                resource_type="machine_request", resource_id=rid,
            )
        except Exception:
            await report_operational_failure(
                access, logger, operation="machine.request_email", category="email_delivery",
            )
    return strip_id(doc)


@api_router.post("/machine-requests", status_code=201)
async def create_machine_request_endpoint(
    body: MachineRequestIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="machine_request.create",
        key=idempotency_key or "", payload=body.model_dump(mode="json"),
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await create_machine_request(
            body, user, access, operation_id=claim.record_id
        )
        await service.complete(claim, response, {"machineRequestId": response["id"]})
        return response
    except Exception as exc:
        await service.fail(claim, error_code="machine_request_failed", exception=exc)
        raise


@api_router.get("/machine-requests")
async def list_machine_requests(
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if user["role"] == "admin":
        rows = await access.machine_requests.find({}).sort("createdAt", -1).to_list(1000)
    else:
        ids = await visible_company_ids(user, access)
        rows = await access.machine_requests.find(
            {"$or": [{"customer.companyId": {"$in": ids}}, {"customer.userId": user["id"]}]}
        ).sort("createdAt", -1).to_list(1000)
    visible = [r for r in rows if await _machine_request_references_visible(r, access)]
    return [strip_id(r) for r in visible]


@api_router.put("/machine-requests/{req_id}")
async def set_machine_terms(
    req_id: str,
    body: MachineTermsIn,
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if user["role"] != "admin":
        ids = await visible_company_ids(user, access)
        if r["customer"].get("companyId") not in ids:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
    status = body.status or "Angebot"
    is_lease = r.get("type") == "leasing"
    # Leasing offers require a complete coffee binding before they can be sent.
    if is_lease and status == "Angebot":
        if not body.minCoffeeKgMonth or body.minCoffeeKgMonth <= 0:
            raise HTTPException(status_code=400, detail="Bitte eine Kaffee-Mindestabnahme größer 0 angeben.")
        if not body.productId:
            raise HTTPException(status_code=400, detail="Bitte eine Kaffeesorte für die Kaffeebindung wählen.")
        if not body.coffeePricePerKg or body.coffeePricePerKg <= 0:
            raise HTTPException(status_code=400, detail="Bitte einen Kaffeepreis pro kg angeben.")

    coffee_name = ""
    if body.productId:
        p = await access.products.find_one({"id": body.productId})
        if not p:
            raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
        coffee_name = f"{p.get('brand', '')} {p.get('name', '')}".strip()
        # Keep internal floor details server-side when sales enters a low price.
        floor = p.get("absoluteFloor")
        if body.coffeePricePerKg is not None and floor is not None and to_minor(body.coffeePricePerKg) < amount_minor(p, "absoluteFloor", expected_currency=access.context.default_currency):
            raise HTTPException(
                status_code=409,
                detail="Dieser Preis benötigt eine Freigabe durch einen Administrator.",
            )

    terms = {
        "downPayment": body.downPayment, "monthlyRate": body.monthlyRate,
        "finalPayment": body.finalPayment, "termMonths": body.termMonths or r.get("termMonths"),
        "minCoffeeKgMonth": body.minCoffeeKgMonth,
        "productId": body.productId, "coffeePricePerKg": body.coffeePricePerKg, "coffeeName": coffee_name,
        "note": body.note,
        "currency": access.context.default_currency,
        "downPaymentMinor": to_minor(body.downPayment) if body.downPayment is not None else None,
        "monthlyRateMinor": to_minor(body.monthlyRate) if body.monthlyRate is not None else None,
        "finalPaymentMinor": to_minor(body.finalPayment) if body.finalPayment is not None else None,
        "coffeePricePerKgMinor": to_minor(body.coffeePricePerKg) if body.coffeePricePerKg is not None else None,
    }
    await access.machine_requests.update_one({"id": req_id}, {"$set": {"terms": terms, "status": status}})
    await tenant_audit(access, user, "machine_terms", req_id, {"status": status})

    email = r["customer"]["email"]
    if email and status == "Angebot":
        lines = []
        if terms["downPayment"] is not None:
            lines.append(f"Anzahlung: {terms['downPayment']:.2f} &euro;")
        if terms["monthlyRate"] is not None:
            lines.append(f"Monatliche Rate: {terms['monthlyRate']:.2f} &euro; ({terms['termMonths']} Monate)")
        if terms["finalPayment"] is not None:
            lines.append(f"Schlussrate (Übernahme): {terms['finalPayment']:.2f} &euro;")
        if terms["minCoffeeKgMonth"] is not None:
            lines.append(f"Mindestabnahme Kaffee: {terms['minCoffeeKgMonth']:.0f} kg / Monat")
        if coffee_name:
            price_txt = f" à {terms['coffeePricePerKg']:.2f} &euro;/kg" if terms.get("coffeePricePerKg") else ""
            lines.append(f"Kaffeesorte: {escape(coffee_name)}{price_txt}")
        body_html = "".join(f"<li>{x}</li>" for x in lines)
        inner = (
            f"<p style='margin:0 0 10px;color:#3A4256'>Ihr Angebot für <strong>{escape(r['machineName'])}</strong> "
            f"({TYPE_LABEL.get(r['type'], r['type'])}):</p>"
            f"<ul style='margin:0 0 10px;color:#3A4256;padding-left:18px'>{body_html}</ul>"
            + (f"<p style='margin:0;color:#8A90A2;font-size:13px'>{escape(terms['note'])}</p>" if terms.get("note") else "")
        )
        try:
            await send_email(
                access=access, to=email, subject=f"Ihr Angebot {req_id} – {r['machineName']}",
                html=email_shell("Ihr persönliches Angebot", "Jetzt in der App ansehen & annehmen.", inner),
                idempotency_key=(
                    f"machine-request:{req_id}:offer:"
                    + hashlib.sha256(json.dumps(terms, sort_keys=True, default=str).encode()).hexdigest()[:16]
                ),
                template_key="machine.offer", resource_type="machine_request", resource_id=req_id,
            )
        except Exception:
            await report_operational_failure(
                access, logger, operation="machine.offer_email", category="email_delivery",
            )
    return strip_id(await access.machine_requests.find_one({"id": req_id}))


async def accept_machine_offer(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if user["role"] != "admin":
        ids = await visible_company_ids(user, access)
        if not _owns(r, user, ids):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
    existing_contract = await access.contracts.find_one({"machineRequestId": req_id})
    if existing_contract:
        await access.machine_requests.update_one(
            {"id": req_id},
            {"$set": {"status": "Bestätigt", "contractId": existing_contract["id"]}},
        )
        return strip_id(await access.machine_requests.find_one({"id": req_id}))
    if r.get("status") != "Angebot":
        raise HTTPException(status_code=409, detail="Es liegt kein offenes Angebot vor")
    updates = {"status": "Bestätigt"}

    # Leasing acceptance automatically creates a coffee-binding contract.
    contract_id = r.get("contractId")
    if r.get("type") == "leasing" and not contract_id and r["customer"].get("companyId"):
        t = r.get("terms") or {}
        company_id = r["customer"].get("companyId")
        product_id = t.get("productId")
        if (
            not isinstance(company_id, str)
            or not await access.companies.find_one({"id": company_id})
            or not isinstance(product_id, str)
            or not await access.products.find_one({"id": product_id})
        ):
            raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
        now = datetime.now(timezone.utc)
        seq = await next_seq("contract")
        contract_id = f"S&S-{now.year}-M{seq:04d}"
        contract_currency = currency_code(t.get("currency") or access.context.default_currency)
        coffee_price_minor = (
            amount_minor(t, "coffeePricePerKg", expected_currency=contract_currency)
            if t.get("coffeePricePerKg") is not None or t.get("coffeePricePerKgMinor") is not None
            else 0
        )
        machine_rate_minor = (
            amount_minor(t, "monthlyRate", expected_currency=contract_currency)
            if t.get("monthlyRate") is not None or t.get("monthlyRateMinor") is not None
            else 0
        )
        company = await access.companies.find_one({"id": company_id})
        contract = {
            "id": contract_id,
            "companyId": company_id,
            "productId": product_id,
            "start": now.date().isoformat(),
            "termMonths": t.get("termMonths") or r.get("termMonths") or 48,
            "minQtyMonth": t.get("minCoffeeKgMonth") or 0,
            "price": from_minor(coffee_price_minor),
            "priceMinor": coffee_price_minor,
            "currency": contract_currency,
            "productName": t.get("coffeeName", ""),
            "machine": r["machineName"],
            "machineRate": from_minor(machine_rate_minor),
            "machineRateMinor": machine_rate_minor,
            "snapshotVersion": 1,
            "source": "machine_leasing",
            "machineRequestId": r["id"],
            "companySnapshot": company_snapshot(company),
            "billingAddressSnapshot": await resolve_address_snapshot(
                access, company_id, None, preferred_type="billing"
            ),
            "deliveryAddressSnapshot": await resolve_address_snapshot(
                access, company_id, None, preferred_type="shipping"
            ),
        }
        if operation_id:
            contract["operationId"] = operation_id
        contract_created = True
        try:
            await access.contracts.insert_one(contract)
        except DuplicateKeyError:
            existing_contract = await access.contracts.find_one({"machineRequestId": req_id})
            if not existing_contract:
                raise
            contract_id = existing_contract["id"]
            contract_created = False
        updates["contractId"] = contract_id
        if contract_created:
            await tenant_audit(access, user, "machine_contract_created", contract_id, {"requestId": r["id"]})

    await access.machine_requests.update_one({"id": req_id}, {"$set": updates})
    await tenant_audit(access, user, "machine_accept", req_id)
    return strip_id(await access.machine_requests.find_one({"id": req_id}))


@api_router.post("/machine-requests/{req_id}/accept")
async def accept_machine_offer_endpoint(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    service = IdempotencyService(
        access, actor_id=user["id"], operation="machine_request.accept",
        key=idempotency_key or "", payload={"machineRequestId": req_id},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await accept_machine_offer(
            req_id, user, access, operation_id=claim.record_id
        )
        refs = {"machineRequestId": req_id}
        if response.get("contractId"):
            refs["contractId"] = response["contractId"]
        await service.complete(claim, response, refs)
        return response
    except Exception as exc:
        await service.fail(claim, error_code="machine_accept_failed", exception=exc)
        raise


def _owns(r: dict, user: dict, ids: list) -> bool:
    if user["role"] == "admin":
        return True
    # sales and customers are both bounded by their visible companies
    return (r["customer"].get("companyId") in ids
            or r["customer"].get("userId") == user["id"])


@api_router.post("/machine-requests/{req_id}/respond")
async def respond_machine_offer(
    req_id: str,
    body: MachineRespondIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    ids = await visible_company_ids(user, access)
    if not _owns(r, user, ids):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if body.action not in ("decline", "question"):
        raise HTTPException(status_code=400, detail="Ungültige Aktion")
    now = datetime.now(timezone.utc).isoformat()
    if body.action == "decline":
        await access.machine_requests.update_one({"id": req_id}, {"$set": {"status": "Abgelehnt"}})
        await tenant_audit(access, user, "machine_decline", req_id)
        subject, headline = f"Angebot {req_id} abgelehnt", "Angebot abgelehnt"
        text = f"Der Kunde hat das Angebot für {escape(r['machineName'])} abgelehnt."
    else:
        entry = {"message": body.message, "at": now, "by": user.get("name", "Kunde")}
        await access.machine_requests.update_one(
            {"id": req_id}, {"$set": {"status": "Rückfrage"}, "$push": {"questions": entry}})
        await tenant_audit(access, user, "machine_question", req_id, {"message": body.message})
        subject, headline = f"Rückfrage zu {req_id}", "Neue Rückfrage"
        text = f"Rückfrage zu {escape(r['machineName'])}: {escape(body.message)}"
    try:
        inner = f"<p style='margin:0;color:#3A4256'>{text}</p>"
        await send_email(
            access=access, to=r["customer"]["email"], subject=subject,
            html=email_shell(headline, "Maschinen-Anfrage", inner),
            idempotency_key=(
                f"machine-request:{req_id}:response:{body.action}:"
                + hashlib.sha256((body.message or "").strip().encode()).hexdigest()[:16]
            ),
            template_key=f"machine.response.{body.action}", resource_type="machine_request", resource_id=req_id,
        )
    except Exception:
        await report_operational_failure(
            access, logger, operation="machine.response_email", category="email_delivery",
        )
    return strip_id(await access.machine_requests.find_one({"id": req_id}))


@api_router.get("/machines/leasing-contracts")
async def leasing_contracts(
    user: Annotated[dict, Depends(require_roles("admin", "sales"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    q = {"source": "machine_leasing"}
    if user["role"] != "admin":
        ids = await visible_company_ids(user, access)
        q["companyId"] = {"$in": ids}
    rows = await access.contracts.find(q).sort("start", -1).to_list(1000)
    out = []
    for c in rows:
        company = (
            await access.companies.find_one({"id": c.get("companyId")})
            if c.get("companyId")
            else None
        )
        prod = (
            await access.products.find_one({"id": c.get("productId")})
            if c.get("productId")
            else None
        )
        d = strip_id(c)
        d["companyName"] = company.get("name") if company else ""
        d["productName"] = c.get("productName") or (f"{prod.get('brand', '')} {prod.get('name', '')}".strip() if prod else "")
        out.append(d)
    return out


async def machine_checkout(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    *,
    operation_id: str | None = None,
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    ids = await visible_company_ids(user, access)
    if not _owns(r, user, ids):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if r.get("type") != "kauf":
        raise HTTPException(status_code=400, detail="Nur Direktkauf ist sofort zahlbar")
    if r.get("paymentStatus") == "Bezahlt":
        raise HTTPException(status_code=409, detail="Bereits bezahlt")
    try:
        currency = currency_code(r.get("currency") or access.context.default_currency)
        amount_cents = amount_minor(r, "machinePrice", expected_currency=currency)
    except MoneyError as exc:
        raise HTTPException(status_code=409, detail="Maschinenanfrage besitzt keinen gültigen Zahlungsbetrag") from exc

    try:
        success_url, cancel_url = checkout_return_urls("machine_request", req_id)
    except PaymentIntegrityError as exc:
        raise HTTPException(status_code=503, detail="Zahlungsdienst ist nicht vollständig konfiguriert") from exc
    return await create_stripe_checkout(
        access,
        resource_type="machine_request",
        resource_id=req_id,
        operation_id=operation_id or f"direct-machine-checkout:{req_id}",
        expected_amount_minor=amount_cents,
        currency=currency,
        product_name=r["machineName"],
        success_url=success_url,
        cancel_url=cancel_url,
    )


@api_router.post("/machine-requests/{req_id}/checkout")
async def machine_checkout_endpoint(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
    idempotency_key: Annotated[Optional[str], Header(alias="Idempotency-Key")] = None,
):
    request = await access.machine_requests.find_one({"id": req_id})
    if not request or not await _machine_request_references_visible(request, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if not _owns(request, user, await visible_company_ids(user, access)):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    service = IdempotencyService(
        access, actor_id=user["id"], operation="machine_request.checkout",
        key=idempotency_key or "", payload={"machineRequestId": req_id},
    )
    claim = await service.claim()
    if claim.is_replay:
        return claim.replay_response
    try:
        response = await machine_checkout(
            req_id, user, access,
            operation_id=claim.record_id,
        )
        await service.complete(
            claim, response,
            {"machineRequestId": req_id, "stripeSessionId": response["sessionId"]},
        )
        return response
    except Exception as exc:
        await service.fail(claim, error_code="machine_checkout_failed", exception=exc)
        raise


@api_router.get("/machine-requests/{req_id}/payment-status")
async def machine_payment_status(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    ids = await visible_company_ids(user, access)
    if not _owns(r, user, ids):
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if r.get("paymentStatus") == "Bezahlt":
        return {"status": "Bezahlt"}
    response = {"status": r.get("paymentStatus", "Offen")}
    if r.get("stripeCheckoutState") is not None:
        response["checkoutState"] = r["stripeCheckoutState"]
    if r.get("stripePaymentStatus") is not None:
        response["providerStatus"] = r["stripePaymentStatus"]
    return response
