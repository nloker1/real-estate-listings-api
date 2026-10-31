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

### Private client activity workspace

The React frontend now has `/admin`, backed by `/api/admin` in FastAPI. This first
version is read-only and uses a single owner account configured on the server.
It includes a searchable/paginated client directory, saved search criteria and
settings, paginated email history, and paginated event timelines. No client
editing, password reset, multi-user roles, or email sending is exposed here.

Reporting joins emails to clients through SavedSearch.lead_id (not an email-string
match). It excludes BACKFILL_DRY_RUN logs from email totals. Recorded open/click
counts come from processed unique EmailEvent rows, separately from legacy
EmailLog counters. Delivery status uses provider event occurrence time, not the
arrival order. An acceptance log does not prove delivery or readership. Clicks on
matching property URLs are labeled listing clicks; other link clicks remain
separate timeline entries. Provider-reported failures appear in email history;
worker exceptions before a send is logged cannot appear there yet.

Existing email tables/event migration are required; authentication adds no new
tables. `migrations/002_admin_reporting_indexes.sql` is an optional performance
migration for search/client and email/search joins. It has not been applied.

Configure the owner account with this **local interactive** helper:

```bash
venv/bin/python -m scripts.admin_credentials
```

It prompts for a password twice and prints ADMIN_USERNAME, ADMIN_PASSWORD_HASH,
and ADMIN_SESSION_SECRET. Save those values privately in the backend environment
or its root .env in production. Never commit or send them in chat. The helper
uses scrypt with a random salt; the plaintext password is not printed or stored.
Do not put any of these values in REACT_APP_* variables.

Also set ADMIN_ALLOWED_ORIGINS to the exact frontend origin, such as
`https://gorgerealty.com` (comma-separated if multiple origins are needed).
Restart the backend after changing configuration. Missing admin configuration
fails closed with 503. Every reporting endpoint requires an authorized session;
client data and session responses have Cache-Control: no-store.

Sessions use host-only HttpOnly, SameSite=Strict cookies, secure by default, with
an eight-hour expiry. Login/logout check the request origin; logout also requires
a session CSRF token. Password validation runs in a worker thread. Login has a
bounded per-process limit of ten attempts per ten minutes per source IP. For a
public deployment with multiple backend processes, also configure a shared
reverse-proxy login rate limit; do not treat the per-process limit as distributed.
Serve the UI/API over HTTPS, redirect HTTP at the proxy, preserve the public host,
and trust forwarded headers only from your reverse proxy. Configure CORS if an
allowed admin origin is added beyond the existing public/local frontend origins.
Same-site frontend/API hosting or a same-origin /api proxy is required; unrelated
cross-site domains are intentionally not supported by this cookie policy.

Signing out clears the browser cookie; it does not revoke a copied token on the
server. Changing the password hash, username, or session secret invalidates all
existing tokens. Treat this as a single-owner account; shared staff access/SSO
and per-session revocation should be added before expanding to multiple users.

For safe local preview:

1. Start the disposable local PostgreSQL container. In the backend terminal,
   export APP_ENV=test and your guarded TEST_DATABASE_URL. Do not use the normal
   production DATABASE_URL. Test mode skips the existing root .env.
2. Run the credential helper and **export** its three generated settings in that
   terminal (they must be environment variables in test mode). Nothing is loaded
   automatically from .env in this mode.
3. Create only the fictional admin_demo schema, then start the local backend:

   ```bash
   venv/bin/python -m scripts.seed_admin_demo
   venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. In the frontend repository, run npm start and open
   `http://localhost:3000/admin`. Use the password chosen in the helper. Match
   hostnames (localhost with localhost, or 127.0.0.1 with 127.0.0.1).

In test mode, admin reporting and endpoints using get_db select admin_demo through
schema translation. This lets /properties and /map preview the same fictional
listings. My Listings fixtures include the agent filter and two sold examples
with different close dates, plus a local placeholder image. Production schema
selection is unchanged. The demo seed resets only
that dedicated schema with fictional clients, searches, log rows, and events;
it does not send email or load .env. It is refused without the guarded test
configuration. Do not run alert jobs as part of a UI preview.

