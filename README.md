# Business IQ API

A multi-tenant Django REST API for retail operations: inventory and stock batches, demand and
cultural-event calendars, freight alerts, price recommendations and Shopify price sync.
Data is scoped per company.

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # set SECRET_KEY at minimum
python manage.py migrate
python manage.py seed_demo_data # demo company + owner (owner@m18foods.example / change-me-please)
python manage.py seed_cultural_events  # optional placeholder events for the calendar
python manage.py runserver
```

Interactive API docs: <http://localhost:8000/api/docs/> (OpenAPI schema at `/api/schema/`).

Redis is only needed for Celery and (optionally) the websocket channel layer:

```bash
celery -A config worker -l info
celery -A config beat -l info
```

### Docker (Postgres + Redis + API + worker + beat)

```bash
cp .env.example .env     # set SECRET_KEY and DB_PASSWORD (and ALLOWED_HOSTS if not on localhost)
docker compose up --build
docker compose exec backend python manage.py createsuperuser   # optional: admin login
```

The API is on <http://localhost:8000> (`API_PORT` in `.env` changes the host port), with docs at
`/api/docs/` and the admin at `/admin/`. Services:

| Service | What it runs |
| --- | --- |
| `db` | Postgres 16 (data in the `postgres_data` volume; not published to the host) |
| `redis` | Redis 7: Celery broker/results and the websocket channel layer |
| `backend` | `migrate`, then `daphne` (HTTP + websockets); static files via WhiteNoise |
| `celery` / `celery-beat` | Worker and scheduler (`config/celery.py`), started once `backend` is healthy |

Inside Compose the database and Redis settings are fixed to the containers; only `DB_NAME`, `DB_USER`
and `DB_PASSWORD` are read from `.env`. Code is baked into the image, so rebuild after changes
(`docker compose up --build`). To use Postgres without Docker, set `DB_ENGINE=django.db.backends.postgresql`
and the `DB_*` values in `.env`.

`python manage.py seed --fixture <dumpdata.json>` loads a data snapshot into an empty database (skipped once
it has users), creates the superuser from `DJANGO_SUPERUSER_EMAIL` / `DJANGO_SUPERUSER_PASSWORD` if missing
(in the first company), and, when `STOREFRONT_BASE_URL` / `STOREFRONT_WEBHOOK_SECRET` are set, writes them to
every storefront connection. Model signals are muted while the snapshot loads. The workspace-level
`docker-compose.yml` (next to this repo, with `m18-backend-pwa/` and `fe/`) runs it on every start.

## Authentication

```bash
# 1. log in: returns a 1 hour access token and a 14 day refresh token (login is rate limited)
curl -X POST localhost:8000/auth/login/ -d username=owner@m18foods.example -d password=change-me-please
# 2. call the API
curl localhost:8000/inventory/products/ -H "Authorization: Bearer <access_token>"
# 3. when the access token expires, trade the refresh token for a new pair
curl -X POST localhost:8000/auth/token/refresh/ -d refresh_token=<refresh_token>
# 4. log out (revokes that session's refresh tokens)
curl -X POST localhost:8000/auth/logout/ -d refresh_token=<refresh_token>
```

Refresh tokens are **single use**: every refresh returns a new `refresh_token`, and the old one stops
working, so always store the newest. Sending an already-used token is treated as theft and ends that
whole session, with one allowance: a token used again within `JWT_REFRESH_REUSE_LEEWAY_SECONDS` (10 s) of
being exchanged is treated as a concurrent request (e.g. two browser tabs) and gets its own fresh pair. A
session also has a hard cap, `JWT_REFRESH_SESSION_MAX_AGE_SECONDS` (90 days) from the original login,
however often it is refreshed. Changing or resetting a password invalidates all of a user's tokens. Logging out revokes
the refresh tokens immediately, but an access token already issued stays valid until it expires (at most
`JWT_ACCESS_TOKEN_TTL_SECONDS`).

**Two-factor sign-in (TOTP).** Admins (`is_staff` / superusers) must use an authenticator app; other password users
can turn it on in Settings; operators (8-digit code) are not affected. When it applies, `/auth/login/` answers
`{"two_factor": "verify" | "enrol", "challenge", "expires_in": 300}` instead of tokens:

```bash
# verify: a 6-digit code from the app (or {"challenge", "recovery_code"}) returns the token pair
curl -X POST localhost:8000/auth/2fa/verify/ -d challenge=<challenge> -d code=123456
# enrol (an admin without an authenticator yet): get the QR/secret, then confirm with a code from the app;
# the confirm returns 10 single-use recovery codes (shown once) and the token pair
curl -X POST localhost:8000/auth/2fa/enrol/ -d challenge=<challenge>
curl -X POST localhost:8000/auth/2fa/enrol/confirm/ -d challenge=<challenge> -d code=123456
```

A challenge lives 5 minutes and dies after 5 wrong codes; a code works once; 10 wrong codes for one user lock every
code for 15 minutes; the routes are throttled (`TWO_FACTOR_THROTTLE_RATE`, 10/min). Signed in, `GET /auth/2fa/` shows
the status and `/auth/2fa/enrol/` + `/enrol/confirm/` (no challenge) set it up; `POST /auth/2fa/disable/ {code}` turns
it off (not for admins). Company staff list everyone's status at `GET /auth/2fa/users/` and reset a lost phone with
`POST /auth/2fa/users/<id>/reset/` (ends that user's sessions; recorded as a `TwoFactorEvent`, read-only in the admin).
An admin's refresh token is refused while they have no authenticator, so older sessions must sign in and enrol.
A reset ends the user's refresh tokens at once, but an access token already issued keeps working until it expires
(`JWT_ACCESS_TOKEN_TTL_SECONDS`, 1 h): after a suspected takeover, also deactivate the account or change its password.
The lockout and throttles live in the Django cache: run production with `USE_REDIS_CACHE=True`, otherwise each worker
process counts separately.

Session and HTTP Basic authentication also work (handy for the browsable API and the admin), with two-factor applied:
HTTP Basic is refused for admins and for anyone with two-factor on, and the Django admin's sign-in page asks for the
code (or a recovery code); an admin without an authenticator sets it up in the web app first.
Password reset confirmation (`/auth/password_reset/confirm/`), invitation acceptance
(`/auth/invitations/accept/`) and email-token verification (`/common/api/auth/verify-email-token/`) are public.

## Multi-tenancy

Every user belongs to a `tenants.Company`, and every API and admin screen only shows that company's data:
other companies' records are `404`, references to them are rejected, and `company` is set from the
signed-in user. Product categories, cultural events, pricing plans and the demand calendar are shared
reference data: readable by everyone, editable by staff. Superusers are not scoped. The rules live in
`common/tenancy.py`.

## Project layout

| Path | Purpose |
| --- | --- |
| `config/` | Project package: settings, URLs, ASGI/WSGI, Celery app and beat schedule |
| `tenants/` | `Company`, `Branch`, `Till` (with an API) and the custom email-login `CustomUser` |
| `authentication/` | Login with access + refresh tokens, two-factor sign-in (TOTP), password reset, invitations |
| `inventory/` | Products, categories, suppliers, orders, stock batches and movements |
| `demand/`, `demand_calendar/` | Demand requests, cultural events, event/product keywords, upcoming-events feed |
| `logistics/` | Freight alerts, providers, deliveries, the company's suppliers and suggested alternatives |
| `pricing/` | Plans, subscriptions, supplier invoices, price-change log |
| `sync/` | Sync tasks and Shopify connections (tokens encrypted at rest) |
| `common/` | Alerts, per-company settings, freight-rate baselines, tenancy helpers, encrypted field, websocket consumer, business services |

Business logic lives in `common/services/` (price recommendation, landed-cost calculation, HMRC tariff,
Hijri calendar, Shopify, verification tokens). Periodic jobs are in each app's `tasks.py`.

## Configuration

All settings come from environment variables / `.env` (see `.env.example`): database, Redis, Celery,
token lifetimes, login/refresh throttles, field-encryption keys, and the external services (below).

## What the API covers

| Area | Endpoints |
| --- | --- |
| Sign-in | `POST /auth/login/`, `/auth/token/refresh/`, `/auth/logout/`; two-factor `/auth/2fa/` (`verify/`, `enrol/`, `enrol/confirm/`, `disable/`, `users/`) |
| Passwords | `POST /auth/password_reset/` (emails a link), `/auth/password_reset/confirm/` |
| Invitations | `/auth/invitations/` (staff invite by email, list, revoke), `POST /auth/invitations/accept/` (public) |
| Company | `/tenants/company/` (yours; staff can edit), `/tenants/branches/`, `/tenants/tills/` |
| Inventory | `/inventory/products/` (and `.../{id}/stock-projection/`), `categories/`, `suppliers/`, `orders/`, `batches/` (`?location=`, `?product=`; `weight_kg` for products sold by weight), `movements/`, `locations/` (any company member and the stock role read; only staff change them; every company starts with a shop floor per branch and virtual supplier/customer/waste/adjustment/in-transit locations), `transfers/` (staff and the stock role: draft → `POST .../{id}/dispatch/` → `.../receive/` with what arrived, or `.../cancel/` a draft), `barcode-rules/` (scale-label EAN-13 layouts; members and the till read, staff change; `?active=true`), `requisitions/` (a store asks for stock; `.../{id}/fulfil/` makes a draft transfer for some or all of it, `.../cancel/` closes the rest), `yield-templates/` (butchery: an input product's expected outputs and waste in %, at most 100, the rest shown as expected loss; members and the stock role read, staff change), `breakdowns/` (butchery: record an input batch cut into the template's outputs, waste and loss in one step, `.../{id}/void/` reverses it; staff and the stock role; a batch's `unit_cost` is spread over the outputs by weight or value), `write-offs/` (stock to WASTE with a reason; above the company's `write_off_approval_threshold`, default £50, or with no known cost it waits for `.../approve/` or `.../reject/` by another staff member), `reports/yield/` and `reports/waste/` (G24: actual vs expected yield of completed breakdowns by input product, supplier or butcher, and written-off stock per UK week by reason or product with its known cost; `?from=&to=&group=`, default the last 8 weeks; staff and the stock role). A batch's optional `supplier` (set on receipt) is kept by breakdown outputs and transferred lots |
| Demand | `/demand/demands/`, `events/`, `keywords/`; calendar: `/calendar/upcoming/` (next 3 months), `events/`, `alerts/` |
| Alerts | `/common/api/alerts/stock/`, `/common/api/alerts/demand/create/`, `.../demand/dismiss/{id}/` |
| Logistics | `/logistics/freight-alerts/`, `providers/`, `deliveries/`, `suppliers/`, `alternative-suppliers/` (read-only), `freight-rates/` (read-only) |
| Pricing | `/pricing/price-changes/` (request, `approve`, `reject`), `recommendation/`, `invoices/` (with file upload and `.../{id}/file/` download), `invoice-items/`, `plans/`, `subscriptions/` |
| Till | `/sales/sessions/` (open, `close`; summary with VAT by rate), `/sales/sales/` (ring up, `void`; each line stores net, VAT, gross and the rate used; weighed products send `weight_kg` and are priced kg × `price_per_kg`; a scanned scale or GS1 label is sent as `{barcode}` only and read on the server, charged at the label price and flagged (`label_price_flagged`) beyond the company's `label_price_tolerance_percent`; a typed `weight_kg` (no label) is marked `manual_weight` and limited to the company's `manual_weight_max_kg`, default 25; `vat_summary` for the receipt), `/sales/catalogue/?since=` (G26: what a till caches; branches carry the optional `code` (G27) tills put in their offline receipt numbers; `/sales/offline-sales/` (G28: POST the sales a till kept offline; recorded once each, never refused, differences become exceptions); `/sales/exceptions/` (G29, staff: review those differences, `.../resolve/`); to keep finding and pricing products offline — products changed or removed since the last cursor, plus VAT rates, barcode rules, tills, branches and till settings; everything when `since` is missing) |
| VAT | `/vat/rates/` (read-only UK rates by code and start date; superusers change them in the admin) |
| Settings | `/common/api/settings/` (per-company key/value; staff write) |
| Trade (B2B) | `/wholesale/customers/` (ledger, open items, credit, statements, `aged-debtors/`), `price-lists/`, `orders/`, `invoices/`, `credit-notes/`, `payments/`, `runs/`, `customer-emails/` — see CLAUDE.md (G38–G51) |
| Accounting | `/accounting/accounts/`, `journals/`, `periods/`, `postings/run/`, `trial-balance/`, `reports/profit-and-loss/`, `reports/balance-sheet/`, `vat-return/`, `vat-returns/`, `exports/journals.csv`, `exports/vat.csv` (G52–G55) |
| Purchasing | `/purchasing/supplier-products/`, `/purchasing/orders/` (`draft/`, `lines/{id}/`, `approve/`, `cancel/`); supplier ordering terms on `/inventory/suppliers/` (G57; drafts only, never sent) |
| Promotions | `/promotions/promotions/` (`?status=live|scheduled|ended`, `end/`; no delete); applied by `POST /sales/sales/` and the offline replay, carried to tills in `/sales/catalogue/` (G58) |
| Reports | `/reports/dashboard/?week=` — sales and margin by channel and branch, waste and stock value by location, a week against the same days of the week before (G59; staff only, read only) |
| Shopify / sync | `/sync/shopify-connections/`, `/sync/tasks/` |

Full, always-current reference with request and response shapes: `/api/docs/`.

**Stock projection.** `GET /inventory/products/{id}/stock-projection/?days=30` projects a product's stock day by day from its
sales over the last `DEMAND_HISTORY_DAYS` days, scaled up on days covered by a demand-calendar event for its category. It returns
the expected reorder and stock-out dates.

**Price changes need approval.** Requesting a change (or the nightly markdown job proposing one) only records it. Staff approve it
to apply the new price to the product (refused if the price has moved in the meantime), and the Shopify sync then pushes approved
prices to the store; or reject it, which keeps it as a record that can never be approved. Both take an optional `{"note"}`, and each
change records its `source` (`manual` or `decay_markdown`), who requested it and who decided when. The nightly job does not
propose a markdown again at prices staff already rejected. `POST /pricing/recommendation/` works out landed cost (price plus import duty) and a recommended selling price.

**Linking the online shop's catalogue.** `python manage.py import_storefront_products --company <reg> [--dry-run]` links every
m18 product to an IQ product: by the shop's id, then barcode (from the signed `integration/stock/` snapshot), then exact name;
anything unmatched is created with an opening batch. Several matches are left for review. Each run is saved as a *Catalogue import*
in the admin, where staff link the leftovers by hand or mark them *not sold online*; the run ends with
`m18 products: N — linked L, excluded E, needs attention A`. Safe to repeat: nothing is duplicated and existing stock is never changed.
Once linked, later runs copy m18's name, description, category and price onto the IQ product (price only while the connection's
`push_prices` is off, and never a missing m18 price).

**Switching on price push.** Before ticking *Push prices* on the storefront connection, run
`python manage.py price_push_readiness --company <reg>`: it lists the price differences from the nightly report (largest first),
linked products m18 would refuse, and ends with `Ready: yes` or the reasons it is not. Every switch on or off is recorded under
*Price push changes* in the admin. The full checklist, and how to switch off, is in the workspace `docs/runbooks/price-push.md`.

### Scheduled jobs (Celery beat, `config/celery.py`)

| Job | When (UTC) | What it does |
| --- | --- | --- |
| `demand_calendar.sync_islamic_events` | Sundays 05:00 | Adds the next four months of Islamic events to the demand calendar (staff then assign categories and tune multipliers) |
| `common.scan_demand_alerts` | Mondays 06:00 | Raises "stock up" alerts for products in an upcoming event's categories when stock will not cover the extra demand |
| `common.check_low_stock` | hourly | Stock alerts for products at or below their reorder threshold |
| `logistics.check_freight_rates` | hourly, :30 | Freight alert when a service's rate moves by `RATE_CHANGE_THRESHOLD` % from its baseline (needs `FREIGHT_RATES_API_URL`) |
| `pricing.propose_decay_markdowns` | daily 02:00 | Proposes markdowns for stock nearing expiry, as pending price changes |
| `pricing.sync_approved_prices` | every 15 min | Pushes approved prices to Shopify |
| `authentication.prune_refresh_tokens` | daily 03:15 | Deletes long-dead refresh tokens |
| `wholesale.statements.run_monthly_statements` | 1st–6th 07:00 | Last month's statement PDF to each trade customer that takes them (once; the 2nd–6th catch up a missed 1st) |
| `wholesale.reminders.run_reminders` | daily 08:00 | Payment reminders 3 days before and 7 and 14 days after due, one email per customer, never twice |
| `wholesale.customer_emails.sweep_customer_emails` | every 15 min | Re-queues statement/reminder emails still queued; gives up after 3 days |
| `accounting.postings.run_nightly` | daily 01:30 | Posts the last 7 days' till takings, customer documents, waste and butchery loss to the journal (once each) |
| `purchasing.drafting.run_nightly` | daily 03:00 | Drafts one purchase order per supplier from stock projections and demand events (never sent) |

Emails (invitations, password resets) are sent with Django's mail settings (`EMAIL_*`); by default they are printed to the console.
Uploaded invoices are kept under `MEDIA_ROOT` and are only downloadable through the authenticated endpoint.

## External services

| Service | Used for | Notes |
| --- | --- | --- |
| [AlAdhan](https://aladhan.com/islamic-calendar-api) | Next Islamic event (Ramadan, Eids, ...) for the demand calendar | Public, no key. Dates follow the Umm al-Qura calendar, so moon-sighting dates can differ by a day |
| [UK Trade Tariff](https://www.trade-tariff.service.gov.uk/api/v2) | Import duty for a product's 10 digit `commodity_code` | Public, no key. Standard duty, plus a lower trade-deal rate when an `origin` country is given (named directly or through a group such as the EU or a trade scheme; group membership is looked up and cached). Duties that are not a plain percentage fall back to `DEFAULT_CUSTOMS_DUTY_RATE` |
| Shopify Admin GraphQL | Pushing approved price changes | Needs a store connection with the `write_products` scope. Pinned by `SHOPIFY_API_VERSION` (Shopify retires versions after 12 months, so keep it current). The store domain must look like `my-store.myshopify.com` |

Responses from the first two are cached (`USE_REDIS_CACHE` switches the cache to Redis). The mocked tests use
real response shapes; to check the code against the live public APIs run
`RUN_LIVE_TESTS=1 pytest common/tests/test_live_integrations.py`. Shopify needs a real store, so it is covered by
mocked tests only.

## Filing VAT with HMRC (MTD)

Staff file the VAT return to HMRC from the Accounts page (G56):
1. **Connect to HMRC**, once per company. It needs the company's VAT number.
2. Pick an **open obligation**.
3. Tick the declaration, then **Submit to HMRC**.

HMRC's receipt is stored (form bundle number, processing date, charge reference). Only then is the return filed in
IQ: the settlement journal is posted and its months are locked, as "Mark as filed" did.
- **Refused by HMRC:** nothing changes, and the reason is shown.
- **No clear answer** (timeout, 5xx or `DUPLICATE_SUBMISSION`): the attempt is **unknown**. **Check with HMRC**
  reads HMRC's copy, and files it only if the boxes match. Nothing is ever sent twice blindly.

**Ways out of an unclear result:**
- **Interrupted:** a submission still "sending" after 5 minutes counts as unknown (also when the list is read).
- **HMRC holds it but IQ couldn't record it:** the submission stays unknown (`NOT_RECORDED`). Check again once the
  cause is fixed.
- **Any unknown attempt** (`FIGURES_DIFFER`, `INTERRUPTED`, `NOT_RECORDED`, no answer): staff either record HMRC's
  figures or close the attempt, with a reason and a second click. Recording figures other than the ledger's clears
  the VAT accounts in full and puts the difference in Suspense, and the attempt's message says how much.
- **One period at a time:** a submit is refused (409, nothing sent) while another period that starts on or before
  this one ends still has no answer, because this return's months could not be locked behind it.

**While a submission may be at HMRC:** nothing new can be posted in its months, and they can't be locked. Late items
go to an open month.

**Connecting:**
- Only the browser that pressed Connect can finish the sign-in: a 10-minute HttpOnly cookie, plus the single-use
  state.
- The callback is throttled (`HMRC_CALLBACK_THROTTLE_RATE`, 30/min).
- Disconnect deletes IQ's tokens only. HMRC's grant ends after 18 months, or remove it in the HMRC account.

**Settings:**

| Setting | Meaning |
| --- | --- |
| `HMRC_VAT_ENVIRONMENT` | `off` (default: "Mark as filed" only), `sandbox` or `production`. Once on, "Mark as filed" is refused |
| `HMRC_CLIENT_ID`, `HMRC_CLIENT_SECRET` | The HMRC Developer Hub application (subscribed to "VAT (MTD)") |
| `HMRC_REDIRECT_URI` | Register it on that application. Locally `http://localhost:5173/accounting/hmrc/callback/` (through Vite); in AWS `https://<domain>/accounting/hmrc/callback/` |
| `HMRC_VENDOR_PUBLIC_IP` | For the `Gov-Vendor-Public-IP` header; blank = sent empty |
| `APP_VERSION` | The image's commit, set by the deploy workflow (`Gov-Vendor-Version`) |

