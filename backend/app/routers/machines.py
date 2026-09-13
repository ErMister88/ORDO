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
from ..deps import require_roles, current_user, visible_company_ids
from ..models import MachineIn, MachineRequestIn, MachineTermsIn
from ..emailer import send_email, email_shell

stripe.api_key = os.environ.get("STRIPE_API_KEY", "")
APP_URL = (os.environ.get("APP_URL") or "https://ordo-connect.preview.emergentagent.com").rstrip("/")

TYPE_LABEL = {"kauf": "Kauf", "finanzierung": "Finanzierung", "leasing": "Leasing (Kaffeebindung)"}

DEMO_MACHINES = [
    {"name": "Espressomaschine La Marzocco Linea Mini", "price": 5900.0,
     "description": "Zweikreiser-Siebträger für höchste Ansprüche. Ideal für Büros und Gastronomie.",
     "imageUrl": ""},
    {"name": "Vollautomat WMF 1500 S+", "price": 8900.0,
     "description": "Kaffeevollautomat für hohe Volumen, bis zu 250 Tassen/Tag.",
     "imageUrl": ""},
    {"name": "Siebträger ECM Synchronika", "price": 3200.0,
     "description": "Dualboiler-Siebträger, Edelstahl, perfekt für kleine Teams.",
     "imageUrl": ""},
]


async def _ensure_seed():
    if await db.machines.count_documents({}) == 0:
        now = datetime.now(timezone.utc).isoformat()
        await db.machines.insert_many([
            {"id": str(uuid.uuid4()), "taxRate": 19, "active": True, "createdAt": now, **m}
            for m in DEMO_MACHINES
        ])


# ---------------- Catalog ----------------
@api_router.get("/machines")
async def list_machines(user: Annotated[dict, Depends(current_user)]):
    await _ensure_seed()
    q = {} if user["role"] in ("admin", "sales") else {"active": True}
    rows = await db.machines.find(q).sort("price", 1).to_list(500)
    return [strip_id(r) for r in rows]


