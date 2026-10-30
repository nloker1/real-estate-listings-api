# Real Estate Listings API

A production-ready REST API for real estate listings search, favorites, and analytics.

Built to combine my brokerage experience (lokerrealty.com) with backend engineering skills.

**Live Demo** (coming soon)  
**Tech Stack**: FastAPI (Python), PostgreSQL, Redis, deployed on Render/Fly.io

## Quick Start
```bash
uvicorn app.main:app --reload
```

### Isolated database configuration

Set `APP_ENV=test` explicitly in the process environment before importing the
application. In this mode, `app.database` does not load the root `.env` and never
falls back to `DATABASE_URL`. It requires `TEST_DATABASE_URL` with:

- Driver `postgresql+asyncpg`.
- Host `localhost`, `127.0.0.1`, or `::1`.
- Database name `listing_alerts_test`.
- No query parameters or fragment (driver options can override connection targets).

Use a disposable local PostgreSQL instance and a dedicated test-only role. Do not
point a local port forward at production. These URL checks do not establish the
identity of the server listening on a local port.

Run the offline configuration checks without dependencies or a database:

```bash
python3 -B -m unittest discover -s tests -p 'test_database_config.py' -v
```

This is a configuration guard, not the complete alert test harness. It does not
provision PostgreSQL, create tables, or disable real email/RMLS integrations.
Do not run alert workers as tests until those integrations have been replaced
with fakes and external network access is blocked.

### Seed fictional alert data

With the disposable local database running and `APP_ENV=test` and
`TEST_DATABASE_URL` set as described above, run from the repository root:

```bash
venv/bin/python -m scripts.seed_test_data
```

The script creates and resets only the dedicated `seed_alerts` schema, inside
one transaction. It leaves the `public` schema untouched. Re-running replaces
all records in `seed_alerts`, including delivery history created during tests.
It needs schema-creation permission and the existing backend dependencies.

Fixtures: one fictional `buyer@example.test` lead, one active search, and six
listings covering a match, excessive price, wrong city, pending status,
unpublished listing, and too few bedrooms. IDs and timestamps are fixed;
`SYNTHETIC-MATCH` is the only eligible listing. No delivery logs are fabricated.

The seed command never imports the application database configuration, email
providers, or RMLS integration. Normal application connections do not select
`seed_alerts`; queries of manually seeded data must explicitly select that schema.
Do not run the normal worker to inspect these fixtures: fake email transport
and worker-level test isolation are still required.

### Run the isolated alert integration test

Keep the local test database running with `APP_ENV=test` and
`TEST_DATABASE_URL` environment variables set. From the repository root:

```bash
RUN_ALERT_DB_TESTS=1 venv/bin/python -B -m unittest discover -s tests -v
```

Without `RUN_ALERT_DB_TESTS=1`, database tests are skipped. Each enabled test
validates the local database URL and creates a unique `alert_regression_*` schema
with fresh tables from the current models and synthetic fixtures. Manual seeding
is not required, and existing `seed_alerts` and `public` tables are untouched. It
injects an in-memory email sender and a fixed clock into the real alert worker.
It asserts one fake email and delivery log for `SYNTHETIC-MATCH`, no additional
email on a second run, and no duplicate even when the date window is reopened.

Worker commits use savepoints within a test transaction; the entire temporary
schema and its records are rolled back on success or failure.
The test blocks socket connections and DNS lookups except for the local test
database port, and checks for blocked attempts even if the worker catches them.
It does not import the normal database module, dotenv, Resend, or RMLS.

The worker accepts optional `session_factory`, `sender`, `now`, and `delay`
dependencies. Existing production invocations retain their defaults. In test
mode, an explicit session factory and sender are mandatory, and the default
Resend transport refuses delivery. The fake sender is test support, not a new
production delivery implementation; concurrency fixes remain outstanding.
The optional `test_lead_email` keyword filters recipients, but does not replace
database or email isolation.

All three alert types use the injected sender and shared success-log helper.
Property watches initialize their baseline without sending, then commit updated
price/status baselines only together with a successful send log. They retain the
existing published-listing restriction: hidden/off-market listings that are
unpublished do not trigger property updates. Changing that policy is separate.
Market alerts wait seven days after creation or the last successful send and use
the injected clock. Their pending metric counts currently pending, published
listings with a status-change timestamp in the last week, not recent sync times.
Map-search alerts retain square-footage and acreage filters (`Listing.acreage`).
Regression coverage includes each added filter, property failure/retry and later
changes, baseline initialization, publication policy, market scheduling/retry and
status dates, and test-mode/recipient safeguards.

