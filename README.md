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
whole session. Changing or resetting a password invalidates all of a user's tokens. Logging out revokes
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
| `tenants/` | `Company`, `Branch`, `Till`, and the custom email-login `CustomUser` |
| `authentication/` | Login with access + refresh tokens, password reset, invitations |
| `inventory/` | Products, categories, suppliers, orders, stock batches and movements |
| `demand/`, `demand_calendar/` | Demand requests, cultural events, event/product keywords, upcoming-events feed |
| `logistics/` | Freight alerts, providers, deliveries |
| `pricing/` | Plans, subscriptions, supplier invoices, price-change log |
| `sync/` | Sync tasks and Shopify connections (tokens encrypted at rest) |
| `common/` | Alerts API, tenancy helpers, encrypted field, websocket consumer, business services |
| `frontend/` | Standalone UI components (not served by Django) |

Business logic lives in `common/services/` (price recommendation, landed-cost calculation, HMRC tariff,
Hijri calendar, Shopify, verification tokens). Periodic jobs are in each app's `tasks.py`.

## Configuration

All settings come from environment variables / `.env` (see `.env.example`): database, Redis, Celery,
token lifetimes, login/refresh throttles, field-encryption keys, and the external services (HMRC, freight rates).

## Development

```bash
pytest                                   # run the tests
pytest path/to/test_file.py::Class::test # one test
pytest --cov=authentication --cov=common # coverage (CI requires 70%)
ruff check .                             # lint (also run in CI)
```
