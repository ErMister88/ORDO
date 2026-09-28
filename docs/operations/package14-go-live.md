# Package 14: go-live runbook

Package 14 stops after a fully tested staging release candidate. Phase B is a later operator procedure and requires explicit owner approval.

## Phase A — staging release candidate

1. Pin backend, worker and frontend to the same reviewed Git tree.
2. Confirm `APP_ENV=staging`, `DB_NAME=ordo_staging`, test-only Stripe credentials and disabled production provider gates.
3. Run the production validator and require `releaseGate.staging.status=READY`; retain `releaseGate.production.status=NOT_READY`.
4. Run migration dry-run, apply only reviewed pending staging migrations, verify the ledger, then require an empty second dry-run.
5. Deploy backend, worker and frontend. Verify liveness, readiness, schema and worker heartbeat.
6. Execute Admin, Sales, B2B and public B2C smoke flows. Do not submit a real order or payment.
7. Execute Quick Offer for Admin and Sales with prospect name only and with an authorized existing customer. Verify PDF, download, secure view, accept/decline, immutable recipient snapshot, no customer auto-creation and no order/invoice auto-creation.
8. If test providers are configured, execute S3, SMTP sink, Stripe test and sevdesk test acceptance. Record each missing provider as external setup required rather than simulating success.
9. Create a real staging backup and restore it only to `ordo_restore_<unique-name>`. Record manifest checksum, schema, counts, indexes, reference verification, RPO and RTO.
10. Verify 390×844, 768×1024 and 1440×900 critical screens, DE/IT/EN, alert delivery and operations-center visibility.
11. Record the exact release commit/tree and freeze the candidate. Package 14 ends here.

## Phase B — production (do not execute in Package 14)

1. Obtain explicit owner approval and record the approved Git tree, maintenance window, rollback owner and communications plan.
2. Provision a separate TLS-only production MongoDB database, least-privilege credentials, Atlas backup policy and a tested isolated restore target.
3. Configure backend and worker secrets in the hosting provider. Keep all demo/test flags false and use production-only domains, CORS, provider accounts and database names.
4. Complete the unresolved accounting decisions: legal invoice issuer, invoice-number authority, tax/revenue-account mapping, payment-method mapping and automatic finalization versus manual review.
5. Configure private object storage, SMTP sender/DNS, worker, live Stripe webhook, approved sevdesk production token and monitoring. Keep every provider disabled until its acceptance check passes.
6. Run the production validator. Continue only when `releaseGate.production.status=READY` and the release acknowledgements reflect real evidence, not placeholders.
7. Create and verify a pre-migration production backup. Run migration dry-run, review output, apply with the required target-bound approval, verify ledger and require an empty second dry-run.
8. Deploy backend and worker first. Verify liveness/readiness, database, schema and worker heartbeat. Deploy frontend with the production API origin and SPA rewrite.
9. Configure/verify `api.<domain>` and `app.<domain>`, TLS, DNS, CORS, canonical/public links, webhook endpoint and provider callbacks.
10. Perform controlled smoke tests for Admin, Sales, B2B, B2C catalog, offer delivery and test/small-value provider flows according to the owner-approved plan. Do not use real customer recipients during validation.
11. Enable monitoring and alerts, verify backup schedule and run a post-deploy reconciliation. Record completion, incidents and rollback decision.

## Rollback boundary

- Application rollback uses the previous reviewed Git tree; do not rewrite Git history.
- Stop new provider writes before rolling back a version that changed integration behavior.
- Never restore over production as an application rollback. Database recovery follows the separate incident plan and explicit owner approval.
- Preserve payment, offer, invoice, outbox and audit ledgers for reconciliation.
