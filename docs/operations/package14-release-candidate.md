# Package 14: release-candidate status

## Staging evidence at Package 14 start

- Backend and frontend were deployed from Git tree `fe19982cde49e016fa4e2c5b76d044aea20601e2` before the final Package 14 code changes.
- Backend readiness reported database and schema version 17 available.
- Migration 17 was applied to staging after a dry-run; the second dry-run reported no pending migration.
- Admin, Sales and B2B authentication succeeded. Role checks denied staff/user-management and operations access to Sales and B2B as expected.
- Admin and Sales Quick Offer live smoke covered prospect name only, PDF generation/download, secure public link and accept/decline. Acceptance created no order or invoice and exposed no internal economics.
- The public shop rendered at 390×844, 768×1024 and 1440×900; catalog, quantity/cart and DE/IT switches were exercised without checkout or payment.

The final Package 14 commit and the new user-management behavior require a fresh deployment and smoke test before freezing a staging release candidate. The final Package 14 report records that result; this document preserves the starting evidence and operational gates.

## Provider acceptance status

Staging readiness currently reports payments, e-mail, storage, background worker and accounting as not configured, and backup tools as unavailable. No real S3, SMTP, worker, Stripe test, sevdesk test, backup/restore or external monitoring acceptance may be claimed until the checklist evidence exists.

## Historical reconciliation: RE-2026-0987

The staging record is a demo-v6 historical fixture marked paid with amount `683.20 EUR`, but has no payment ledger entry and no current payment snapshot fields. This is a historical demo/legacy data finding. Current payment authority requires verified payment records and would not create this shape. Package 14 performs no invented payment and no automatic correction.

## Production gate

The readiness validator now exposes separate staging and production gates. Production stays `NOT_READY` unless base configuration/runtime are ready, payments, storage, e-mail, worker, accounting and backups are available, and the operator has explicitly recorded accounting decisions, isolated restore verification and monitoring activation.

These acknowledgement variables are evidence flags, not a substitute for the acceptance work:

- `PRODUCTION_ACCOUNTING_DECISIONS_CONFIRMED`
- `PRODUCTION_RESTORE_VERIFIED`
- `PRODUCTION_MONITORING_ENABLED`
