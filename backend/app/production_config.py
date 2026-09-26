"""Secret-safe production configuration validation."""

from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlparse

from .tenancy import TenancyConfigurationError, TenancySettings


TRUE_VALUES = {"1", "true", "yes", "on"}
PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}
KNOWN_ENVIRONMENTS = {"dev", "development", "test", "testing", "stage", "staging", *PRODUCTION_ENVIRONMENTS}
PLACEHOLDER_MARKERS = ("replace", "change-me", "changeme", "example", "dummy", "test-only")
LOCAL_OR_RESERVED_HOSTS = {"localhost", "127.0.0.1", "::1"}


@dataclass(frozen=True, slots=True)
class ConfigurationCheck:
    name: str
    status: str
    message: str
    critical: bool = True

    def public(self) -> dict[str, object]:
        return {"name": self.name, "status": self.status, "message": self.message, "critical": self.critical}


def _value(environment: dict[str, str], name: str) -> str:
    return (environment.get(name) or "").strip()


def _enabled(environment: dict[str, str], name: str) -> bool:
    return _value(environment, name).lower() in TRUE_VALUES


def _secret_is_safe(value: str, minimum: int = 32) -> bool:
    lowered = value.lower()
    return len(value.encode("utf-8")) >= minimum and not any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _host_is_deployable(hostname: str | None) -> bool:
    if not hostname:
        return False
    lowered = hostname.lower().rstrip(".")
    return (
        lowered not in LOCAL_OR_RESERVED_HOSTS
        and not lowered.endswith((".localhost", ".invalid", ".example", ".test"))
    )


def _https_origin_is_safe(value: str) -> bool:
    parsed = urlparse(value)
    return bool(
        parsed.scheme == "https"
        and _host_is_deployable(parsed.hostname)
        and not parsed.username
        and not parsed.password
        and parsed.path in {"", "/"}
        and not parsed.params
        and not parsed.query
        and not parsed.fragment
    )


