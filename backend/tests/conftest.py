import os
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(Path(__file__).parent.parent / ".env")

BASE_URL = os.environ["EXPO_PUBLIC_BACKEND_URL"].rstrip("/") if os.environ.get("EXPO_PUBLIC_BACKEND_URL") else None
if not BASE_URL:
    # fallback to frontend .env
    fenv = Path("/app/frontend/.env")
    if fenv.exists():
        for line in fenv.read_text().splitlines():
            if line.startswith("EXPO_PUBLIC_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().strip('"').rstrip("/")


@pytest.fixture(scope="session")
def base_url():
    assert BASE_URL, "Missing EXPO_PUBLIC_BACKEND_URL"
    return BASE_URL


@pytest.fixture(scope="session")
def api_client():
    s = requests.Session()
    return s


def _login(session, base_url, email, password):
    r = session.post(f"{base_url}/api/auth/login",
                     data={"username": email, "password": password},
                     headers={"Content-Type": "application/x-www-form-urlencoded"},
                     timeout=30)
    return r


@pytest.fixture(scope="session")
def admin_token(api_client, base_url):
    r = _login(api_client, base_url, "admin@ss-coffee.de", "Admin#2026")
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def sales_token(api_client, base_url):
    r = _login(api_client, base_url, "vertrieb@ss-coffee.de", "Sales#2026")
    assert r.status_code == 200, f"sales login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def customer_token(api_client, base_url):
    r = _login(api_client, base_url, "kunde@ss-coffee.de", "Kunde#2026")
    assert r.status_code == 200, f"customer login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}
