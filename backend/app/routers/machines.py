"""B2B machines: admin catalog + acquisition (Kauf / Finanzierung / Leasing)."""
import os
import uuid
from datetime import datetime, timezone
from html import escape
from typing import Annotated, Optional

import stripe
from fastapi import Depends, HTTPException
from starlette.concurrency import run_in_threadpool

from ..core import api_router, db, strip_id, next_seq, logger, audit
from ..deps import current_user, require_roles, tenant_business_access, visible_company_ids
from ..models import MachineIn, MachineRequestIn, MachineTermsIn, MachineRespondIn
from ..emailer import send_email, email_shell
from ..tenant_access import TenantBusinessAccess

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = (os.environ.get("APP_URL") or "https://ordo-connect.preview.emergentagent.com").rstrip("/")

TYPE_LABEL = {"kauf": "Kauf", "finanzierung": "Finanzierung", "leasing": "Leasing (Kaffeebindung)"}

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
    doc = {"id": str(uuid.uuid4()), "taxRate": 19,
           "createdAt": datetime.now(timezone.utc).isoformat(), **body.model_dump()}
    await access.machines.insert_one(doc)
    await audit(user, "machine_create", doc["id"], {"name": body.name}, tenant_id=access.context.tenant_id)
    return strip_id(doc)


