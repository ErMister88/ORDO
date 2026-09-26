"""App assembly: mount routers, CORS, startup/shutdown."""
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse

from .core import app, api_router, db, client, logger
from .database_setup import ensure_required_indexes
from .capabilities import capability_snapshot
from .observability import OperationalContextMiddleware, configure_structured_logging
from .production_config import assert_safe_startup_configuration
from .runtime_security import RuntimeSecurityMiddleware

# Import routers so their @api_router routes register before we include it.
from .routers import (  # noqa: F401,E402
    auth, users, products, pricing, companies, offers, orders, invoices, dashboard, analytics,
    subscriptions, billing, payments, audit, shop, newsletter, push, machines, crm,
    business_config, operations, stripe_webhooks, files, notifications,
)

import os

app.include_router(api_router)
configure_structured_logging()

# Bearer-token auth is used (no cookies), so credentials are not needed.
# Avoid the invalid wildcard-origin + credentials combination.
_cors_origins = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=[o.strip() for o in _cors_origins.split(",")] if _cors_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Error-ID"],
)
app.add_middleware(OperationalContextMiddleware, database=db)
app.add_middleware(RuntimeSecurityMiddleware, database=db)


def _runtime_payload():
    environment = (os.getenv("APP_ENV") or "unknown").strip().lower() or "unknown"
    version = (os.getenv("RENDER_GIT_COMMIT") or os.getenv("APP_VERSION") or "unknown").strip() or "unknown"
    return {"service": "ordo-api", "environment": environment, "version": version}


@app.get("/api/health/live", tags=["operations"])
async def liveness():
    return {**_runtime_payload(), "status": "alive"}


@app.get("/api/health/ready", tags=["operations"])
async def readiness():
    result = await capability_snapshot(db)
    payload = {**_runtime_payload(), **result}
    if not result["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@app.get("/api/health", tags=["operations"])
async def health():
    payload = _runtime_payload()
    try:
        await db.command("ping")
    except Exception:
        logger.warning("Health check failed: database unavailable")
        return JSONResponse(
            status_code=503,
            content={**payload, "status": "unhealthy", "database": "unavailable"},
        )
    return {**payload, "status": "ok", "database": "connected"}


@app.on_event("startup")
async def on_startup():
    assert_safe_startup_configuration()
    await db.command("ping")
    await ensure_required_indexes(db)


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