**Fraud-prevention headers:** HMRC requires them by law; we send the `WEB_APP_VIA_SERVER` set
(`accounting/hmrc/fraud.py`).
- **From the browser:** device ID, screens, window, time zone and user agent, sent in each request body.
- **From the server:** the client IP and port from CloudFront's `CloudFront-Viewer-Address`, the user's last TOTP,
  and the vendor details.
- **Sent empty, by HMRC's missing-data rule:** licence IDs (in-house software), and the client IP and port on a
  local run (private address). Declare both when applying for production.
- **Checking them:** staff can test the headers in the sandbox with `POST /accounting/hmrc/fraud-check/`.

**Sandbox check:**
1. Create a sandbox application and a test organisation in the Developer Hub.
2. Set the company's VAT number to the test VRN.
3. Run with `HMRC_VAT_ENVIRONMENT=sandbox` and connect through the Accounts page, signing in as the test user.
4. Run:
   ```bash
   python manage.py hmrc_sandbox_check --company <id> --user <staff id> [--submit <period key>]
   ```
   It prints HMRC's verdict on the headers, the open obligations and, with `--submit`, the receipt.

**Production** needs HMRC's approval of the application: the fraud-header evidence and their questionnaire.

## Development

```bash
pytest                                   # run the tests
pytest path/to/test_file.py::Class::test # one test
pytest --cov=authentication --cov=common # coverage (CI requires 70%)
ruff check .                             # lint (also run in CI)
```