Partial-delivery regressions cover both a missing provider message ID and an
exception before acceptance. The first candidate fails, the second succeeds,
and the next run must retry only the failed candidate. A third run must send
nothing. The extra eligible listing exists only in the rolled-back transaction.

Matching now uses search creation as the eligibility baseline and excludes
successful EmailLogs in SQL. `last_alert_sent` records the last successful send
but does not exclude failed matches. This also recovers unsent eligible listings
previously skipped by an advanced send timestamp. No schema migration is needed.
Provider failures leave no success log and remain eligible on subsequent job
runs; retry backoff and permanent-failure handling are not yet implemented.
Database commit failures stop the worker rather than continuing a broken session.
Provider acceptance followed by a timeout or failed database commit can still
cause duplicates: durable delivery claims and provider idempotency are separate
outstanding work. These tests simulate rejection before acceptance only.

### Verified Resend event recording

`POST /api/v1/webhooks/resend` now verifies the raw body with the installed
Resend SDK before parsing events. Set the separate `RESEND_WEBHOOK_SECRET` from
the webhook's signing-secret setting in the server environment; it is not the
Resend API key and must never be included in frontend configuration. No new
dependency is needed. Missing configuration returns 503; invalid, tampered,
or stale signatures return 400. Bodies larger than 256 KiB return 413.

Deployment prerequisites (not executed automatically):

1. Review and apply `migrations/001_email_events.sql` once against the intended
   database/schema **before** deploying this handler and the updated job script.
   It adds `email_events` and a lookup index on `email_logs.message_id`; it does
   not alter historical engagement counts. Existing tracking columns from the
   prior branch (`open_count`, `click_count`, `last_clicked_url`) must exist.
   There is no automatic production migration runner. The SQL file has not been
   executed as part of the tests; tests build current models in isolated schemas.
2. Configure the signing secret and the HTTPS webhook URL in Resend. Subscribe to
   sent, delivered, delivery_delayed, opened, clicked, bounced, complained, failed,
   suppressed, and optionally scheduled email events. Verify domain open/click
   tracking settings separately. Unsupported signed event types are acknowledged
   without storing them. These account settings have not been changed here.
3. Ensure the existing scheduled job invokes the updated `run_sync.sh`. It now
   runs reconciliation after alerting and records aggregate results in
   `email-events.log`. Alternatively schedule the reconciliation command below
   independently. Monitor failed reconciliation runs, pending associations, and
   webhook delivery failures in Resend; provider retries are finite.

```bash
venv/bin/python -m app.services.reconcile_email_events
```

This command uses the normal configured database and sends no emails. It commits
up to 100 uniquely associable pending message IDs per invocation. Unknown and
ambiguous message IDs remain pending for investigation. Do not run it locally
without explicit test database configuration; it is not a preview/dry-run.

Every supported verified event is stored with unique `svix-id`, provider email ID,
type, provider occurrence time, receipt time, minimal details, and processing
status. Duplicate receipts are acknowledged without double-counting. Row locks
serialize updates for a message; event storage, counter changes, and processing
status commit together. A database failure rolls everything back and returns 503
so the provider can retry. Early events remain pending until a matching EmailLog
exists; a later receipt or the reconciliation job associates them through
`EmailLog.message_id`, then search/client/listing through that existing log.

Opens and clicks use provider timestamps, even when events arrive out of order.
The last clicked URL follows the latest event time, not delivery order. Signed
distinct opens/clicks count as separate recorded events, not unique human users.
Their source is explicitly `unclassified`: security scanners and privacy proxies
are not automatically identified. Delivery, bounce, complaint, and suppression
events are retained separately and do not increase engagement counts. They do
not yet automatically suppress future sends; preference/suppression handling and
authenticated reporting remain separate work. No public event-history endpoint
has been added.

The handler stores no raw request, subject, recipient list, IP address, or user
agent, and logs no client data or clicked URLs. Stored links may themselves contain
query parameters: treat event data as private. Historical counts remain as-is;
pre-upgrade duplicated events or receipt-time timestamps cannot be repaired from
the old counters alone. Replaying historical provider events can add to legacy
counts, so reporting should distinguish the new event history from old totals.

Run signed synthetic webhook tests with the same isolated suite:

```bash
RUN_ALERT_DB_TESTS=1 venv/bin/python -B -m unittest discover -s tests -v
```

No Resend account access, real email, or production credentials are required.
Coverage includes invalid/missing/expired signatures, raw UTF-8, malformed and
oversized payloads, duplicate and changed-duplicate requests, out-of-order times,
early and ambiguous associations, reconciliation, distinct delivery outcomes,
and storage failure followed by a successful retry.
