from fastapi import FastAPI, APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from starlette.concurrency import run_in_threadpool
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from pymongo import ReturnDocument
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import jwt
import bcrypt
import uuid
import re
import ipaddress
import secrets
import string
import hashlib
import requests
import httpx
from html import escape
from html.parser import HTMLParser
from urllib.parse import urlparse
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional, Literal, Annotated
from datetime import datetime, timedelta, timezone

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / ".env")

mongo_url = os.environ["MONGO_URL"]
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ["DB_NAME"]]

JWT_SECRET = os.environ["JWT_SECRET"]
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "720"))

app = FastAPI(title="S&S B2B API")
api_router = APIRouter(prefix="/api")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

Role = Literal["admin", "sales", "customer"]


# --------------------------------------------------------------------------
# Emergent Managed Object Storage
# --------------------------------------------------------------------------
STORAGE_BASE = (os.environ.get("INTEGRATION_PROXY_URL") or "").strip() or "https://integrations.emergentagent.com"
STORAGE_URL = STORAGE_BASE.rstrip("/") + "/objstore/api/v1/storage"
EMERGENT_KEY = os.environ.get("EMERGENT_LLM_KEY")
APP_NAME = "ss-grosshandel"
_storage_key = None


def init_storage():
    global _storage_key
    if _storage_key:
        return _storage_key
    resp = requests.post(f"{STORAGE_URL}/init", json={"emergent_key": EMERGENT_KEY}, timeout=30)
    resp.raise_for_status()
    _storage_key = resp.json()["storage_key"]
    return _storage_key


def put_object(path: str, data: bytes, content_type: str) -> dict:
    key = init_storage()
    resp = requests.put(
        f"{STORAGE_URL}/objects/{path}",
        headers={"X-Storage-Key": key, "Content-Type": content_type},
        data=data,
        timeout=120,
    )
    resp.raise_for_status()
    return resp.json()


def get_object(path: str):
    global _storage_key
    key = init_storage()
    resp = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    if resp.status_code == 503:
        _storage_key = None
        key = init_storage()
        resp = requests.get(f"{STORAGE_URL}/objects/{path}", headers={"X-Storage-Key": key}, timeout=60)
    resp.raise_for_status()
    return resp.content, resp.headers.get("Content-Type", "application/octet-stream")


# --------------------------------------------------------------------------
# Emergent Managed Email (Resend) — transactional notifications
# --------------------------------------------------------------------------
EMAIL_BASE_URL = "https://integrations.emergentagent.com"
EMAIL_KEY = os.environ.get("EMERGENT_EMAIL_KEY", "")
EMAIL_FROM_NAME = os.environ.get("EMAIL_FROM_NAME", "ORDO Connect by S&S")
EMAIL_REPLY_TO = os.environ.get("EMAIL_REPLY_TO")

_SHORTENERS = ("bit.ly", "tinyurl.com", "t.co", "is.gd", "cutt.ly", "goo.gl", "rebrand.ly")
_CRED_ASK = ("reply with your password", "reply with the code", "send your password", "cvv",
             "send us your password", "enter your password below", "confirm your card number",
             "your full card number", "seed phrase", "recovery phrase", "verify your card",
             "social security number", "confirm your bank details")
_HOSTISH = re.compile(r"\b(?:https?://)?((?:[a-z0-9-]+\.)+[a-z]{2,})", re.I)


def _host_ok(host: str) -> bool:
    if not host or "xn--" in host:
        return False
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    return not any(host == s or host.endswith("." + s) for s in _SHORTENERS)


def _same_site(shown: str, real: str) -> bool:
    return shown == real or real.endswith("." + shown) or shown.endswith("." + real)


class _EmailScan(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.urls, self.anchors = set(), [], []
        self._href, self._text = None, []

    def handle_starttag(self, tag, attrs):
        self.tags.add(tag.lower())
        self.urls += [v for k, v in attrs if k.lower() in ("href", "src") and v]
        if tag.lower() == "a":
            self._href = dict((k.lower(), v) for k, v in attrs).get("href")
            self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            self.anchors.append((self._href, "".join(self._text)))
            self._href, self._text = None, []


def _assert_safe_email(subject: str, html: str) -> None:
    scan = _EmailScan()
    scan.feed(html)
    if scan.tags & {"form", "input", "textarea", "select"}:
        raise ValueError("No forms or input fields in email (G2)")
    body = f"{subject}\n{html}".lower()
    for p in _CRED_ASK:
        if p in body:
            raise ValueError(f"Email asks the recipient for credentials: {p!r} (G2)")
    for url in scan.urls:
        low = url.strip().lower()
        if low.startswith(("mailto:", "tel:", "cid:", "#")):
            continue
        if not low.startswith("https://"):
            raise ValueError(f"Email links/assets must be absolute https: {url!r} (G3)")
        host = urlparse(low).hostname or ""
        if not _host_ok(host) or urlparse(low).username is not None:
            raise ValueError(f"Shortened, numeric-host or credential-bearing URL: {url!r} (G3)")
    for href, text in scan.anchors:
        real = urlparse(href.strip().lower()).hostname or ""
        if not real:
            continue
        for m in _HOSTISH.finditer(text):
            if not _same_site(m.group(1).lower(), real):
                raise ValueError(f"Anchor text {m.group(1)!r} != real link host {real!r} (G3)")


async def send_email(*, to: str, subject: str, html: str) -> Optional[str]:
    _assert_safe_email(subject, html)
    payload = {"to": [to], "subject": subject, "html": html, "from_name": EMAIL_FROM_NAME}
    if EMAIL_REPLY_TO:
        payload["contact_email"] = EMAIL_REPLY_TO
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{EMAIL_BASE_URL}/api/v1/email/send",
            headers={"X-Email-Key": EMAIL_KEY},
            json=payload,
        )
    resp.raise_for_status()
    return resp.json().get("id")