## CI/CD (GitHub Actions)

**CI** (`.github/workflows/ci.yml`) runs on every push:

| Job | What it checks |
| --- | --- |
| `test` | Missing migrations, migrations on Postgres 16, the test suite with a 70% coverage gate |
| `lint` | `ruff check .` |
| `docker` | The image builds, the Compose file is valid, and `manage.py check` passes inside the image |

**Deploy** (`.github/workflows/deploy.yml`) runs after CI succeeds on `main` (or manually from the Actions
tab, `main` only). It builds the image and pushes it to Amazon ECR, tagged with the commit SHA. Then
`scripts/deploy/ecs.sh deploy`:

1. Registers a new task-definition revision for each ECS service (`web`, `worker`, `beat`) on that image.
2. Runs `python manage.py migrate --noinput` once, as a one-off task on the first service's new revision.
   If it fails, nothing is rolled out.
3. Rolls every service onto its new revision with the ECS circuit breaker on, and checks that each new
   revision is the PRIMARY deployment with `rolloutState=COMPLETED`.
4. If any service fails, puts **every** service back on the revision it ran before, so two versions never run
   together, and fails the job. Revisions that were never used are deregistered, so a later "previous"
   rollback skips them.

**Rollback** (`.github/workflows/rollback.yml`, manual) runs `ecs.sh rollback <target>`. The target is
`previous` (the image before the one running) or a commit SHA still in ECR. A rollback never runs migrations.
Deploy and Rollback share one concurrency group, so they never run at the same time. Both use the
`production` environment, so you can add required reviewers under *Settings > Environments*.