@api_router.post("/machines", status_code=201)
async def create_machine(body: MachineIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    doc = {"id": str(uuid.uuid4()), "taxRate": 19,
           "createdAt": datetime.now(timezone.utc).isoformat(), **body.model_dump()}
    await db.machines.insert_one(doc)
    await audit(user, "machine_create", doc["id"], {"name": body.name})
    return strip_id(doc)


@api_router.put("/machines/{machine_id}")
async def update_machine(machine_id: str, body: MachineIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    r = await db.machines.update_one({"id": machine_id}, {"$set": body.model_dump()})
    if r.matched_count == 0:
        raise HTTPException(status_code=404, detail="Maschine nicht gefunden")
    await audit(user, "machine_update", machine_id, {"name": body.name})
    return strip_id(await db.machines.find_one({"id": machine_id}))


@api_router.delete("/machines/{machine_id}")
async def delete_machine(machine_id: str, user: Annotated[dict, Depends(require_roles("admin"))]):
    await db.machines.update_one({"id": machine_id}, {"$set": {"active": False}})
    await audit(user, "machine_delete", machine_id)
    return {"ok": True}


# ---------------- Requests / acquisition ----------------
async def _customer_snapshot(user: dict) -> dict:
    company = None
    if user.get("companyId"):
        company = await db.companies.find_one({"id": user["companyId"]})
    return {"userId": user["id"], "companyId": user.get("companyId"),
            "userName": user.get("name", ""), "email": user.get("email", ""),
            "companyName": company.get("name") if company else ""}


@api_router.post("/machine-requests", status_code=201)
async def create_machine_request(body: MachineRequestIn, user: Annotated[dict, Depends(current_user)]):
    if body.type not in TYPE_LABEL:
        raise HTTPException(status_code=400, detail="Ungültiger Erwerbstyp")
    m = await db.machines.find_one({"id": body.machineId})
    if not m or not m.get("active", True):
        raise HTTPException(status_code=404, detail="Maschine nicht verfügbar")
    now = datetime.now(timezone.utc)
    seq = await next_seq("machinereq")
    rid = f"M-{now.year}-{seq:05d}"
    status = "Zahlung offen" if body.type == "kauf" else "Angefragt"
    doc = {
        "id": rid, "machineId": m["id"], "machineName": m["name"], "machinePrice": float(m["price"]),
        "type": body.type, "termMonths": body.termMonths or 48, "message": body.message,
        "customer": await _customer_snapshot(user),
        "status": status, "paymentStatus": "Offen",
        "terms": None, "createdAt": now.isoformat(),
    }
    await db.machine_requests.insert_one(doc)

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
async def list_machine_requests(user: Annotated[dict, Depends(current_user)]):
    if user["role"] in ("admin", "sales"):
        rows = await db.machine_requests.find({}).sort("createdAt", -1).to_list(1000)
    else:
        ids = await visible_company_ids(user)
        rows = await db.machine_requests.find(
            {"$or": [{"customer.companyId": {"$in": ids}}, {"customer.userId": user["id"]}]}
        ).sort("createdAt", -1).to_list(1000)
    return [strip_id(r) for r in rows]


@api_router.put("/machine-requests/{req_id}")
async def set_machine_terms(req_id: str, body: MachineTermsIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    r = await db.machine_requests.find_one({"id": req_id})
    if not r:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    terms = {
        "downPayment": body.downPayment, "monthlyRate": body.monthlyRate,
        "finalPayment": body.finalPayment, "termMonths": body.termMonths or r.get("termMonths"),
        "minCoffeeKgMonth": body.minCoffeeKgMonth, "note": body.note,
    }
    status = body.status or "Angebot"
    await db.machine_requests.update_one({"id": req_id}, {"$set": {"terms": terms, "status": status}})
    await audit(user, "machine_terms", req_id, {"status": status})

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
        body_html = "".join(f"<li>{escape(x)}</li>" for x in lines)
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
    return strip_id(await db.machine_requests.find_one({"id": req_id}))


@api_router.post("/machine-requests/{req_id}/accept")
async def accept_machine_offer(req_id: str, user: Annotated[dict, Depends(current_user)]):
    r = await db.machine_requests.find_one({"id": req_id})
    if not r:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    if user["role"] not in ("admin", "sales"):
        ids = await visible_company_ids(user)
        if r["customer"].get("companyId") not in ids and r["customer"].get("userId") != user["id"]:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if r.get("status") != "Angebot":
        raise HTTPException(status_code=409, detail="Es liegt kein offenes Angebot vor")
    await db.machine_requests.update_one({"id": req_id}, {"$set": {"status": "Bestätigt"}})
    await audit(user, "machine_accept", req_id)
    return strip_id(await db.machine_requests.find_one({"id": req_id}))


def _owns(r: dict, user: dict, ids: list) -> bool:
    return (user["role"] in ("admin", "sales")
            or r["customer"].get("companyId") in ids
            or r["customer"].get("userId") == user["id"])


@api_router.post("/machine-requests/{req_id}/checkout")
async def machine_checkout(req_id: str, user: Annotated[dict, Depends(current_user)]):
    r = await db.machine_requests.find_one({"id": req_id})
    if not r:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    ids = await visible_company_ids(user)
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
    await db.machine_requests.update_one({"id": req_id}, {"$set": {"stripeSessionId": session.id}})
    return {"url": session.url, "sessionId": session.id}


@api_router.get("/machine-requests/{req_id}/payment-status")
async def machine_payment_status(req_id: str, user: Annotated[dict, Depends(current_user)]):
    r = await db.machine_requests.find_one({"id": req_id})
    if not r:
        raise HTTPException(status_code=404, detail="Anfrage nicht gefunden")
    ids = await visible_company_ids(user)
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
            await db.machine_requests.update_one(
                {"id": req_id, "paymentStatus": {"$ne": "Bezahlt"}},
                {"$set": {"paymentStatus": "Bezahlt", "status": "Gekauft",
                          "paidAt": datetime.now(timezone.utc).isoformat()}},
            )
            return {"status": "Bezahlt"}
    return {"status": r.get("paymentStatus", "Offen")}
