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
python manage.py runserver
```

Interactive API docs: <http://localhost:8000/api/docs/> (OpenAPI schema at `/api/schema/`).

Redis is only needed for Celery and (optionally) the websocket channel layer:

```bash
celery -A config worker -l info
celery -A config beat -l info
```

`docker-compose.yml` starts Postgres, Redis, the API and a Celery worker.

## Authentication

```bash
# 1. exchange credentials for a token (rate limited, see LOGIN_THROTTLE_RATE)
curl -X POST localhost:8000/auth/login/ -d username=owner@m18foods.example -d password=change-me-please
# 2. call the API
curl localhost:8000/inventory/products/ -H "Authorization: Bearer <access_token>"
```

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
| `authentication/` | Login (JWT), password reset, invitations |
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
JWT lifetime, login throttle, field-encryption keys, and the external services (HMRC, freight rates).

## Development

```bash
pytest                                   # run the tests
pytest path/to/test_file.py::Class::test # one test
pytest --cov=authentication --cov=common # coverage (CI requires 70%)
ruff check .                             # lint (also run in CI)
```
