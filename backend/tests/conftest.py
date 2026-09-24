import os
import pytest
import requests
from dotenv import load_dotenv
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(BACKEND_DIR / ".env.test", override=True)

BASE_URL = os.getenv("EXPO_PUBLIC_BACKEND_URL", "http://127.0.0.1:8000").strip().rstrip("/")
RUN_LIVE_INTEGRATION = os.getenv("ORDO_RUN_LIVE_INTEGRATION", "").strip() == "1"
LIVE_INTEGRATION_FILES = {
    "test_ss_backend.py",
    *(f"test_iteration{number}.py" for number in range(2, 21)),
    "test_iteration19_security_fixes.py",
    "test_iteration20_machines_newsletter.py",
}


def pytest_collection_modifyitems(config, items):
    """Keep destructive legacy integration tests opt-in and off real staging."""

    if RUN_LIVE_INTEGRATION:
        return
    marker = pytest.mark.skip(
        reason=(
            "live integration harness disabled; copy backend/test.env.example to "
            "backend/.env.test and set ORDO_RUN_LIVE_INTEGRATION=1 for an isolated, "
            "disposable test database"
        )
    )
    for item in items:
        if Path(str(item.fspath)).name in LIVE_INTEGRATION_FILES:
            item.add_marker(pytest.mark.live_integration)
            item.add_marker(marker)


def pytest_sessionstart(session):
    """Refuse destructive integration runs unless the disposable target is explicit."""

    if not RUN_LIVE_INTEGRATION:
        return
    app_env = os.getenv("APP_ENV", "").strip().lower()
    database_name = os.getenv("DB_NAME", "").strip()
    confirmation = os.getenv("ORDO_TEST_TARGET_CONFIRMATION", "").strip()
    if app_env not in {"test", "testing"}:
        raise pytest.UsageError(
            "ORDO_RUN_LIVE_INTEGRATION requires APP_ENV=test or APP_ENV=testing"
        )
    if not database_name.startswith("ordo_test_"):
        raise pytest.UsageError(
            "ORDO_RUN_LIVE_INTEGRATION requires a DB_NAME beginning with ordo_test_"
        )
    expected_confirmation = f"{app_env}:{database_name}"
    if confirmation != expected_confirmation:
        raise pytest.UsageError(
            "ORDO_TEST_TARGET_CONFIRMATION must exactly match APP_ENV:DB_NAME"
        )
    for role in ("ADMIN", "SALES", "CUSTOMER"):
        _credentials(role)


@pytest.fixture(scope="session")
def base_url():
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


def _credentials(role: str) -> tuple[str, str]:
    email = os.getenv(f"ORDO_TEST_{role}_EMAIL", "").strip()
    password = os.getenv(f"ORDO_TEST_{role}_PASSWORD", "")
    if not email or not password:
        pytest.fail(
            f"ORDO_TEST_{role}_EMAIL and ORDO_TEST_{role}_PASSWORD are required "
            "for the live integration harness"
        )
    return email, password


@pytest.fixture(scope="session")
def admin_credentials():
    return _credentials("ADMIN")


@pytest.fixture(scope="session")
def sales_credentials():
    return _credentials("SALES")


@pytest.fixture(scope="session")
def customer_credentials():
    return _credentials("CUSTOMER")


@pytest.fixture(scope="session")
def admin_token(api_client, base_url, admin_credentials):
    r = _login(api_client, base_url, *admin_credentials)
    assert r.status_code == 200, f"admin login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def sales_token(api_client, base_url, sales_credentials):
    r = _login(api_client, base_url, *sales_credentials)
    assert r.status_code == 200, f"sales login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


@pytest.fixture(scope="session")
def customer_token(api_client, base_url, customer_credentials):
    r = _login(api_client, base_url, *customer_credentials)
    assert r.status_code == 200, f"customer login failed: {r.status_code} {r.text}"
    return r.json()["access_token"]


def hdr(tok):
    return {"Authorization": f"Bearer {tok}"}
