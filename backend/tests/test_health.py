from __future__ import annotations

import asyncio
import json
import os


os.environ["MONGO_URL"] = "mongodb://127.0.0.1:1"
os.environ["DB_NAME"] = "ordo_test_health_import"
os.environ["JWT_SECRET"] = "test-only-health-secret-at-least-32-bytes"
os.environ["APP_ENV"] = "test"

from app import main as app_main


class HealthyDatabase:
    async def command(self, name: str):
        assert name == "ping"
        return {"ok": 1}


class UnavailableDatabase:
    async def command(self, _name: str):
        raise RuntimeError("mongodb://secret-host.example.test must not leak")


def run(coroutine):
    return asyncio.run(coroutine)


def test_health_reports_safe_runtime_and_database_status(monkeypatch):
    monkeypatch.setattr(app_main, "db", HealthyDatabase())
    monkeypatch.setenv("APP_ENV", "  Staging  ")
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc123")

    result = run(app_main.health())

    assert result == {
        "service": "ordo-api",
        "environment": "staging",
        "version": "abc123",
        "status": "ok",
        "database": "connected",
    }
    assert any(
        getattr(route, "path", None) == "/api/health"
        for route in app_main.app.routes
    )


def test_health_fails_closed_without_leaking_database_details(monkeypatch):
    monkeypatch.setattr(app_main, "db", UnavailableDatabase())
    monkeypatch.setenv("APP_ENV", "staging")
    monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)
    monkeypatch.delenv("APP_VERSION", raising=False)

    response = run(app_main.health())
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload == {
        "service": "ordo-api",
        "environment": "staging",
        "version": "unknown",
        "status": "unhealthy",
        "database": "unavailable",
    }
    assert "secret-host" not in response.body.decode()


def test_liveness_does_not_depend_on_database(monkeypatch):
    monkeypatch.setattr(app_main, "db", UnavailableDatabase())
    monkeypatch.setenv("APP_ENV", "staging")

    result = run(app_main.liveness())

    assert result["status"] == "alive"
    assert result["environment"] == "staging"


def test_readiness_returns_503_for_failed_critical_capability(monkeypatch):
    async def not_ready(_database):
        return {
            "status": "not_ready",
            "ready": False,
            "capabilities": {"database": {"status": "unavailable", "message": "Datenbank nicht erreichbar"}},
        }

    monkeypatch.setattr(app_main, "capability_snapshot", not_ready)
    monkeypatch.setenv("APP_ENV", "staging")

    response = run(app_main.readiness())
    payload = json.loads(response.body)

    assert response.status_code == 503
    assert payload["ready"] is False
    assert payload["capabilities"]["database"]["status"] == "unavailable"
