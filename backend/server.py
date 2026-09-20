"""Backend entry point — assembles the FastAPI app from the app package.

The heavy lifting lives in app/ (core, models, deps, emailer, storage
and app/routers/*). Uvicorn target stays `server:app`.
"""
from app.main import app  # noqa: F401
