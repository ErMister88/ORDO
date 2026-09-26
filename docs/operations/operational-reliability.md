# ORDO operational reliability

## Health and readiness

- `GET /api/health/live` proves that the API process can answer requests. It does not contact external services.
- `GET /api/health/ready` checks MongoDB, the migration ledger, tenancy configuration and reports provider capabilities.
- `GET /api/health` remains the hosting-compatible database health check.
- Optional providers such as payments, email and storage are reported as `not_configured` or `degraded`; they do not make CRM and B2B functions unavailable.

Health responses contain status codes and public runtime metadata only. They never contain connection strings, credentials or provider responses.

## Structured logs and error references

Each HTTP request receives `X-Request-ID`. Technical server failures additionally receive `X-Error-ID`. JSON logs contain only allow-listed operational fields: timestamp, level, service, environment, request, server-resolved tenant and actor identifiers, operation, duration, status and error reference. Request bodies, authorization headers, tokens, passwords and provider payloads are not logged.

Tenant-bound technical failures are stored as redacted `technical_errors` records. The Operations Center exposes the reference, category, time and operation to tenant administrators without raw payloads or exception text.

## Background jobs

The `background_jobs` collection implements tenant scope, intent idempotency, a lease token, bounded exponential backoff, crash recovery after lease expiry and a terminal `dead` state. Package 11 enables only `reconcile.tenant`, a read-only commercial consistency check. It cannot create or alter an economic record.

Run one diagnostic worker pass with:

```bash
BACKGROUND_JOBS_ENABLED=1 python -m scripts.run_jobs
```

For normal operation, deploy the persistent worker separately:

```bash
BACKGROUND_JOBS_ENABLED=1 python -m scripts.run_worker
```

The worker must run with the same validated application configuration as the API. Its heartbeat determines whether the Background-Jobs capability is actually available.

## Reconciliation

The reconciliation engine reads tenant-scoped orders, invoices, payment records, checkout resources, Stripe event ledger entries and idempotency results. It records `OK`, `WARNING`, `ERROR` or `REQUIRES_REVIEW`. It never changes commercial records. Ambiguous states always require human review.

The number of inspected documents per collection and stored issues per run is bounded. A truncated run is explicitly marked and must not be interpreted as complete.

## Operations Center security

All `/api/operations/*` routes require the existing tenant `admin` role. Sales, B2B and B2C identities receive no system state, logs, jobs, reconciliation results or recovery controls. Every collection read is server-side tenant scoped.

## Retention classes

ORDO distinguishes these classes without assigning or enforcing legal retention periods:

- business records
- audit records
- technical logs
- idempotency records
- payment event records
- background job records

Only the already approved idempotency TTL remains active. No Package-11 migration deletes business, audit, payment, job or technical-error records. Concrete retention periods require a legal and business decision.