The frontend uses REACT_APP_ADMIN_API_URL if explicitly configured, otherwise
port 8000 on the same loopback hostname during local development and same-origin
/api/admin in production. It never persists passwords or session tokens in browser
storage. The dev/test cookie allows HTTP; do not set APP_ENV=test or development
on production. Ensure Nginx sends /api/admin requests to FastAPI and serves /admin
and /admin/* through the React SPA fallback. Static admin HTML/JS contains no
client records; authentication is enforced at each API endpoint.

Verification:

```bash
RUN_ALERT_DB_TESTS=1 venv/bin/python -B -m unittest discover -s tests -v
```

Use explicit APP_ENV=test and TEST_DATABASE_URL for database tests. Tests create
and roll back unique schemas, use synthetic owner credentials, and access no
production services. Frontend tests mock every fetch and exercise sign-in,
client history, event timeline, expired sessions, logout, search, and safe links.

### Local UI with live production data (read-only preview)

Use the dedicated preview profile when you want to debug real recorded activity
locally. It does not use Docker or fictional seed data, and does not change the
normal `.env`. This is separate from `APP_ENV=test`.

One-time configuration, from the local backend checkout:

```bash
venv/bin/python -m scripts.configure_preview --tunnel
```

This reads only DATABASE_URL from the existing local .env and creates a private,
Git-ignored `.env.preview.local` (file mode 0600). It prompts for a new local admin
password; username is admin. The production admin password is unaffected. Provider
credentials are not copied. The generated file holds the database URL, password
hash, and local session secret: never commit it or share it in chat. Existing
preview files are not overwritten; edit them privately to change configuration.

For this deployment the DB host matches the website SSH server, and PostgreSQL
uses port 5432. The helper prints the exact SSH port-forward command. Run it in a
separate terminal and leave it open. The tunnel forwards local port 55433 to
127.0.0.1:5432 on that server. SSH uses your normal server authentication and host
verification. If your SSH username/host differs, pass `--ssh-host user@server`
to the helper. This profile assumes PostgreSQL is on the SSH server; if it is
hosted elsewhere, adjust the remote forwarding destination accordingly.

Then start the local backend:

```bash
venv/bin/python -m scripts.run_preview
```

The launcher explicitly chooses APP_ENV=preview, loads only whitelisted settings
from .env.preview.local, and binds to 127.0.0.1:8000 with proxy headers disabled.
Previously exported test variables are not used for this database selection.

In a second/third terminal, from loker-realty/frontend:

```bash
npm run start:preview
```

Open http://localhost:3000/admin and sign in with the LOCAL password selected in
the helper. The UI labels this as live production data and read-only. /properties
also reads current production listings. All frontend API URLs are pinned to the
local backend by this command. Ports 3000 and 3001 are allowed in preview mode.
Keep the SSH tunnel and backend terminal running. Ctrl+C stops each process.

Protections in this profile:

- PostgreSQL connections start every transaction read-only. Writes are rejected
  by the database, independently of the UI. Statement timeout is 15 seconds and
  the connection pool is limited to two connections.
- Only known read routes and cookie-only admin login/logout are allowed. Writes,
  webhooks, subscription forms, and the GET unsubscribe endpoint are blocked.
- Non-loopback clients, foreign hostnames, and non-local browser origins are
  rejected. Preview is a local development profile, not a production deployment.
- Email provider credentials are removed from the process. Alert sending, digest,
  sync, backfill, reconciliation, initialization, and reset entry points reject
  preview mode. Existing seed commands require test mode and refuse preview.
- Remote direct database connections require certificate-verified TLS. A direct
  connection check against the currently configured database failed certificate
  verification, so SSH tunneling is the supported setup here. Do not disable
  certificate verification on a public database connection to work around this.

For environments with trusted remote database certificates and direct network
access, omit --tunnel when creating preview configuration. A dedicated SELECT-only
PostgreSQL role is an additional option; this implementation does not create or
modify any production roles, credentials, tables, or records. Transaction read-only
settings protect the provided runtime, but someone who controls the credentials
and changes the code could bypass them; do not distribute production DB credentials.

Verification was performed against the disposable test database: SELECT succeeds,
PostgreSQL rejects DDL in read-only transactions, mutation routes are blocked,
mail sending is rejected, and normal auth/alert/webhook tests still pass. Tests
never use production. A separate connection check found the remote certificate
issue; an automated SSH check could not authenticate noninteractively. The actual
live-data preview needs your normal SSH authentication in your own terminal.

The admin homepage now reads authenticated GET /api/admin/activity. It returns
one row per client with processed open/click events, ordered by latest provider
occurrence time, plus whole-period summary counts. `days=30` is the default;
`days=7` and `days=0` select the last week and all history. Pagination is bounded
as with the other admin endpoints. Pending events, delivery-only events, legacy
counters without event records, and BACKFILL_DRY_RUN logs are excluded. Listing
clicks are classified using the same URL rules as the existing email timeline.
The complete client directory remains at frontend /admin/clients. This requires
no database migration. Restart a local preview backend to load the new read route;
the existing SSH tunnel can remain open. Tests use only synthetic local records.

### Unsubscribe token repair

New leads receive unique unsubscribe tokens. Saved-search and alert signups repair
older clients missing a token. Before sending an alert, the worker persists a
missing token and includes the unsubscribe page URL; it no longer substitutes
the Contact page. Existing nonempty tokens and subscription preferences are preserved.
Previously sent emails with Contact links cannot be repaired retroactively.

Optional maintenance from the intended backend environment (no email delivery):

```bash
venv/bin/python -m scripts.backfill_unsubscribe_tokens          # count only
venv/bin/python -m scripts.backfill_unsubscribe_tokens --apply  # persist repairs
```

Preview mode blocks this maintenance command. No database migration is required:
the token column already exists. Apply/deployment is manual; these commands have
not been run against production as part of implementation. The current unsubscribe
endpoint stops future worker selections; an email already in flight may still arrive.