def validate_runtime_configuration(environment: dict[str, str] | None = None) -> dict[str, object]:
    env = dict(os.environ if environment is None else environment)
    app_env = _value(env, "APP_ENV").lower()
    production = app_env in PRODUCTION_ENVIRONMENTS
    deployed = production or app_env in {"stage", "staging"}
    checks: list[ConfigurationCheck] = []

    checks.append(ConfigurationCheck(
        "environment", "configured" if app_env in KNOWN_ENVIRONMENTS else "invalid",
        "APP_ENV ist explizit" if app_env else "APP_ENV fehlt",
    ))
    database_name = _value(env, "DB_NAME")
    mongo_url = _value(env, "MONGO_URL")
    db_safe = bool(database_name and mongo_url.startswith(("mongodb://", "mongodb+srv://")))
    parsed_mongo = urlparse(mongo_url)
    if production:
        db_safe = db_safe and _host_is_deployable(parsed_mongo.hostname)
    if production and mongo_url.startswith("mongodb://"):
        query = parsed_mongo.query.lower()
        db_safe = db_safe and ("tls=true" in query or "ssl=true" in query)
    if production and any(marker in database_name.lower() for marker in ("test", "demo", "staging", "development")):
        db_safe = False
    checks.append(ConfigurationCheck("database", "configured" if db_safe else "invalid", "Datenbankziel konfiguriert" if db_safe else "Datenbankziel fehlt oder ist für Production unsicher"))

    app_url = _value(env, "APP_URL")
    parsed_app = urlparse(app_url)
    app_url_safe = (
        _https_origin_is_safe(app_url)
        if deployed else bool(parsed_app.hostname and parsed_app.scheme in {"http", "https"})
    )
    checks.append(ConfigurationCheck("app_url", "configured" if app_url_safe else "invalid" if deployed else "not_configured", "APP_URL gültig" if app_url_safe else "APP_URL fehlt oder verwendet ein unsicheres Schema", critical=deployed))

    origins = [value.strip() for value in _value(env, "CORS_ORIGINS").split(",") if value.strip()]
    cors_safe = bool(origins) and not (deployed and "*" in origins)
    if deployed:
        cors_safe = cors_safe and all(_https_origin_is_safe(origin) for origin in origins)
    checks.append(ConfigurationCheck("cors", "configured" if cors_safe else "invalid" if deployed else "not_configured", "CORS ist explizit" if cors_safe else "CORS fehlt oder ist für die Zielumgebung unsicher", critical=deployed))

    jwt_value = _value(env, "JWT_SECRET")
    jwt_safe = _secret_is_safe(jwt_value) if deployed else bool(jwt_value)
    checks.append(ConfigurationCheck("auth_secret", "configured" if jwt_safe else "invalid", "Authentifizierungs-Secret konfiguriert" if jwt_safe else "Authentifizierungs-Secret fehlt oder ist unsicher"))

    try:
        TenancySettings.from_mapping(env)
        tenancy_safe = True
    except TenancyConfigurationError:
        tenancy_safe = False
    checks.append(ConfigurationCheck("tenancy", "configured" if tenancy_safe else "invalid", "Tenant-Konfiguration gültig" if tenancy_safe else "Tenant-Konfiguration ungültig"))

    payments_enabled = _enabled(env, "PAYMENTS_ENABLED")
    stripe_key = _value(env, "STRIPE_API_KEY")
    webhook = _value(env, "STRIPE_WEBHOOK_SECRET")
    stripe_prefix = "sk_live_" if production else "sk_test_" if deployed else "sk_"
    payment_ok = not payments_enabled or (
        stripe_key.startswith(stripe_prefix) and _secret_is_safe(stripe_key)
        and webhook.startswith("whsec_") and _secret_is_safe(webhook, minimum=16)
    )
    checks.append(ConfigurationCheck("payments", "configured" if payments_enabled and payment_ok else "not_configured" if not payments_enabled else "invalid", "Zahlungen konfiguriert" if payments_enabled and payment_ok else "Zahlungen deaktiviert" if not payments_enabled else "Zahlungskonfiguration unvollständig", critical=payments_enabled))

    storage_enabled = _enabled(env, "STORAGE_ENABLED")
    storage_endpoint = _value(env, "S3_ENDPOINT_URL")
    storage_bucket = _value(env, "S3_BUCKET")
    storage_ok = not storage_enabled or (
        _value(env, "STORAGE_BACKEND").lower() == "s3"
        and (not storage_endpoint or _https_origin_is_safe(storage_endpoint))
        and len(storage_bucket) >= 3
        and not any(marker in storage_bucket.lower() for marker in PLACEHOLDER_MARKERS)
        and bool(_value(env, "S3_REGION"))
        and _secret_is_safe(_value(env, "S3_ACCESS_KEY_ID"), minimum=8)
        and _secret_is_safe(_value(env, "S3_SECRET_ACCESS_KEY"), minimum=16)
    )
    checks.append(ConfigurationCheck("storage", "configured" if storage_enabled and storage_ok else "not_configured" if not storage_enabled else "invalid", "Object Storage konfiguriert" if storage_enabled and storage_ok else "Object Storage deaktiviert" if not storage_enabled else "Object Storage unvollständig", critical=storage_enabled))

    email_enabled = _enabled(env, "EMAIL_ENABLED")
    sender = _value(env, "SMTP_FROM_EMAIL")
    sender_host = sender.rsplit("@", 1)[-1] if "@" in sender else None
    email_ok = not email_enabled or (
        _value(env, "EMAIL_BACKEND").lower() == "smtp"
        and _host_is_deployable(_value(env, "SMTP_HOST"))
        and _host_is_deployable(sender_host)
        and bool(_value(env, "SMTP_USERNAME")) == bool(_value(env, "SMTP_PASSWORD"))
        and (
            not _value(env, "SMTP_USERNAME")
            or (
                not any(marker in _value(env, "SMTP_USERNAME").lower() for marker in PLACEHOLDER_MARKERS)
                and _secret_is_safe(_value(env, "SMTP_PASSWORD"), minimum=8)
            )
        )
    )
    checks.append(ConfigurationCheck("email", "configured" if email_enabled and email_ok else "not_configured" if not email_enabled else "invalid", "E-Mail konfiguriert" if email_enabled and email_ok else "E-Mail deaktiviert" if not email_enabled else "E-Mail-Konfiguration unvollständig", critical=email_enabled))

    jobs_enabled = _enabled(env, "BACKGROUND_JOBS_ENABLED")
    worker_service = _enabled(env, "WORKER_SERVICE_ENABLED")
    worker_ok = not jobs_enabled or worker_service
    checks.append(ConfigurationCheck("worker", "configured" if jobs_enabled and worker_ok else "not_configured" if not jobs_enabled else "invalid", "Worker-Betrieb konfiguriert" if jobs_enabled and worker_ok else "Background Jobs deaktiviert" if not jobs_enabled else "Background Jobs aktiviert, aber Worker-Service nicht bestätigt", critical=jobs_enabled))

    demo_safe = not production or not any(_enabled(env, name) for name in ("DEMO_SEED_ENABLED", "ALLOW_DEMO_SEED", "TEST_MODE"))
    checks.append(ConfigurationCheck("demo_isolation", "configured" if demo_safe else "invalid", "Demo-/Testpfade in Production gesperrt" if demo_safe else "Demo-/Testmodus ist in Production aktiviert"))

    ready = all(check.status != "invalid" for check in checks if check.critical)
    return {"status": "READY" if ready else "NOT_READY", "ready": ready, "checks": [check.public() for check in checks]}


def assert_safe_startup_configuration(environment: dict[str, str] | None = None) -> None:
    env = dict(os.environ if environment is None else environment)
    app_env = _value(env, "APP_ENV").lower()
    if app_env not in KNOWN_ENVIRONMENTS:
        raise RuntimeError("Runtime configuration is not ready: environment")
    if app_env not in PRODUCTION_ENVIRONMENTS:
        return
    result = validate_runtime_configuration(env)
    if not result["ready"]:
        failed = ", ".join(check["name"] for check in result["checks"] if check["critical"] and check["status"] == "invalid")
        raise RuntimeError(f"Production configuration is not ready: {failed}")
