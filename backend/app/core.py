"""Shared app objects: config, DB, security helpers, router."""
from fastapi import FastAPI, APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from dotenv import load_dotenv
from pymongo import ReturnDocument
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
import jwt
import bcrypt
import secrets
import string
import hashlib
from pathlib import Path
from typing import Literal
from datetime import datetime, timedelta, timezone

ROOT_DIR = Path(__file__).parent.parent
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
logger = logging.getLogger("ss")

Role = Literal["admin", "sales", "customer"]

ORDER_STATUS_FLOW = ["Neu", "Bestätigt", "Kommissioniert", "Versendet", "Abgeschlossen"]


# --- Password + token helpers -------------------------------------------------
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
    # High-entropy, human-friendly code (no ambiguous chars). ~ 32^8 combinations.
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    code = "".join(secrets.choice(alphabet) for _ in range(8))
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


def strip_id(doc: dict) -> dict:
    doc = dict(doc)
    doc.pop("_id", None)
    doc.pop("_demoSeed", None)
    doc.pop("_demoSeedFingerprint", None)
    doc.pop("tenantId", None)
    return doc


async def next_seq(name: str) -> int:
    doc = await db.counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]
