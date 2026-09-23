"""App assembly: mount routers, CORS, startup/shutdown."""
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from .core import app, api_router, db, client, logger
from .database_setup import ensure_required_indexes
from .storage import init_storage

# Import routers so their @api_router routes register before we include it.
from .routers import (  # noqa: F401,E402
    auth, users, products, pricing, companies, offers, orders, invoices, dashboard, analytics,
    subscriptions, billing, payments, audit, shop, newsletter, push, machines,
)

import os

app.include_router(api_router)

# Bearer-token auth is used (no cookies), so credentials are not needed.
# Avoid the invalid wildcard-origin + credentials combination.
_cors_origins = os.getenv("CORS_ORIGINS", "*")
app.add_middleware(
    CORSMiddleware,
    allow_credentials=False,
    allow_origins=[o.strip() for o in _cors_origins.split(",")] if _cors_origins != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health", tags=["operations"])
async def health():
    environment = (os.getenv("APP_ENV") or "unknown").strip().lower() or "unknown"
    version = (
        os.getenv("RENDER_GIT_COMMIT")
        or os.getenv("APP_VERSION")
        or "unknown"
    ).strip() or "unknown"
    payload = {
        "service": "ordo-api",
        "environment": environment,
        "version": version,
    }
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
    await db.command("ping")
    await ensure_required_indexes(db)
    try:
        await run_in_threadpool(init_storage)
        logger.info("Object storage initialised")
    except Exception as e:
        logger.warning(f"Object storage init failed (uploads may be unavailable): {e}")


@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
