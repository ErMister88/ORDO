"""App assembly: mount routers, CORS, startup/shutdown."""
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .core import app, api_router, db, client, logger
from .storage import init_storage
from .seed import seed

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
