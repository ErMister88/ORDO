# Package 14: external setup checklist

This checklist is the operator handoff for a release candidate. It contains no credentials. Store every secret in the hosting provider's encrypted environment settings, never in Git or in the public frontend environment.

## Environment and domains

| Item | Backend web service | Worker service | Frontend static site | PASS criterion |
| --- | --- | --- | --- | --- |
| Environment | `APP_ENV=production` | same | build environment only | Backend validator reports the production environment explicitly. |
| Public URLs | `APP_URL=https://api.<domain>` and `CORS_ORIGINS=https://app.<domain>` | same backend configuration | `EXPO_PUBLIC_BACKEND_URL=https://api.<domain>` | Both origins use HTTPS, have no path or trailing test URL, CORS allows only the app origin, and browser API calls succeed. |
| Tenant | `TENANCY_MODE=single`, `DEFAULT_TENANT_ID=tnt_ss_0001` | same | none | Tenant resolver starts and returns only the configured active tenant. |
| Authentication | `JWT_SECRET` from a cryptographic secret generator | same | never present | Login succeeds, a manipulated token is rejected, and the secret is absent from build artifacts and logs. |

Add the production frontend origin to Stripe return URLs and provider allowlists. Public offer links and e-mail links are generated from `APP_URL`; verify that a production smoke document contains no staging hostname. Configure the B2C shop and canonical public URL to `https://app.<domain>` at the CDN/domain layer. Do not switch DNS until the separate production approval.

## MongoDB Atlas

- **Purpose:** primary transactional database.
- **Credentials:** dedicated production database user with a generated password; operator access with MFA; separate backup/restore operator where supported.
- **Variables:** `MONGO_URL`, `DB_NAME=ordo_production` on backend and worker; never on frontend.
- **Setup:** create a production cluster/database separate from `ordo_staging`, restrict network access to deployment egress, require TLS, enable Atlas backups and alerts.
- **Test:** run the production validator against the intended database, migration dry-run, then an isolated pre-launch database test. Do not migrate production in Package 14.
- **PASS:** TLS connection succeeds, database name is production-only, schema validation is exact, backups are scheduled, and an isolated restore has been recorded.

## Private S3-compatible object storage

- **Purpose:** private documents and offer PDFs; controlled public delivery of product images.
- **Credentials:** bucket-scoped access key and secret with object read/write/delete only for the ORDO bucket; provider endpoint/region.
- **Variables:** `STORAGE_ENABLED=true`, `STORAGE_BACKEND=s3`, `S3_ENDPOINT_URL` (empty for AWS), `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_ADDRESSING_STYLE` on backend and worker.
- **Setup:** private bucket, blocked anonymous listing, encryption at rest, lifecycle/versioning according to retention policy, CORS only if the chosen signed-URL flow needs it.
- **Test:** upload a synthetic PNG and PDF through the backend, verify metadata and magic bytes, read by authorized signed URL, reject cross-tenant/expired access, then delete the synthetic objects.
- **PASS:** put/read/metadata/delete and signed URLs work; documents remain private; tenant prefixes, size limits and MIME checks are enforced; no credentials enter the frontend.

## SMTP and e-mail outbox

