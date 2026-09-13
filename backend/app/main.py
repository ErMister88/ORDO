"""App assembly: mount routers, CORS, startup/shutdown."""
from starlette.middleware.cors import CORSMiddleware
from starlette.concurrency import run_in_threadpool

from .core import app, api_router, db, client, logger
from .storage import init_storage
from .seed import seed

# Import routers so their @api_router routes register before we include it.
from .routers import (  # noqa: F401,E402
    auth, users, products, pricing, companies, offers, orders, invoices, dashboard, analytics,
    subscriptions, billing, payments, audit, shop,
)

app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
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
