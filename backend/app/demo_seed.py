"""Explicit, non-production demo data seeding.

This module has no startup hook and does not import the application's global
database client. Callers must provide the target database explicitly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from hmac import compare_digest
import json
from typing import Callable, Mapping

import bcrypt
from pymongo.errors import DuplicateKeyError


DEMO_SEED_VERSION = "ordo-demo-v1"
DEMO_FINGERPRINT_FIELD = "_demoSeedFingerprint"
PASSWORD_KEYS = ("admin", "sales", "customer")
PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}
KNOWN_ENVIRONMENTS = {
    "dev",
    "development",
    "test",
    "testing",
    "stage",
    "staging",
    *PRODUCTION_ENVIRONMENTS,
}


class DemoSeedError(RuntimeError):
    """Base class for expected demo-seed failures."""


class DemoSeedConfigurationError(DemoSeedError):
    pass


class DemoSeedConflictError(DemoSeedError):
    pass


def validate_demo_target(
    app_env: str,
    database_name: str,
    confirmation: str,
) -> tuple[str, str]:
    if not all(isinstance(value, str) for value in (app_env, database_name, confirmation)):
        raise DemoSeedConfigurationError(
            "APP_ENV, DB_NAME and target confirmation must be strings"
        )
    normalized_env = app_env.strip().lower()
    normalized_database = database_name.strip()
    if not normalized_env:
        raise DemoSeedConfigurationError("APP_ENV must be configured explicitly")
    if normalized_env not in KNOWN_ENVIRONMENTS:
        raise DemoSeedConfigurationError(
            f"Unknown APP_ENV {normalized_env!r}; refusing unsafe default"
        )
    if normalized_env in PRODUCTION_ENVIRONMENTS:
        raise DemoSeedConfigurationError("Demo seeding is permanently blocked in production")
    if not normalized_database:
        raise DemoSeedConfigurationError("DB_NAME must be configured explicitly")
    expected = f"{normalized_env}:{normalized_database}"
    if not compare_digest(confirmation.strip(), expected):
        raise DemoSeedConfigurationError(
            "Demo seed target confirmation does not match APP_ENV and DB_NAME"
        )
    return normalized_env, normalized_database


def hash_demo_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _demo_id(collection: str, identity: str) -> str:
    return f"{DEMO_SEED_VERSION}:{collection}:{identity}"


def _with_metadata(collection: str, identity: str, document: dict) -> dict:
    return {
        "_id": _demo_id(collection, identity),
        "_demoSeed": DEMO_SEED_VERSION,
        **document,
    }


def build_demo_manifest(now: datetime | None = None) -> dict[str, list[dict]]:
    """Build the deterministic business identities used by the demo database."""

    now = now or datetime.now(timezone.utc)
    created_at = now.isoformat()

    users = [
        _with_metadata("users", "u-admin", {
            "id": "u-admin", "name": "Sergio (Admin)", "email": "admin@ss-coffee.de",
            "role": "admin", "createdAt": created_at, "_passwordKey": "admin",
        }),
        _with_metadata("users", "u-sales", {
            "id": "u-sales", "name": "Marco Vertrieb", "email": "vertrieb@ss-coffee.de",
            "role": "sales", "salesRepId": "u-sales", "createdAt": created_at,
            "_passwordKey": "sales",
        }),
        _with_metadata("users", "u-customer", {
            "id": "u-customer", "name": "Ristorante Roma", "email": "kunde@ss-coffee.de",
            "role": "customer", "companyId": "c1", "createdAt": created_at,
            "_passwordKey": "customer",
        }),
    ]

    companies = [
        _with_metadata("companies", "c1", {
            "id": "c1", "name": "Ristorante Roma GmbH", "city": "Nürnberg",
            "email": "info@roma.de", "phone": "0911 123456", "vatId": "DE123456789",
            "assignedSalesRepId": "u-sales", "active": True, "monthlyKg": 48,
            "orderCycleDays": 14,
        }),
        _with_metadata("companies", "c2", {
            "id": "c2", "name": "Bar Milano GmbH", "city": "Fürth",
            "email": "ciao@milano.de", "phone": "0911 987654", "vatId": "DE987654321",
            "assignedSalesRepId": "u-admin", "active": True, "monthlyKg": 72,
            "orderCycleDays": 21,
        }),
        _with_metadata("companies", "c3", {
            "id": "c3", "name": "Eis Venezia", "city": "Ingolstadt",
            "email": "info@venezia.de", "phone": "0841 555123", "vatId": "DE555444333",
            "assignedSalesRepId": "u-sales", "active": True, "monthlyKg": 110,
            "orderCycleDays": 30,
        }),
        _with_metadata("companies", "c4", {
            "id": "c4", "name": "Caffè Torino", "city": "Erlangen",
            "email": "hallo@torino.de", "phone": "09131 44556", "vatId": "DE444555666",
            "assignedSalesRepId": "u-sales", "active": True, "monthlyKg": 35,
            "orderCycleDays": 14,
        }),
    ]

    products = [
        _with_metadata("products", "p1", {
            "id": "p1", "name": "Espresso Bar", "brand": "Gambilongo", "unit": "kg",
            "standardPrice": 16.90, "salesFloor": 15.90, "absoluteFloor": 14.90,
            "cost": 11.50, "active": True, "taxRate": 7, "stock": None,
            "discountTiers": [{"minQty": 50, "price": 16.20}, {"minQty": 100, "price": 15.50}],
        }),
        _with_metadata("products", "p2", {
            "id": "p2", "name": "Strong", "brand": "Caffè Aiello", "unit": "kg",
            "standardPrice": 18.90, "salesFloor": 17.90, "absoluteFloor": 16.90,
            "cost": 15.35, "active": True, "taxRate": 7, "stock": None,
            "discountTiers": [{"minQty": 50, "price": 18.20}, {"minQty": 100, "price": 17.50}],
        }),
        _with_metadata("products", "p3", {
            "id": "p3", "name": "Crema Mousse", "brand": "S&S", "unit": "Stk.",
            "standardPrice": 12.90, "salesFloor": 11.90, "absoluteFloor": 10.90,
            "cost": 7.40, "active": True, "taxRate": 7, "stock": None,
        }),
        _with_metadata("products", "p4", {
            "id": "p4", "name": "Decaf Gold", "brand": "Caffè Aiello", "unit": "kg",
            "standardPrice": 21.50, "salesFloor": 19.90, "absoluteFloor": 18.50,
            "cost": 14.20, "active": True, "taxRate": 7, "stock": None,
        }),
    ]

    customer_prices = [
        _with_metadata("customer_prices", "c1:p1", {"companyId": "c1", "productId": "p1", "price": 15.90}),
        _with_metadata("customer_prices", "c1:p2", {"companyId": "c1", "productId": "p2", "price": 17.90}),
        _with_metadata("customer_prices", "c2:p2", {"companyId": "c2", "productId": "p2", "price": 18.20}),
        _with_metadata("customer_prices", "c3:p1", {"companyId": "c3", "productId": "p1", "price": 15.50}),
        _with_metadata("customer_prices", "c4:p1", {"companyId": "c4", "productId": "p1", "price": 16.20}),
    ]

    offers = [
        _with_metadata("offers", "A-2026-0187", {
            "id": "A-2026-0187", "companyId": "c1", "createdBy": "u-sales",
            "status": "Freigabe nötig", "items": [{"productId": "p1", "qty": 80, "price": 15.50}],
            "reason": "Strategischer Kunde, 80 kg/Monat", "termMonths": 48,
            "createdAt": "2026-06-08T09:00:00",
        }),
        _with_metadata("offers", "A-2026-0181", {
            "id": "A-2026-0181", "companyId": "c3", "createdBy": "u-sales",
            "status": "Freigegeben", "items": [{"productId": "p1", "qty": 110, "price": 15.50}],
            "reason": "", "termMonths": 36, "createdAt": "2026-05-20T09:00:00",
        }),
    ]

    customer_price = {
        ("c1", "p1"): 15.90, ("c1", "p2"): 17.90, ("c2", "p2"): 18.20,
        ("c3", "p1"): 15.50, ("c4", "p1"): 16.20,
    }
    standard_price = {"p1": 16.90, "p2": 18.90, "p3": 12.90, "p4": 21.50}
    order_base = {"c1": ("p1", 18), "c2": ("p2", 24), "c3": ("p1", 38), "c4": ("p1", 12)}
    orders = []
    counter = 1
    for company_id, (product_id, quantity) in order_base.items():
        for month in range(6, 0, -1):
            order_id = f"B-2026-{counter:05d}"
            ordered_at = now - timedelta(days=month * 28 + (counter % 5))
            price = customer_price.get((company_id, product_id), standard_price[product_id])
            qty = quantity + (month % 3) * 3
            orders.append(_with_metadata("orders", order_id, {
                "id": order_id, "companyId": company_id, "createdBy": "u-sales",
                "status": "Abgeschlossen", "items": [{"productId": product_id, "qty": qty, "price": price}],
                "createdAt": ordered_at.isoformat(),
            }))
            counter += 1
    orders.append(_with_metadata("orders", "B-2026-00987", {
        "id": "B-2026-00987", "companyId": "c1", "createdBy": "u-customer",
        "status": "Neu", "items": [{"productId": "p1", "qty": 18, "price": 15.90}],
        "createdAt": (now - timedelta(days=2)).isoformat(),
    }))

    contracts = [
        _with_metadata("contracts", "S&S-2026-0187", {
            "id": "S&S-2026-0187", "companyId": "c1", "productId": "p1",
            "start": "2026-01-01", "termMonths": 48, "minQtyMonth": 36, "price": 15.90,
            "machine": "BFC Lira", "machineRate": 129, "serviceRate": 24.90,
        }),
        _with_metadata("contracts", "S&S-2026-0142", {
            "id": "S&S-2026-0142", "companyId": "c3", "productId": "p1",
            "start": "2025-09-01", "termMonths": 36, "minQtyMonth": 90, "price": 15.50,
            "machine": "La Cimbali M26", "machineRate": 159, "serviceRate": 29.90,
        }),
    ]

    invoices = [
        _with_metadata("invoices", "RE-2026-0987", {
            "id": "RE-2026-0987", "companyId": "c1", "date": "2026-06-03",
            "amount": 683.20, "status": "Bezahlt",
        }),
        _with_metadata("invoices", "RE-2026-0921", {
            "id": "RE-2026-0921", "companyId": "c1", "date": "2026-05-15",
            "amount": 1143.00, "status": "Offen",
        }),
        _with_metadata("invoices", "RE-2026-0850", {
            "id": "RE-2026-0850", "companyId": "c3", "date": "2026-05-28",
            "amount": 1705.00, "status": "Überfällig",
        }),
        _with_metadata("invoices", "RE-2026-0870", {
            "id": "RE-2026-0870", "companyId": "c2", "date": "2026-06-01",
            "amount": 872.40, "status": "Offen",
        }),
    ]

    machines = [
        _with_metadata("machines", "demo-linea-mini", {
            "id": "demo-linea-mini", "name": "Espressomaschine La Marzocco Linea Mini",
            "price": 5900.0, "taxRate": 19, "active": True,
            "description": "Zweikreiser-Siebträger für höchste Ansprüche. Ideal für Büros und Gastronomie.",
            "imageUrl": "", "createdAt": created_at,
        }),
        _with_metadata("machines", "demo-wmf-1500", {
            "id": "demo-wmf-1500", "name": "Vollautomat WMF 1500 S+",
            "price": 8900.0, "taxRate": 19, "active": True,
            "description": "Kaffeevollautomat für hohe Volumen, bis zu 250 Tassen/Tag.",
            "imageUrl": "", "createdAt": created_at,
        }),
        _with_metadata("machines", "demo-ecm-synchronika", {
            "id": "demo-ecm-synchronika", "name": "Siebträger ECM Synchronika",
            "price": 3200.0, "taxRate": 19, "active": True,
            "description": "Dualboiler-Siebträger, Edelstahl, perfekt für kleine Teams.",
            "imageUrl": "", "createdAt": created_at,
        }),
    ]

    counters = [
        {"_id": "offer", "_demoSeed": DEMO_SEED_VERSION, "seq": 200},
        {"_id": "order", "_demoSeed": DEMO_SEED_VERSION, "seq": 1000},
        {"_id": "product", "_demoSeed": DEMO_SEED_VERSION, "seq": 4},
        {"_id": "invoice", "_demoSeed": DEMO_SEED_VERSION, "seq": 1000},
    ]

    return {
        "users": users,
        "companies": companies,
        "products": products,
        "customer_prices": customer_prices,
        "offers": offers,
        "orders": orders,
        "contracts": contracts,
        "invoices": invoices,
        "machines": machines,
        "counters": counters,
    }


def _identity_query(collection: str, document: Mapping) -> dict:
    if collection == "users":
        return {"$or": [
            {"_id": document["_id"]},
            {"id": document["id"]},
            {"email": document["email"]},
        ]}
    if collection == "customer_prices":
        return {"$or": [
            {"_id": document["_id"]},
            {"companyId": document["companyId"], "productId": document["productId"]},
        ]}
    if collection == "counters":
        return {"_id": document["_id"]}
    return {"$or": [{"_id": document["_id"]}, {"id": document["id"]}]}


def _identity_matches(collection: str, existing: Mapping, expected: Mapping) -> bool:
    if collection == "users":
        return existing.get("id") == expected["id"] and existing.get("email") == expected["email"]
    if collection == "customer_prices":
        return (
            existing.get("companyId") == expected["companyId"]
            and existing.get("productId") == expected["productId"]
        )
    if collection == "counters":
        return existing.get("_id") == expected["_id"]
    return existing.get("id") == expected["id"]


def _document_fingerprint(document: Mapping) -> str:
    payload = dict(document)
    payload.pop(DEMO_FINGERPRINT_FIELD, None)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


async def _find_matches(database, collection: str, document: Mapping) -> list[dict]:
    return await database[collection].find(_identity_query(collection, document)).to_list(length=3)


def _assert_owned_match(collection: str, expected: Mapping, matches: list[Mapping]) -> bool:
    if not matches:
        return False
    if len(matches) != 1:
        raise DemoSeedConflictError(
            f"Demo seed conflict in {collection}: multiple documents use the same demo identity"
        )
    existing = matches[0]
    if existing.get("_demoSeed") != DEMO_SEED_VERSION:
        raise DemoSeedConflictError(
            f"Demo seed conflict in {collection}: an existing business document uses a demo identity"
        )
    if not _identity_matches(collection, existing, expected):
        raise DemoSeedConflictError(
            f"Demo seed conflict in {collection}: the reserved demo document has another identity"
        )
    stored_fingerprint = existing.get(DEMO_FINGERPRINT_FIELD)
    try:
        actual_fingerprint = _document_fingerprint(existing)
    except (TypeError, ValueError):
        actual_fingerprint = ""
    if (
        not isinstance(stored_fingerprint, str)
        or len(stored_fingerprint) != 64
        or not compare_digest(stored_fingerprint, actual_fingerprint)
    ):
        raise DemoSeedConflictError(
            f"Demo seed conflict in {collection}: a marked demo document is modified or incomplete"
        )
    return True


def _validate_passwords(passwords: Mapping[str, str]) -> None:
    missing = [key for key in PASSWORD_KEYS if not str(passwords.get(key, "")).strip()]
    if missing:
        raise DemoSeedConfigurationError(
            "Missing demo passwords: " + ", ".join(sorted(missing))
        )


async def seed_demo(
    database,
    *,
    app_env: str,
    target_confirmation: str,
    passwords: Mapping[str, str],
    password_hasher: Callable[[str], str] = hash_demo_password,
    now: datetime | None = None,
) -> dict:
    """Insert only missing demo documents and never overwrite existing data."""

    validate_demo_target(app_env, database.name, target_confirmation)
    _validate_passwords(passwords)
    manifest = build_demo_manifest(now)
    pending: list[tuple[str, dict]] = []
    unchanged = 0
    per_collection = {name: {"inserted": 0, "unchanged": 0} for name in manifest}

    # Complete the conflict check before the first write.
    for collection, documents in manifest.items():
        for expected in documents:
            matches = await _find_matches(database, collection, expected)
            if _assert_owned_match(collection, expected, matches):
                unchanged += 1
                per_collection[collection]["unchanged"] += 1
            else:
                pending.append((collection, expected))

    inserted = 0
    for collection, expected in pending:
        document = dict(expected)
        password_key = document.pop("_passwordKey", None)
        if password_key:
            document["hashed_password"] = password_hasher(passwords[password_key])
        document[DEMO_FINGERPRINT_FIELD] = _document_fingerprint(document)
        try:
            await database[collection].insert_one(document)
            matches = await _find_matches(database, collection, expected)
            _assert_owned_match(collection, expected, matches)
            inserted += 1
            per_collection[collection]["inserted"] += 1
        except DuplicateKeyError:
            # A concurrent identical seed run may have inserted the deterministic _id.
            matches = await _find_matches(database, collection, expected)
            if not _assert_owned_match(collection, expected, matches):
                raise
            unchanged += 1
            per_collection[collection]["unchanged"] += 1

    return {
        "seedVersion": DEMO_SEED_VERSION,
        "inserted": inserted,
        "unchanged": unchanged,
        "collections": per_collection,
    }
