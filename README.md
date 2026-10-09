# Business IQ API

A multi-tenant Django REST API for retail operations: inventory and stock batches, demand and
cultural-event calendars, freight alerts, price recommendations and Shopify price sync.
Data is scoped per company.

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt   # requirements.txt is runtime only (what the Docker image uses)
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

Session and HTTP Basic authentication also work (handy for the browsable API and the admin).
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
| `authentication/` | Login with access + refresh tokens, password reset, invitations |
| `inventory/` | Products, categories, suppliers, orders, stock batches and movements |
| `demand/`, `demand_calendar/` | Demand requests, cultural events, event/product keywords, upcoming-events feed |
| `logistics/` | Freight alerts, providers, deliveries, the company's suppliers and suggested alternatives |
| `pricing/` | Plans, subscriptions, supplier invoices, price-change log |
| `sync/` | Sync tasks and Shopify connections (tokens encrypted at rest) |
| `common/` | Alerts, per-company settings, freight-rate baselines, tenancy helpers, encrypted field, websocket consumer, business services |
| `frontend/` | Standalone React/Angular UI components (no build setup; not served by Django). They call the endpoints above |

Business logic lives in `common/services/` (price recommendation, landed-cost calculation, HMRC tariff,
Hijri calendar, Shopify, verification tokens). Periodic jobs are in each app's `tasks.py`.

## Configuration

All settings come from environment variables / `.env` (see `.env.example`): database, Redis, Celery,
token lifetimes, login/refresh throttles, field-encryption keys, and the external services (below).

## What the API covers

| Area | Endpoints |
| --- | --- |
| Sign-in | `POST /auth/login/`, `/auth/token/refresh/`, `/auth/logout/` |
| Passwords | `POST /auth/password_reset/` (emails a link), `/auth/password_reset/confirm/` |
| Invitations | `/auth/invitations/` (staff invite by email, list, revoke), `POST /auth/invitations/accept/` (public) |
| Company | `/tenants/company/` (yours; staff can edit), `/tenants/branches/`, `/tenants/tills/` |
| Inventory | `/inventory/products/` (and `.../{id}/stock-projection/`), `categories/`, `suppliers/`, `orders/`, `batches/`, `movements/` |
| Demand | `/demand/demands/`, `events/`, `keywords/`; calendar: `/calendar/upcoming/` (next 3 months), `events/`, `alerts/` |
| Alerts | `/common/api/alerts/stock/`, `/common/api/alerts/demand/create/`, `.../demand/dismiss/{id}/` |
| Logistics | `/logistics/freight-alerts/`, `providers/`, `deliveries/`, `suppliers/`, `alternative-suppliers/` (read-only), `freight-rates/` (read-only) |
| Pricing | `/pricing/price-changes/` (request, `approve`), `recommendation/`, `invoices/` (with file upload and `.../{id}/file/` download), `invoice-items/`, `plans/`, `subscriptions/` |
| Settings | `/common/api/settings/` (per-company key/value; staff write) |
| Shopify / sync | `/sync/shopify-connections/`, `/sync/tasks/` |

Full, always-current reference with request and response shapes: `/api/docs/`.

**Stock projection.** `GET /inventory/products/{id}/stock-projection/?days=30` projects a product's stock day by day from its
sales over the last `DEMAND_HISTORY_DAYS` days, scaled up on days covered by a demand-calendar event for its category. It returns
the expected reorder and stock-out dates.

**Price changes need approval.** Requesting a change (or the nightly markdown job proposing one) only records it. Staff approve it
to apply the new price to the product (refused if the price has moved in the meantime), and the Shopify sync then pushes approved
prices to the store. `POST /pricing/recommendation/` works out landed cost (price plus import duty) and a recommended selling price.

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
tab, `main` only). It builds the image, pushes it to Amazon ECR tagged with the commit SHA, registers a new
ECS task definition revision that points every container using that repository at the new image, updates the
service and waits for it to become stable. The previous and new task definitions are listed in the run summary
for rollbacks. Deployments never overlap, and the job uses the `production` environment, so you can add
required reviewers under *Settings > Environments*.

Until the setup below is done the deploy job skips itself with a warning annotation rather than failing.

### One-time AWS setup

1. In AWS IAM, add the OIDC identity provider `token.actions.githubusercontent.com` (audience `sts.amazonaws.com`)
   and create a role it can assume. Trust it for this repository's `production` environment only:

   ```json
   {"Effect": "Allow", "Action": "sts:AssumeRoleWithWebIdentity",
    "Principal": {"Federated": "arn:aws:iam::<account>:oidc-provider/token.actions.githubusercontent.com"},
    "Condition": {
      "StringEquals": {"token.actions.githubusercontent.com:aud": "sts.amazonaws.com"},
      "StringLike":   {"token.actions.githubusercontent.com:sub": "repo:sanushshakya/business-iq:environment:production"}}}
   ```

   The role needs: `ecr:GetAuthorizationToken`; push access to the repository (`ecr:BatchCheckLayerAvailability`,
   `InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`, `PutImage`); `ecs:DescribeServices`,
   `ecs:DescribeTaskDefinition`, `ecs:RegisterTaskDefinition`, `ecs:UpdateService`; and `iam:PassRole` for the
   task and execution roles. For automatic migrations (below) it also needs `ecs:RunTask` and `ecs:DescribeTasks`.
2. In GitHub, *Settings > Secrets and variables > Actions*:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `AWS_ROLE_ARN` | ARN of the role above |
   | Secret | `AWS_ECR_REPOSITORY_URL` | `<account>.dkr.ecr.<region>.amazonaws.com/<repo>` (no tag) |
   | Secret | `AWS_ECS_CLUSTER` | ECS cluster name |
   | Secret | `AWS_ECS_SERVICE` | ECS service name |
   | Variable | `AWS_REGION` | e.g. `eu-west-2` |
   | Variable (optional) | `ECS_RUN_MIGRATIONS` | `true` to run `migrate` before each rollout (see below) |
   | Variable (optional) | `ECS_APP_CONTAINER` | Container to run `migrate` in; defaults to the first one using the app image |

### Migrations and rollbacks

- **Rollbacks are automatic.** Every deployment switches on the ECS deployment circuit breaker with rollback,
  and the job then checks that the *new* task definition is the one that actually completed. A rollout that ECS
  rolled back (which looks like a stable service) therefore fails the job instead of reporting success.
- **Migrations are opt-in.** Set the variable `ECS_RUN_MIGRATIONS` to `true` and the deployment first runs
  `python manage.py migrate --noinput` once, as a one-off task on the new task definition using the service's
  own launch type and network settings. If it fails, nothing is deployed. Do not also migrate in the task
  definition's command, or every replica will try to migrate at once. Migrate *before* the rollout means the
  old code briefly runs against the new schema, so keep migrations backwards compatible.
- Both were tested against a stand-in for the AWS CLI (success, failed migration, rolled-back rollout, stuck
  rollout, timeouts), not against a live AWS account. Try the first deployment on a non-production service.

### Things the pipeline does not do

- **Secrets for the app.** `SECRET_KEY` (32+ characters), `DB_PASSWORD`, `FIELD_ENCRYPTION_KEYS` and the rest
  belong in the task definition (ideally from Secrets Manager), not in this repository.