@api_router.put("/machines/{machine_id}")
async def update_machine(
    machine_id: str,
    body: MachineIn,
    user: Annotated[dict, Depends(require_roles("admin"))],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    r = await access.machines.update_one({"id": machine_id}, {"$set": body.model_dump()})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden")
    await audit(user, "machine_update", machine_id, {"name": body.name}, tenant_id=access.context.tenant_id)
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
    await audit(user, "machine_delete", machine_id, tenant_id=access.context.tenant_id)
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
    if not isinstance(machine_id, str) or not await access.machines.find_one({"id": machine_id}):
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


@api_router.post("/machine-requests", status_code=201)
async def create_machine_request(
    body: MachineRequestIn,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    if body.type not in TYPE_LABEL:
        raise HTTPException(status_code=400, detail="Ungültiger Erwerbstyp")
    m = await access.machines.find_one({"id": body.machineId})
    if not m or not m.get("active", True):
        raise HTTPException(status_code=404, detail="Maschine nicht verfügbar")
    now = datetime.now(timezone.utc)
    seq = await next_seq("machinereq")
    rid = f"M-{now.year}-{seq:05d}"
    status = "Zahlung offen" if body.type == "kauf" else "Angefragt"
    doc = {
        "id": rid, "machineId": m["id"], "machineName": m["name"], "machinePrice": float(m["price"]),
        "type": body.type, "termMonths": body.termMonths or 48, "message": body.message,
        "customer": await _customer_snapshot(user, access),
        "status": status, "paymentStatus": "Offen",
        "terms": None, "createdAt": now.isoformat(),
    }
    await access.machine_requests.insert_one(doc)

    # Notify staff about new requests (best-effort).
    if body.type != "kauf":
        try:
            inner = (
                f"<p style='margin:0 0 8px;color:#3A4256'>Neue Maschinen-Anfrage <strong>{escape(rid)}</strong></p>"
                f"<p style='margin:0;color:#3A4256'>Typ: {TYPE_LABEL[body.type]}<br/>"
                f"Maschine: {escape(m['name'])}<br/>Kunde: {escape(doc['customer'].get('companyName') or doc['customer']['userName'])}</p>"
            )
            await send_email(to=doc["customer"]["email"], subject=f"Ihre Maschinen-Anfrage {rid}",
                             html=email_shell("Anfrage eingegangen", "Wir melden uns mit einem Angebot.", inner))
        except Exception as e:
            logger.warning(f"Anfrage-Mail fehlgeschlagen: {e}")
    return strip_id(doc)


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
        # Never bind coffee below the product's absolute floor price.
        floor = p.get("absoluteFloor")
        if body.coffeePricePerKg is not None and floor is not None and body.coffeePricePerKg < floor:
            raise HTTPException(status_code=400,
                                detail=f"Kaffeepreis darf {floor:.2f} €/kg nicht unterschreiten.")

    terms = {
        "downPayment": body.downPayment, "monthlyRate": body.monthlyRate,
        "finalPayment": body.finalPayment, "termMonths": body.termMonths or r.get("termMonths"),
        "minCoffeeKgMonth": body.minCoffeeKgMonth,
        "productId": body.productId, "coffeePricePerKg": body.coffeePricePerKg, "coffeeName": coffee_name,
        "note": body.note,
    }
    await access.machine_requests.update_one({"id": req_id}, {"$set": {"terms": terms, "status": status}})
    await audit(user, "machine_terms", req_id, {"status": status}, tenant_id=access.context.tenant_id)

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
            await send_email(to=email, subject=f"Ihr Angebot {req_id} – {r['machineName']}",
                             html=email_shell("Ihr persönliches Angebot", "Jetzt in der App ansehen & annehmen.", inner))
        except Exception as e:
            logger.warning(f"Angebots-Mail fehlgeschlagen: {e}")
    return strip_id(await access.machine_requests.find_one({"id": req_id}))


@api_router.post("/machine-requests/{req_id}/accept")
async def accept_machine_offer(
    req_id: str,
    user: Annotated[dict, Depends(current_user)],
    access: Annotated[TenantBusinessAccess, Depends(tenant_business_access)],
):
    r = await access.machine_requests.find_one({"id": req_id})
    if not r or not await _machine_request_references_visible(r, access):
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if user["role"] != "admin":
        ids = await visible_company_ids(user, access)
        if not _owns(r, user, ids):
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
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
        await access.contracts.insert_one({
            "id": contract_id,
            "companyId": company_id,
            "productId": product_id,
            "start": now.date().isoformat(),
            "termMonths": t.get("termMonths") or r.get("termMonths") or 48,
            "minQtyMonth": t.get("minCoffeeKgMonth") or 0,
            "price": t.get("coffeePricePerKg") or 0,
            "machine": r["machineName"],
            "machineRate": t.get("monthlyRate") or 0,
            "source": "machine_leasing",
            "machineRequestId": r["id"],
        })
        updates["contractId"] = contract_id
        await audit(user, "machine_contract_created", contract_id, {"requestId": r["id"]}, tenant_id=access.context.tenant_id)

    await access.machine_requests.update_one({"id": req_id}, {"$set": updates})
    await audit(user, "machine_accept", req_id, tenant_id=access.context.tenant_id)
    return strip_id(await access.machine_requests.find_one({"id": req_id}))


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
        await audit(user, "machine_decline", req_id, tenant_id=access.context.tenant_id)
        subject, headline = f"Angebot {req_id} abgelehnt", "Angebot abgelehnt"
        text = f"Der Kunde hat das Angebot für {escape(r['machineName'])} abgelehnt."
    else:
        entry = {"message": body.message, "at": now, "by": user.get("name", "Kunde")}
        await access.machine_requests.update_one(
            {"id": req_id}, {"$set": {"status": "Rückfrage"}, "$push": {"questions": entry}})
        await audit(user, "machine_question", req_id, {"message": body.message}, tenant_id=access.context.tenant_id)
        subject, headline = f"Rückfrage zu {req_id}", "Neue Rückfrage"
        text = f"Rückfrage zu {escape(r['machineName'])}: {escape(body.message)}"
    try:
        inner = f"<p style='margin:0;color:#3A4256'>{text}</p>"
        await send_email(to=r["customer"]["email"], subject=subject,
                         html=email_shell(headline, "Maschinen-Anfrage", inner))
    except Exception as e:
        logger.warning(f"Antwort-Mail fehlgeschlagen: {e}")
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
        d["productName"] = f"{prod.get('brand', '')} {prod.get('name', '')}".strip() if prod else ""
        out.append(d)
    return out


@api_router.post("/machine-requests/{req_id}/checkout")
async def machine_checkout(
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
    if r.get("type") != "kauf":
        raise HTTPException(status_code=400, detail="Nur Direktkauf ist sofort zahlbar")
    if r.get("paymentStatus") == "Bezahlt":
        raise HTTPException(status_code=409, detail="Bereits bezahlt")
    amount_cents = int(round(float(r["machinePrice"]) * 100))

    def _create():
        return stripe.checkout.Session.create(
            mode="payment", currency="eur", locale="de",
            line_items=[{"price_data": {"currency": "eur", "unit_amount": amount_cents,
                                         "product_data": {"name": r["machineName"]}}, "quantity": 1}],
            client_reference_id=req_id, metadata={"machineRequestId": req_id},
            success_url=f"{APP_URL}/?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{APP_URL}/?payment=cancelled",
        )

    try:
        session = await run_in_threadpool(_create)
    except Exception as e:
        logger.warning(f"Stripe Checkout (Maschine) fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail="Zahlung konnte nicht gestartet werden")
    await access.machine_requests.update_one({"id": req_id}, {"$set": {"stripeSessionId": session.id}})
    return {"url": session.url, "sessionId": session.id}


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
    sid = r.get("stripeSessionId")
    if sid:
        try:
            session = await run_in_threadpool(stripe.checkout.Session.retrieve, sid)
        except Exception as e:
            logger.warning(f"Stripe Status (Maschine) fehlgeschlagen: {e}")
            session = None
        if session and session.get("payment_status") in ("paid", "no_payment_required"):
            await access.machine_requests.update_one(
                {"id": req_id, "paymentStatus": {"$ne": "Bezahlt"}},
                {"$set": {"paymentStatus": "Bezahlt", "status": "Gekauft",
                          "paidAt": datetime.now(timezone.utc).isoformat()}},
            )
            return {"status": "Bezahlt"}
    return {"status": r.get("paymentStatus", "Offen")}