async def _company_recipient(company_id: str):
    c = await db.companies.find_one({"id": company_id})
    if not c:
        return None, ""
    return c.get("email"), c.get("name", "")


async def _items_html(items: list) -> str:
    rows = ""
    for it in items:
        p = await db.products.find_one({"id": it["productId"]})
        label = f"{p['brand']} {p['name']}" if p else it["productId"]
        unit = (p or {}).get("unit", "kg")
        rows += (
            "<tr>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF'>{escape(label)}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{it['qty']:g} {escape(unit)}</td>"
            f"<td style='padding:6px 8px;border-bottom:1px solid #E6E8EF;text-align:right'>{it['price']:.2f} &euro;</td>"
            "</tr>"
        )
    return rows


def _email_shell(heading: str, intro: str, body_inner: str) -> str:
    return (
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='background:#F4F6FB'>"
        "<tr><td align='center' style='padding:24px'>"
        "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' "
        "style='max-width:560px;background:#FFFFFF;border-radius:16px;overflow:hidden;font-family:Arial,Helvetica,sans-serif'>"
        "<tr><td style='background:#0B1B3D;padding:20px 24px'>"
        f"<span style='color:#FFFFFF;font-size:18px;font-weight:bold'>{escape(EMAIL_FROM_NAME)}</span></td></tr>"
        "<tr><td style='padding:24px'>"
        f"<h2 style='margin:0 0 8px;color:#0B1B3D;font-size:20px'>{escape(heading)}</h2>"
        f"<p style='margin:0 0 16px;color:#3A4256;font-size:15px;line-height:1.5'>{intro}</p>"
        f"{body_inner}"
        "<p style='margin:20px 0 0;font-size:12px;color:#8A90A2;line-height:1.5'>"
        f"Gesendet von {escape(EMAIL_FROM_NAME)}. Wir fragen Sie niemals per E-Mail nach Passwort oder Zahlungsdaten."
        "</p>"
        "</td></tr></table></td></tr></table>"
    )


