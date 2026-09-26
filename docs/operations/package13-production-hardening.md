# Package 13 – Production hardening

## Runtime and scale boundaries

- Core tenant lists accept bounded `limit`/`offset` pagination. Defaults are 100–200 and hard maxima are 200–500, while the existing array response format remains compatible.
- Customer list follow-up data uses one tenant-scoped aggregation instead of one order query per customer.
- B2B promotion lookup is limited to the current validity interval and at most three candidates; the existing ambiguity rule remains unchanged.
- Migration 13 adds only compound indexes for observed tenant, company, status/date and pricing lookup patterns. It changes no document.
- Selected public and expensive endpoints use the existing shared MongoDB fixed-window limiter. Limits are keyed by a server-observed peer address and stored only as HMAC identifiers.

## Production validation

Run `python -m scripts.validate_production` in the backend environment. The command validates environment safety, database connectivity, the complete migration ledger/schema version and runtime capabilities. It prints only safe states and never prints secret values. Production startup fails closed for critical invalid configuration. Optional providers may remain explicitly disabled until Package 14.

## Known staging invoice inconsistency

The previously reported paid-invoice mismatch is attributable to the synthetic demo manifest entry `RE-2026-0987` in `app/demo_seed.py`: it declares status `Bezahlt` but predates the immutable money/payment snapshot fields and therefore has no matching `paidAmountMinor` or payment ledger entry. Current manual and Stripe payment code writes both the aggregate paid amount and an immutable payment record atomically, and its regression/concurrency tests remain authoritative.

The reconciliation result is intentionally retained. Package 13 does not rewrite the staging invoice, rerun the seed, or weaken reconciliation. If demo data is refreshed later, the fixture should be versioned and regenerated consistently rather than silently repaired in place.

## Package 14 operational hooks

Readiness exposes provider-neutral capability states for database, schema, storage, email, payments, worker, backups and reconciliation. Structured logs include request, tenant, actor, duration, status and safe error references. External uptime, error and dead-job alert providers can consume these endpoints and fields without application changes.