Until AWS is configured, the deploy job skips itself with a warning annotation instead of failing.

### AWS setup

The infrastructure is the CloudFormation stack `deploy/aws/iq.yml` in the workspace. It holds ECR, ECS, RDS,
Redis, EFS, CloudFront, the secrets and the GitHub OIDC deploy role. Creating it, filling in the secrets and
running the first deploy are covered in the workspace runbook `docs/runbooks/deploy.md`. Its outputs fill in
these GitHub settings (*Settings > Secrets and variables > Actions*, environment `production`):

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `AWS_ROLE_ARN` | Stack output `BackendDeployRoleArn` |
| Variable | `AWS_REGION` | e.g. `eu-west-2` |
| Variable | `AWS_ECR_REPOSITORY_URL` | Stack output `EcrRepositoryUrl` (no tag) |
| Variable | `AWS_ECS_CLUSTER` | Stack output `EcsCluster` |
| Variable | `AWS_ECS_SERVICES` | Stack output `EcsServices` (space-separated) |
| Variable (optional) | `ECS_RUN_MIGRATIONS` | `false` to skip the migration step (on by default) |
| Variable (optional) | `ECS_APP_CONTAINER` | Container to migrate in; defaults to the first one on the app image |

The older single-service names still work: `AWS_ECR_REPOSITORY_URL`, `AWS_ECS_CLUSTER` and `AWS_ECS_SERVICE` as
secrets.