# --------------------------------------------------------------------------
# Password helpers (bcrypt directly)
# --------------------------------------------------------------------------
def hash_pw(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_pw(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


DUMMY_HASH = hash_pw("dummy-not-a-real-account")


def random_password(n: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def gen_reset_code() -> tuple:
    code = f"{secrets.randbelow(1000000):06d}"
    digest = hashlib.sha256(code.encode()).hexdigest()
    return code, digest


def create_token(user: dict) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "iat": now,
        "exp": now + timedelta(minutes=TOKEN_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class PublicUser(BaseModel):
    id: str
    email: str
    name: str
    role: Role
    companyId: Optional[str] = None
    salesRepId: Optional[str] = None


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: PublicUser


class OfferItemIn(BaseModel):
    productId: str
    qty: float
    price: float


class OfferCreate(BaseModel):
    companyId: str
    items: List[OfferItemIn]
    termMonths: int = 48
    reason: Optional[str] = ""


class OrderCreate(BaseModel):
    companyId: str
    items: List[OfferItemIn]


class DecisionIn(BaseModel):
    note: Optional[str] = ""


class DiscountTier(BaseModel):
    minQty: float
    price: float


class ProductIn(BaseModel):
    brand: str
    name: str
    unit: str = "kg"
    standardPrice: float
    salesFloor: float
    absoluteFloor: float
    cost: float
    description: str = ""
    imageUrl: str = ""
    discountTiers: List[DiscountTier] = []
    active: bool = True


class CustomerPriceIn(BaseModel):
    companyId: str
    productId: str
    price: float


class OrderStatusIn(BaseModel):
    status: str


class ActiveIn(BaseModel):
    active: bool


class NewCompanyIn(BaseModel):
    name: str
    city: str = ""
    email: str = ""
    phone: str = ""


class CreateUserIn(BaseModel):
    name: str
    email: str
    role: Literal["sales", "customer"]
    companyId: Optional[str] = None
    newCompany: Optional[NewCompanyIn] = None


class ForgotPwIn(BaseModel):
    email: str


class ResetPwIn(BaseModel):
    email: str
    code: str
    newPassword: str


class ChangePwIn(BaseModel):
    currentPassword: str
    newPassword: str


ORDER_STATUS_FLOW = ["Neu", "Bestätigt", "Kommissioniert", "Versendet", "Abgeschlossen"]


def strip_id(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


async def next_seq(name: str) -> int:
    doc = await db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


# --------------------------------------------------------------------------
# Auth dependencies
# --------------------------------------------------------------------------
async def current_user(token: Annotated[str, Depends(oauth2_scheme)]) -> dict:
    err = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Ungültige oder abgelaufene Anmeldung",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        uid = payload.get("sub")
        if not uid:
            raise err
    except Exception:
        raise err
    user = await db.users.find_one({"id": uid})
    if not user:
        raise err
    return user


def require_roles(*allowed: Role):
    async def dep(user: Annotated[dict, Depends(current_user)]) -> dict:
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Keine Berechtigung")
        return user

    return dep


# --------------------------------------------------------------------------
# Visibility helpers
# --------------------------------------------------------------------------
async def visible_company_ids(user: dict) -> List[str]:
    if user["role"] == "admin":
        companies = await db.companies.find({"active": True}).to_list(1000)
        return [c["id"] for c in companies]
    if user["role"] == "sales":
        companies = await db.companies.find(
            {"assignedSalesRepId": user["id"], "active": True}
        ).to_list(1000)
        return [c["id"] for c in companies]
    # customer
    return [user["companyId"]] if user.get("companyId") else []


# --------------------------------------------------------------------------
# Routes: Auth
# --------------------------------------------------------------------------
@api_router.post("/auth/login", response_model=Token)
async def login(form: Annotated[OAuth2PasswordRequestForm, Depends()]):
    email = form.username.strip().lower()
    user = await db.users.find_one({"email": email})
    if not user:
        verify_pw(form.password, DUMMY_HASH)
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    if not verify_pw(form.password, user["hashed_password"]):
        raise HTTPException(status_code=401, detail="E-Mail oder Passwort falsch")
    return {"access_token": create_token(user), "user": PublicUser(**strip_id(user))}


@api_router.get("/auth/me", response_model=PublicUser)
async def me(user: Annotated[dict, Depends(current_user)]):
    return PublicUser(**strip_id(user))


# --------------------------------------------------------------------------
# Routes: User management (admin) + password reset / change
# --------------------------------------------------------------------------
@api_router.get("/users")
async def list_users(user: Annotated[dict, Depends(require_roles("admin"))]):
    users = await db.users.find({}).to_list(1000)
    comps = {c["id"]: c["name"] for c in await db.companies.find({}).to_list(1000)}
    users.sort(key=lambda u: u.get("createdAt", ""))
    return [
        {
            "id": u["id"], "name": u.get("name"), "email": u["email"], "role": u["role"],
            "companyId": u.get("companyId"), "companyName": comps.get(u.get("companyId")),
            "createdAt": u.get("createdAt"),
        }
        for u in users
    ]


@api_router.post("/users")
async def create_user(body: CreateUserIn, admin: Annotated[dict, Depends(require_roles("admin"))]):
    email = body.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Ungültige E-Mail-Adresse")
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=409, detail="E-Mail-Adresse ist bereits vergeben")
    company_id = None
    if body.role == "customer":
        if body.newCompany and body.newCompany.name.strip():
            company_id = "c" + secrets.token_hex(4)
            await db.companies.insert_one({
                "id": company_id, "name": body.newCompany.name.strip(), "city": body.newCompany.city.strip(),
                "email": body.newCompany.email.strip(), "phone": body.newCompany.phone.strip(), "vatId": "",
                "assignedSalesRepId": admin["id"], "active": True, "monthlyKg": 0, "orderCycleDays": 30,
            })
        elif body.companyId:
            c = await db.companies.find_one({"id": body.companyId})
            if not c:
                raise HTTPException(status_code=400, detail="Firma nicht gefunden")
            company_id = body.companyId
        else:
            raise HTTPException(status_code=400, detail="Für ein Kundenkonto ist eine Firma erforderlich")
    uid = "u-" + secrets.token_hex(5)
    pw = random_password()
    doc = {
        "id": uid, "name": body.name.strip() or email, "email": email, "role": body.role,
        "hashed_password": hash_pw(pw), "companyId": company_id,
        "salesRepId": uid if body.role == "sales" else None,
        "must_change_password": True,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }
    await db.users.insert_one(doc)
    return {"id": uid, "name": doc["name"], "email": email, "role": body.role,
            "companyId": company_id, "initialPassword": pw}


@api_router.post("/users/{user_id}/reset")
async def admin_reset_password(user_id: str, admin: Annotated[dict, Depends(require_roles("admin"))]):
    u = await db.users.find_one({"id": user_id})
    if not u:
        raise HTTPException(status_code=404, detail="Benutzer nicht gefunden")
    pw = random_password()
    await db.users.update_one({"id": user_id}, {"$set": {"hashed_password": hash_pw(pw), "must_change_password": True}})
    return {"id": user_id, "email": u["email"], "initialPassword": pw}


@api_router.post("/auth/password/forgot")
async def forgot_password(body: ForgotPwIn):
    email = body.email.strip().lower()
    u = await db.users.find_one({"email": email})
    if u:
        code, digest = gen_reset_code()
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        await db.password_resets.delete_many({"userId": u["id"]})
        await db.password_resets.insert_one({"userId": u["id"], "codeHash": digest, "expiresAt": expires, "used": False})
        try:
            inner = (
                f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(u.get('name', ''))},<br>"
                "Ihr Sicherheitscode zum Zur&uuml;cksetzen des Passworts lautet:</p>"
                f"<p style='font-size:30px;font-weight:bold;letter-spacing:6px;color:#0B1B3D;margin:0 0 12px'>{code}</p>"
                "<p style='margin:0;color:#8A90A2;font-size:13px'>G&uuml;ltig f&uuml;r 30 Minuten. "
                "Falls Sie das nicht angefordert haben, ignorieren Sie diese E-Mail.</p>"
            )
            html = _email_shell("Passwort zur&uuml;cksetzen", "Sie haben einen Sicherheitscode angefordert.", inner)
            await send_email(to=email, subject="Ihr Code zum Zurücksetzen des Passworts", html=html)
        except Exception as e:
            logger.warning(f"E-Mail (Reset-Code) fehlgeschlagen: {e}")
    return {"ok": True, "message": "Falls die E-Mail existiert, wurde ein Code gesendet."}


@api_router.post("/auth/password/reset")
async def reset_password(body: ResetPwIn):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    email = body.email.strip().lower()
    u = await db.users.find_one({"email": email})
    if not u:
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    digest = hashlib.sha256(body.code.strip().encode()).hexdigest()
    rec = await db.password_resets.find_one({"userId": u["id"], "codeHash": digest, "used": False})
    if not rec:
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    exp = rec["expiresAt"]
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="Code ungültig oder abgelaufen")
    res = await db.password_resets.update_one({"_id": rec["_id"], "used": False}, {"$set": {"used": True}})
    if res.modified_count != 1:
        raise HTTPException(status_code=400, detail="Code wurde bereits verwendet")
    await db.users.update_one({"id": u["id"]}, {"$set": {"hashed_password": hash_pw(body.newPassword), "must_change_password": False}})
    return {"ok": True, "message": "Passwort geändert. Bitte neu anmelden."}


@api_router.post("/auth/password/change")
async def change_password(body: ChangePwIn, user: Annotated[dict, Depends(current_user)]):
    if len(body.newPassword) < 8:
        raise HTTPException(status_code=400, detail="Passwort muss mindestens 8 Zeichen haben")
    if not verify_pw(body.currentPassword, user["hashed_password"]):
        raise HTTPException(status_code=400, detail="Aktuelles Passwort ist falsch")
    await db.users.update_one({"id": user["id"]}, {"$set": {"hashed_password": hash_pw(body.newPassword), "must_change_password": False}})
    return {"ok": True, "message": "Passwort geändert."}


# --------------------------------------------------------------------------
# Routes: Products
# --------------------------------------------------------------------------
@api_router.get("/products")
async def get_products(user: Annotated[dict, Depends(current_user)]):
    prods = await db.products.find({}).to_list(1000)
    result = []
    for p in prods:
        p = strip_id(p)
        if user["role"] == "customer":
            p.pop("cost", None)
            p.pop("salesFloor", None)
            p.pop("absoluteFloor", None)
        elif user["role"] == "sales":
            # DB / Deckungsbeitrag is internal — hide cost so margin can't be derived
            p.pop("cost", None)
        result.append(p)
    return result


@api_router.post("/products")
async def create_product(body: ProductIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    seq = await next_seq("product")
    prod = {"id": f"p{seq}", **body.model_dump()}
    await db.products.insert_one(prod)
    return strip_id(prod)


@api_router.put("/products/{product_id}")
async def update_product(product_id: str, body: ProductIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    res = await db.products.update_one({"id": product_id}, {"$set": body.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    p = await db.products.find_one({"id": product_id})
    return strip_id(p)


@api_router.put("/products/{product_id}/active")
async def set_product_active(product_id: str, body: ActiveIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    res = await db.products.update_one({"id": product_id}, {"$set": {"active": body.active}})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Produkt nicht gefunden")
    return {"ok": True, "active": body.active}


# --------------------------------------------------------------------------
# Routes: Image upload / serving (Emergent Object Storage)
# --------------------------------------------------------------------------
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/heic", "image/heif"}


@api_router.post("/upload")
async def upload_image(user: Annotated[dict, Depends(require_roles("admin"))], file: UploadFile = File(...)):
    content_type = file.content_type or "application/octet-stream"
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Nur Bilddateien sind erlaubt")
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Bild ist zu groß (max. 8 MB)")
    ext = (file.filename or "img.jpg").rsplit(".", 1)[-1].lower()
    path = f"{APP_NAME}/uploads/{user['id']}/{uuid.uuid4().hex}.{ext}"
    try:
        result = await run_in_threadpool(put_object, path, data, content_type)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else 500
        if code == 402:
            raise HTTPException(status_code=402, detail="Speicher-Kontingent aufgebraucht")
        raise HTTPException(status_code=502, detail="Upload fehlgeschlagen")
    stored = result["path"]
    await db.uploads.insert_one({
        "storagePath": stored,
        "ownerId": user["id"],
        "contentType": content_type,
        "createdAt": datetime.now(timezone.utc).isoformat(),
    })
    return {"url": f"/api/files/{stored}", "path": stored}


@api_router.get("/files/{path:path}")
async def serve_file(path: str):
    doc = await db.uploads.find_one({"storagePath": path})
    if not doc:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    try:
        content, content_type = await run_in_threadpool(get_object, path)
    except requests.HTTPError:
        raise HTTPException(status_code=404, detail="Datei nicht gefunden")
    return Response(content=content, media_type=content_type, headers={"Cache-Control": "public, max-age=86400"})


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


# --------------------------------------------------------------------------
# Routes: Companies / Customers
# --------------------------------------------------------------------------
@api_router.get("/companies")
async def get_companies(user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
    companies = await db.companies.find({"id": {"$in": ids}}).to_list(1000)
    companies = [strip_id(c) for c in companies]
    now = datetime.now(timezone.utc)
    for c in companies:
        last = await db.orders.find({"companyId": c["id"]}).sort("createdAt", -1).to_list(1)
        overdue = False
        days_since = None
        if last:
            last_dt = datetime.fromisoformat(last[0]["createdAt"]).replace(tzinfo=timezone.utc)
            days_since = (now - last_dt).days
            overdue = days_since > c.get("orderCycleDays", 30)
        else:
            overdue = True
        c["overdue"] = overdue
        c["daysSinceLastOrder"] = days_since
    return companies


@api_router.get("/companies/{company_id}")
async def get_company(company_id: str, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    c = await db.companies.find_one({"id": company_id})
    if not c:
        raise HTTPException(status_code=404, detail="Kunde nicht gefunden")
    return strip_id(c)


@api_router.get("/companies/{company_id}/prices")
async def get_company_prices(company_id: str, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if company_id not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    prices = await db.customer_prices.find({"companyId": company_id}).to_list(1000)
    return [strip_id(p) for p in prices]


# --------------------------------------------------------------------------
# Routes: Offers
# --------------------------------------------------------------------------
@api_router.get("/offers")
async def get_offers(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    offers = await db.offers.find({"companyId": {"$in": ids}}).sort("createdAt", -1).to_list(1000)
    return [strip_id(o) for o in offers]


@api_router.post("/offers")
async def create_offer(body: OfferCreate, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    ids = await visible_company_ids(user)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    needs_approval = False
    for it in body.items:
        prod = await db.products.find_one({"id": it.productId})
        if not prod:
            raise HTTPException(status_code=400, detail="Produkt unbekannt")
        if it.price < prod["absoluteFloor"]:
            raise HTTPException(status_code=400, detail="Preis unter absoluter Grenze – nicht zulässig")
        if it.price < prod["salesFloor"]:
            needs_approval = True
    now = datetime.now(timezone.utc)
    seq = await next_seq("offer")
    offer_no = f"A-{now.year}-{seq:04d}"
    offer = {
        "id": offer_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Freigabe nötig" if needs_approval else "Freigegeben",
        "items": [it.model_dump() for it in body.items],
        "reason": body.reason or ("Preis unter Vertriebslimit" if needs_approval else ""),
        "termMonths": body.termMonths,
        "createdAt": now.isoformat(),
    }
    await db.offers.insert_one(offer)
    return strip_id(offer)


@api_router.post("/offers/{offer_id}/approve")
async def approve_offer(offer_id: str, body: DecisionIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    o = await db.offers.find_one({"id": offer_id})
    if not o:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await db.offers.update_one({"id": offer_id}, {"$set": {"status": "Freigegeben", "decisionNote": body.note}})
    try:
        email, cname = await _company_recipient(o["companyId"])
        if email:
            rows = await _items_html(o["items"])
            total = sum(it["price"] * it["qty"] for it in o["items"])
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
            html = _email_shell("Angebot freigegeben", "Gute Neuigkeiten!", inner)
            await send_email(to=email, subject=f"Angebot {o['id']} freigegeben", html=html)
    except Exception as e:
        logger.warning(f"E-Mail (Angebot freigegeben) fehlgeschlagen: {e}")
    return {"ok": True, "status": "Freigegeben"}


@api_router.post("/offers/{offer_id}/accept")
async def accept_offer(offer_id: str, user: Annotated[dict, Depends(current_user)]):
    o = await db.offers.find_one({"id": offer_id})
    if not o:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o["status"] != "Freigegeben":
        raise HTTPException(status_code=400, detail="Nur freigegebene Angebote können angenommen werden.")
    if o.get("orderId"):
        raise HTTPException(status_code=400, detail="Angebot wurde bereits in eine Bestellung umgewandelt.")
    now = datetime.now(timezone.utc)
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": o["companyId"],
        "createdBy": user["id"],
        "status": "Neu",
        "items": o["items"],
        "fromOffer": offer_id,
        "createdAt": now.isoformat(),
    }
    await db.orders.insert_one(order)
    await db.offers.update_one({"id": offer_id}, {"$set": {"status": "Angenommen", "orderId": order_no}})
    return strip_id(order)


@api_router.post("/offers/{offer_id}/reject")
async def reject_offer(offer_id: str, body: DecisionIn, user: Annotated[dict, Depends(require_roles("admin"))]):
    o = await db.offers.find_one({"id": offer_id})
    if not o:
        raise HTTPException(status_code=404, detail="Angebot nicht gefunden")
    await db.offers.update_one({"id": offer_id}, {"$set": {"status": "Abgelehnt", "decisionNote": body.note}})
    return {"ok": True, "status": "Abgelehnt"}


# --------------------------------------------------------------------------
# Routes: Orders
# --------------------------------------------------------------------------
@api_router.get("/orders")
async def get_orders(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    orders = await db.orders.find({"companyId": {"$in": ids}}).sort("createdAt", -1).to_list(2000)
    return [strip_id(o) for o in orders]


@api_router.post("/orders")
async def create_order(body: OrderCreate, user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    if body.companyId not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    now = datetime.now(timezone.utc)
    seq = await next_seq("order")
    order_no = f"B-{now.year}-{seq:05d}"
    order = {
        "id": order_no,
        "companyId": body.companyId,
        "createdBy": user["id"],
        "status": "Neu",
        "items": [it.model_dump() for it in body.items],
        "createdAt": now.isoformat(),
    }
    await db.orders.insert_one(order)
    return strip_id(order)


@api_router.get("/orders/{order_id}")
async def get_order(order_id: str, user: Annotated[dict, Depends(current_user)]):
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    return strip_id(o)


@api_router.put("/orders/{order_id}/status")
async def set_order_status(order_id: str, body: OrderStatusIn, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    if body.status not in ORDER_STATUS_FLOW:
        raise HTTPException(status_code=400, detail="Ungültiger Status")
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    update = {"status": body.status}
    # When shipped, attach tracking + estimated delivery (once)
    if body.status == "Versendet" and not o.get("trackingNumber"):
        now = datetime.now(timezone.utc)
        eta = now + timedelta(days=2)
        update["trackingNumber"] = f"SS{now.strftime('%y%m%d')}{o['id'].split('-')[-1]}"
        update["shippedAt"] = now.isoformat()
        update["estimatedDelivery"] = eta.date().isoformat()
    await db.orders.update_one({"id": order_id}, {"$set": update})
    if body.status == "Versendet":
        try:
            email, cname = await _company_recipient(o["companyId"])
            if email:
                tracking = update.get("trackingNumber") or o.get("trackingNumber") or "-"
                eta = update.get("estimatedDelivery") or o.get("estimatedDelivery")
                eta_row = (
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Voraussichtliche Lieferung</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(eta))}</td></tr>"
                    if eta else ""
                )
                inner = (
                    f"<p style='margin:0 0 12px;color:#3A4256;font-size:15px'>Hallo {escape(cname)},<br>"
                    f"Ihre Bestellung <strong>{escape(o['id'])}</strong> wurde versendet und ist unterwegs.</p>"
                    "<table role='presentation' width='100%' cellpadding='0' cellspacing='0' style='font-size:14px;color:#3A4256'>"
                    f"<tr><td style='padding:6px 8px;color:#8A90A2'>Sendungsnummer</td>"
                    f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{escape(str(tracking))}</td></tr>"
                    f"{eta_row}"
                    "</table>"
                )
                html = _email_shell("Bestellung versendet", "Ihre Lieferung ist auf dem Weg.", inner)
                await send_email(to=email, subject=f"Bestellung {o['id']} ist unterwegs", html=html)
        except Exception as e:
            logger.warning(f"E-Mail (Bestellung versendet) fehlgeschlagen: {e}")
    return {"ok": True, "status": body.status}


@api_router.put("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user: Annotated[dict, Depends(current_user)]):
    o = await db.orders.find_one({"id": order_id})
    if not o:
        raise HTTPException(status_code=404, detail="Bestellung nicht gefunden")
    ids = await visible_company_ids(user)
    if o["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    if o["status"] != "Neu":
        raise HTTPException(
            status_code=400,
            detail="Bestellung wird bereits bearbeitet und kann nicht mehr storniert werden.",
        )
    await db.orders.update_one(
        {"id": order_id},
        {"$set": {"status": "Storniert", "cancelledAt": datetime.now(timezone.utc).isoformat(), "cancelledBy": user["id"]}},
    )
    return {"ok": True, "status": "Storniert"}


# --------------------------------------------------------------------------
# Routes: Contracts & Invoices
# --------------------------------------------------------------------------
@api_router.get("/contracts")
async def get_contracts(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    rows = await db.contracts.find({"companyId": {"$in": ids}}).to_list(1000)
    return [strip_id(r) for r in rows]


@api_router.get("/invoices")
async def get_invoices(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    rows = await db.invoices.find({"companyId": {"$in": ids}}).sort("date", -1).to_list(1000)
    return [strip_id(r) for r in rows]


@api_router.put("/invoices/{invoice_id}/pay")
async def mark_invoice_paid(invoice_id: str, user: Annotated[dict, Depends(require_roles("admin", "sales"))]):
    inv = await db.invoices.find_one({"id": invoice_id})
    if not inv:
        raise HTTPException(status_code=404, detail="Rechnung nicht gefunden")
    ids = await visible_company_ids(user)
    if inv["companyId"] not in ids:
        raise HTTPException(status_code=403, detail="Keine Berechtigung")
    await db.invoices.update_one(
        {"id": invoice_id},
        {"$set": {"status": "Bezahlt", "paidAt": datetime.now(timezone.utc).isoformat()}},
    )
    return {"ok": True, "status": "Bezahlt"}


# --------------------------------------------------------------------------
# Routes: Dashboard
# --------------------------------------------------------------------------
@api_router.get("/dashboard")
async def dashboard(user: Annotated[dict, Depends(current_user)]):
    ids = await visible_company_ids(user)
    companies = await db.companies.find({"id": {"$in": ids}}).to_list(1000)
    orders = await db.orders.find({"companyId": {"$in": ids}}).to_list(5000)
    offers = await db.offers.find({"companyId": {"$in": ids}}).to_list(1000)
    invoices = await db.invoices.find({"companyId": {"$in": ids}}).to_list(1000)

    def order_total(o):
        return sum(i["price"] * i["qty"] for i in o["items"])

    now = datetime.now(timezone.utc)
    this_month = [o for o in orders if datetime.fromisoformat(o["createdAt"]).month == now.month
                  and datetime.fromisoformat(o["createdAt"]).year == now.year]
    revenue_month = sum(order_total(o) for o in this_month)
    total_kg = sum(c.get("monthlyKg", 0) for c in companies)
    open_offers = [o for o in offers if o["status"] in ("Freigabe nötig", "Freigegeben", "Versendet")]
    approvals = [o for o in offers if o["status"] == "Freigabe nötig"]
    open_invoices = [i for i in invoices if i["status"] != "Bezahlt"]
    open_invoices_sum = sum(i["amount"] for i in open_invoices)

    followups = []
    for c in companies:
        last = await db.orders.find({"companyId": c["id"]}).sort("createdAt", -1).to_list(1)
        if last:
            last_dt = datetime.fromisoformat(last[0]["createdAt"]).replace(tzinfo=timezone.utc)
            days = (now - last_dt).days
            if days > c.get("orderCycleDays", 30):
                followups.append({"companyId": c["id"], "name": c["name"], "days": days})
        else:
            followups.append({"companyId": c["id"], "name": c["name"], "days": None})

    if user["role"] == "customer":
        c = companies[0] if companies else None
        contract = await db.contracts.find_one({"companyId": user.get("companyId")})
        return {
            "role": "customer",
            "companyName": c["name"] if c else "",
            "monthlyKg": c.get("monthlyKg", 0) if c else 0,
            "minQtyMonth": contract.get("minQtyMonth", 0) if contract else 0,
            "openInvoices": open_invoices_sum,
            "openInvoicesCount": len(open_invoices),
            "contract": strip_id(contract) if contract else None,
            "ordersCount": len(orders),
        }

    return {
        "role": user["role"],
        "revenueMonth": revenue_month,
        "activeCustomers": len(companies),
        "totalKg": total_kg,
        "openOffers": len(open_offers),
        "pendingApprovals": len(approvals),
        "openInvoices": open_invoices_sum,
        "ordersCount": len(orders),
        "followups": sorted(followups, key=lambda x: -(x["days"] or 999)),
    }


# --------------------------------------------------------------------------
# Routes: Analytics
# --------------------------------------------------------------------------
@api_router.get("/analytics")
async def analytics(user: Annotated[dict, Depends(require_roles("admin", "sales"))], months: int = 6):
    ids = await visible_company_ids(user)
    orders = await db.orders.find({"companyId": {"$in": ids}}).to_list(10000)
    products = await db.products.find().to_list(1000)
    cost_map = {p["id"]: p["cost"] for p in products}

    now = datetime.now(timezone.utc)
    buckets = {}
    labels = []
    for i in range(months - 1, -1, -1):
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        key = f"{y}-{m:02d}"
        buckets[key] = {"revenue": 0.0, "kg": 0.0, "margin": 0.0}
        labels.append(key)

    for o in orders:
        dt = datetime.fromisoformat(o["createdAt"])
        key = f"{dt.year}-{dt.month:02d}"
        if key in buckets:
            for it in o["items"]:
                rev = it["price"] * it["qty"]
                buckets[key]["revenue"] += rev
                buckets[key]["kg"] += it["qty"]
                buckets[key]["margin"] += (it["price"] - cost_map.get(it["productId"], 0)) * it["qty"]

    month_names = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
    is_admin = user["role"] == "admin"
    series = []
    for key in labels:
        y, m = key.split("-")
        row = {
            "label": month_names[int(m) - 1],
            "revenue": round(buckets[key]["revenue"], 2),
            "kg": round(buckets[key]["kg"], 1),
        }
        # DB / Deckungsbeitrag is internal — only admins get margin figures
        if is_admin:
            row["margin"] = round(buckets[key]["margin"], 2)
        series.append(row)

    total_rev = sum(s["revenue"] for s in series)
    result = {
        "series": series,
        "totalRevenue": round(total_rev, 2),
        "showMargin": is_admin,
    }
    if is_admin:
        total_margin = sum(buckets[key]["margin"] for key in labels)
        margin_pct = (total_margin / total_rev * 100) if total_rev else 0
        result["totalMargin"] = round(total_margin, 2)
        result["marginPct"] = round(margin_pct, 1)
    return result


app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Seeding
# --------------------------------------------------------------------------
async def seed():
    await db.users.create_index("email", unique=True, name="uniq_email")
    await db.password_resets.create_index("expiresAt", expireAfterSeconds=0, name="ttl_reset")

    seed_users = [
        {"id": "u-admin", "name": "Sergio (Admin)", "email": "admin@ss-coffee.de", "role": "admin",
         "pw": os.environ["SEED_ADMIN_PASSWORD"]},
        {"id": "u-sales", "name": "Marco Vertrieb", "email": "vertrieb@ss-coffee.de", "role": "sales",
         "salesRepId": "u-sales", "pw": os.environ["SEED_SALES_PASSWORD"]},
        {"id": "u-customer", "name": "Ristorante Roma", "email": "kunde@ss-coffee.de", "role": "customer",
         "companyId": "c1", "pw": os.environ["SEED_CUSTOMER_PASSWORD"]},
    ]
    for u in seed_users:
        pw = u.pop("pw")
        doc = {**u, "hashed_password": hash_pw(pw), "createdAt": datetime.now(timezone.utc).isoformat()}
        await db.users.update_one({"email": u["email"]}, {"$setOnInsert": doc}, upsert=True)

    if await db.companies.count_documents({}) == 0:
        companies = [
            {"id": "c1", "name": "Ristorante Roma GmbH", "city": "Nürnberg", "email": "info@roma.de",
             "phone": "0911 123456", "vatId": "DE123456789", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 48, "orderCycleDays": 14},
            {"id": "c2", "name": "Bar Milano GmbH", "city": "Fürth", "email": "ciao@milano.de",
             "phone": "0911 987654", "vatId": "DE987654321", "assignedSalesRepId": "u-admin", "active": True,
             "monthlyKg": 72, "orderCycleDays": 21},
            {"id": "c3", "name": "Eis Venezia", "city": "Ingolstadt", "email": "info@venezia.de",
             "phone": "0841 555123", "vatId": "DE555444333", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 110, "orderCycleDays": 30},
            {"id": "c4", "name": "Caffè Torino", "city": "Erlangen", "email": "hallo@torino.de",
             "phone": "09131 44556", "vatId": "DE444555666", "assignedSalesRepId": "u-sales", "active": True,
             "monthlyKg": 35, "orderCycleDays": 14},
        ]
        await db.companies.insert_many(companies)

    if await db.products.count_documents({}) == 0:
        products = [
            {"id": "p1", "name": "Espresso Bar", "brand": "Gambilongo", "unit": "kg", "standardPrice": 16.90,
             "salesFloor": 15.90, "absoluteFloor": 14.90, "cost": 11.50, "active": True,
             "discountTiers": [{"minQty": 50, "price": 16.20}, {"minQty": 100, "price": 15.50}]},
            {"id": "p2", "name": "Strong", "brand": "Caffè Aiello", "unit": "kg", "standardPrice": 18.90,
             "salesFloor": 17.90, "absoluteFloor": 16.90, "cost": 15.35, "active": True,
             "discountTiers": [{"minQty": 50, "price": 18.20}, {"minQty": 100, "price": 17.50}]},
            {"id": "p3", "name": "Crema Mousse", "brand": "S&S", "unit": "Stk.", "standardPrice": 12.90,
             "salesFloor": 11.90, "absoluteFloor": 10.90, "cost": 7.40, "active": True},
            {"id": "p4", "name": "Decaf Gold", "brand": "Caffè Aiello", "unit": "kg", "standardPrice": 21.50,
             "salesFloor": 19.90, "absoluteFloor": 18.50, "cost": 14.20, "active": True},
        ]
        await db.products.insert_many(products)

    if await db.customer_prices.count_documents({}) == 0:
        prices = [
            {"companyId": "c1", "productId": "p1", "price": 15.90},
            {"companyId": "c1", "productId": "p2", "price": 17.90},
            {"companyId": "c2", "productId": "p2", "price": 18.20},
            {"companyId": "c3", "productId": "p1", "price": 15.50},
            {"companyId": "c4", "productId": "p1", "price": 16.20},
        ]
        await db.customer_prices.insert_many(prices)

    if await db.offers.count_documents({}) == 0:
        await db.offers.insert_many([
            {"id": "A-2026-0187", "companyId": "c1", "createdBy": "u-sales", "status": "Freigabe nötig",
             "items": [{"productId": "p1", "qty": 80, "price": 15.50}], "reason": "Strategischer Kunde, 80 kg/Monat",
             "termMonths": 48, "createdAt": "2026-06-08T09:00:00"},
            {"id": "A-2026-0181", "companyId": "c3", "createdBy": "u-sales", "status": "Freigegeben",
             "items": [{"productId": "p1", "qty": 110, "price": 15.50}], "reason": "",
             "termMonths": 36, "createdAt": "2026-05-20T09:00:00"},
        ])

    if await db.orders.count_documents({}) == 0:
        now = datetime.now(timezone.utc)
        cust_price = {("c1", "p1"): 15.90, ("c1", "p2"): 17.90, ("c2", "p2"): 18.20,
                      ("c3", "p1"): 15.50, ("c4", "p1"): 16.20}
        std = {"p1": 16.90, "p2": 18.90, "p3": 12.90, "p4": 21.50}
        base = {"c1": ("p1", 18), "c2": ("p2", 24), "c3": ("p1", 38), "c4": ("p1", 12)}
        orders = []
        counter = 1
        for cid, (pid, qty) in base.items():
            for i in range(6, 0, -1):
                dt = now - timedelta(days=i * 28 + (counter % 5))
                price = cust_price.get((cid, pid), std[pid])
                q = qty + (i % 3) * 3
                orders.append({
                    "id": f"B-2026-{counter:05d}", "companyId": cid, "createdBy": "u-sales", "status": "Abgeschlossen",
                    "items": [{"productId": pid, "qty": q, "price": price}],
                    "createdAt": dt.isoformat(),
                })
                counter += 1
        orders.append({
            "id": "B-2026-00987", "companyId": "c1", "createdBy": "u-customer", "status": "Neu",
            "items": [{"productId": "p1", "qty": 18, "price": 15.90}],
            "createdAt": (now - timedelta(days=2)).isoformat(),
        })
        await db.orders.insert_many(orders)

    if await db.contracts.count_documents({}) == 0:
        await db.contracts.insert_many([
            {"id": "S&S-2026-0187", "companyId": "c1", "productId": "p1", "start": "2026-01-01", "termMonths": 48,
             "minQtyMonth": 36, "price": 15.90, "machine": "BFC Lira", "machineRate": 129, "serviceRate": 24.90},
            {"id": "S&S-2026-0142", "companyId": "c3", "productId": "p1", "start": "2025-09-01", "termMonths": 36,
             "minQtyMonth": 90, "price": 15.50, "machine": "La Cimbali M26", "machineRate": 159, "serviceRate": 29.90},
        ])

    if await db.invoices.count_documents({}) == 0:
        await db.invoices.insert_many([
            {"id": "RE-2026-0987", "companyId": "c1", "date": "2026-06-03", "amount": 683.20, "status": "Bezahlt"},
            {"id": "RE-2026-0921", "companyId": "c1", "date": "2026-05-15", "amount": 1143.00, "status": "Offen"},
            {"id": "RE-2026-0850", "companyId": "c3", "date": "2026-05-28", "amount": 1705.00, "status": "Überfällig"},
            {"id": "RE-2026-0870", "companyId": "c2", "date": "2026-06-01", "amount": 872.40, "status": "Offen"},
        ])

    # unique indexes + counter starting points (above seeded IDs)
    await db.offers.create_index("id", unique=True, name="uniq_offer_id")
    await db.orders.create_index("id", unique=True, name="uniq_order_id")
    await db.products.create_index("id", unique=True, name="uniq_product_id")
    if await db.counters.find_one({"_id": "offer"}) is None:
        await db.counters.insert_one({"_id": "offer", "seq": 200})
    if await db.counters.find_one({"_id": "order"}) is None:
        await db.counters.insert_one({"_id": "order", "seq": 1000})
    if await db.counters.find_one({"_id": "product"}) is None:
        # start above the highest seeded product number so new ids never collide
        prod_ids = [int(p["id"][1:]) for p in await db.products.find().to_list(1000) if p.get("id", "").startswith("p") and p["id"][1:].isdigit()]
        await db.counters.insert_one({"_id": "product", "seq": max(prod_ids) if prod_ids else 0})

    logger.info("Seeding complete")


@app.on_event("startup")
async def on_startup():
    await db.command("ping")
    await seed()
    try:
        await run_in_threadpool(init_storage)
        logger.info("Object storage initialised")
    except Exception as e:
        logger.warning(f"Object storage init failed (uploads may be unavailable): {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