- **Purpose:** account invitations, offer delivery and technical notifications through the existing outbox/worker.
- **Credentials:** SMTP user/password or provider-specific SMTP credential for a dedicated sender domain.
- **Variables:** `EMAIL_ENABLED=true`, `EMAIL_BACKEND=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `EMAIL_FROM_NAME`, `EMAIL_REPLY_TO`, `EMAIL_MESSAGE_ID_DOMAIN` on backend and worker.
- **DNS:** publish provider-approved SPF and DKIM; add DMARC with an initially monitored policy; validate From and Reply-To domains.
- **Test:** send only to an approved sink address, process with the worker, inspect one outbox row and Message-ID, retry a controlled transient failure, and verify deduplication.
- **PASS:** one queued message becomes `sent`; retries do not duplicate delivery; permanent failures become visible; no real customer is contacted.

## Worker

- **Purpose:** durable e-mail, accounting and other background jobs.
- **Service:** create a separate always-on service from the backend root with start command `python -m scripts.run_worker`.
- **Variables:** same database/tenant/provider configuration as backend plus `BACKGROUND_JOBS_ENABLED=true`, `WORKER_SERVICE_ENABLED=true` and a stable unique `WORKER_ID` per process.
- **Test:** observe a fresh heartbeat, enqueue an allow-listed synthetic job, restart during retry, and verify one final result plus correct failed/dead visibility.
- **PASS:** readiness reports `background_jobs=available`, heartbeat remains fresh, retries/backoff work, and restart creates no duplicate side effect.

## Stripe

- **Purpose:** B2C Checkout payment authority.
- **Credentials:** separate live restricted/secret API key and live webhook signing secret for production; test equivalents only in staging.
- **Variables:** `PAYMENTS_ENABLED=true`, `STRIPE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, correct `APP_URL` on backend only.
- **Webhook:** `https://api.<domain>/api/payments/stripe/webhook`; subscribe to `checkout.session.completed`, `checkout.session.async_payment_succeeded`, `checkout.session.async_payment_failed`, and `checkout.session.expired`.
- **Test:** first complete the full test-mode matrix in staging, including invalid signature, duplicate and out-of-order events, amount/currency/reference mismatch and expired sessions. Live keys require a separate owner-approved production step.
- **PASS:** only signed, correctly matched events change payment state; repeated events do not duplicate orders, payments or notifications.

## sevdesk

- **Purpose:** provider-neutral accounting export through the existing adapter.
- **Credentials:** dedicated sevdesk test/sandbox token first; production token only after business decisions and separate approval.
- **Variables:** `ACCOUNTING_PROVIDER=sevdesk`, `SEVDESK_BASE_URL`, `SEVDESK_API_TOKEN`, `SEVDESK_STAGING_WRITES_ENABLED=true` for the isolated staging test; `SEVDESK_PRODUCTION_ENABLED` stays false until go-live approval.
- **Test:** map a synthetic contact and draft invoice in the test context, retry the same job, verify tenant/provider references, and ensure ambiguous contact matching fails closed.
- **PASS:** contact/invoice mapping is idempotent and tax, revenue account, payment method and invoice authority decisions are documented and approved.

## Backups and isolated restore

- **Purpose:** verified recovery of MongoDB plus independent object-storage recovery.
- **Requirements:** MongoDB Database Tools available to the backup job, encrypted durable `BACKUP_DIRECTORY`, Atlas backup policy, object versioning/backup and access to a separate restore database.
- **Test:** run `python -m scripts.backup_database --output <secure-path>`, verify manifest/checksum/schema version, then use `python -m scripts.restore_database --manifest <manifest> --target-db ordo_restore_<unique-name>`.
- **PASS:** non-empty archive and checksum-valid manifest exist; isolated restore verifies collections, counts, indexes, schema and references; measured RPO/RTO are recorded. Never restore into staging or production.

## Monitoring and alerting

- **Purpose:** detect availability and asynchronous failure before users report it.
- **Credentials:** monitor/API token of the selected provider; no provider is chosen by the codebase.
- **Targets:** frontend URL, `/api/health/live`, `/api/health/ready`, database capability, worker heartbeat, failed/dead jobs, Stripe webhook failures, e-mail failures, storage failures and accounting failures.
- **Setup:** route alerts to an owned on-call destination, define severity/escalation and redact headers, query strings and payloads.
- **Test:** create controlled synthetic failures for one endpoint/job in staging and acknowledge the alert.
- **PASS:** outage and stale-worker alerts arrive within the agreed window, recovery closes them, and logs contain request/error references without secrets.

## Domain and DNS

- **Purpose:** stable application and API origins.
- **Values:** `app.<domain>` to the static frontend/CDN and `api.<domain>` to the backend service; provider validation records and TLS certificates.
- **Test:** HTTPS and certificate chain, SPA rewrite, direct route refresh, CORS, public offer link, e-mail link, Stripe webhook reachability and no staging host in generated content.
- **PASS:** all production URLs resolve over HTTPS and every generated public/callback URL uses the production origin.