### Rules for migrations

- **Keep migrations backwards compatible.** They run before the rollout, and a rollback never undoes them, so
  the previous release must work on the new schema. Add a column first, then remove it a release later.
- **Never migrate in the container command** as well, or every replica tries to migrate at once.
- **What the tests cover:** `scripts/deploy/tests/test_deploy_ecs.py` runs `ecs.sh` against a stand-in `aws` CLI
  (`fake_aws.py`): a failed migration, a rolled-back rollout, a stuck rollout, a missing image, and rollback
  targets. It has not yet been run against a real AWS account (the runbook's staging rehearsal).

### Production settings

The stack sets these for the app (see `.env.example`):

- `BEHIND_HTTPS_PROXY=True`
- `NUM_PROXIES=2` (CloudFront and the load balancer, so throttles see the real client)
- `CSRF_TRUSTED_ORIGINS`
- `SECURE_HSTS_SECONDS`
- `DB_SSLMODE=require`
- `FIELD_ENCRYPTION_KEYS_REQUIRED=True`

`SECRET_KEY`, `DB_PASSWORD` and `FIELD_ENCRYPTION_KEYS` come from Secrets Manager, never from this repository.

The load balancer and the container healthcheck call `/healthz/` (`common/health.py`). It answers before the
host check and the HTTPS redirect, and never touches the database.
