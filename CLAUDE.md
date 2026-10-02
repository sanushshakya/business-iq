# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run from the repo root. `SECRET_KEY` has no default and must be set (via `.env` or the environment), otherwise settings fail to import.

```bash
export SECRET_KEY=dev-secret          # or copy .env.example to .env
python manage.py migrate              # SQLite by default (db.sqlite3)
python manage.py runserver
python manage.py seed_demo_data       # idempotent demo company + owner (lives in tenants/)

pytest                                # whole suite (use this, not `manage.py test`)
pytest authentication/tests.py::PasswordResetConfirmTests::test_valid_token_resets_password
pytest -k tenancy
pytest --cov=authentication --cov=common --cov-fail-under=70   # the CI gate

python manage.py makemigrations --check --dry-run
python manage.py spectacular --file schema.yml                 # OpenAPI; should print no errors/warnings
celery -A config worker -l info       # needs Redis; `celery -A config beat` for the schedule
ruff check .                          # CI lint step
```

Bare `manage.py test` skips `common/tests/` (unittest discovery), so use pytest. A local `venv/` is git-ignored; API docs are served at `/api/docs/`.

## Architecture

Django 5.2+/DRF monolith. `config/` is only the project package (settings, urls, asgi, wsgi, celery); every app is a top-level directory. `README.md` is stale (it describes `ShopifyStore`, `celery_app/`); trust the code.

**Multi-tenancy is by company, not by schema.** `tenants.Company` is the tenant; `AUTH_USER_MODEL = tenants.CustomUser` (email login, has a `company` FK). `common/tenancy.py` is the single source of truth: `TENANT_LOOKUPS` maps each tenant-owned model to the ORM path reaching its company (e.g. `StockMovement -> batch__product__company`).
- Viewsets for tenant data must use `TenantScopedMixin` (filters the queryset, stamps `company` on create, requires a company) and serializers must extend `TenantModelSerializer` (limits related-object fields to the user's company, makes `company` read-only). Other companies' rows are 404, never 403.
- Adding a tenant-owned model means adding it to `TENANT_LOOKUPS`; a test in `common/tests/test_tenancy.py` fails if a lookup path is wrong.
- Shared reference data (`ProductCategory`, `CulturalEvent`, `PricingPlan`, `demand_calendar`) is unscoped: viewsets use `IsStaffOrReadOnly`.
- Superusers bypass scoping; users with no company get 403. `authentication.middleware.TenantMiddleware` (JWT -> `request.company`) exists but is **not** in `MIDDLEWARE`; scoping uses `request.user.company`.
- Default DRF permission is `IsAuthenticated`. Only password-reset confirm, invitation accept and verify-email-token are `AllowAny`.

**App layout.** Each app follows models / serializers / views (ModelViewSets) / urls (`DefaultRouter`). URL prefixes are in `config/urls.py` (`auth/`, `inventory/`, `demand/`, `calendar/`, `logistics/`, `pricing/`, `sync/`, `common/`). `common` also holds cross-app pieces: `DemandAlert`/`StockAlert` models, business `services/` (price recommendation, cost calculation, HMRC tariff, Hijri calendar, Shopify, verification tokens), the websocket consumer (`consumers.py`, `routing.py`), and `tenancy.py`.

**Background work.** Celery tasks live in each app's `tasks.py` (autodiscovered): `common` (low-stock and demand alerts), `logistics` (freight rates), `pricing` (`sync_approved_prices` pushes approved `PriceChangeLog`s to Shopify via `ShopifyService`), `sync`. The beat schedule is defined in `config/celery.py`. `config/__init__.py` imports the Celery app.

**Realtime.** `config/asgi.py` routes websockets through `common/routing.py` (`/ws/sync/`). The channel layer is in-memory unless `USE_REDIS_CHANNELS=True`.

**Settings** read everything through `python-decouple` (`.env`). External-service settings (`HMRC_*`, `FREIGHT_RATES_API_URL`, `RATE_CHANGE_THRESHOLD`, `DEFAULT_CUSTOMS_DUTY_RATE`, `REDIS_*`) are listed in `.env.example`.

## Gotchas

- Migrations were regenerated as a fresh initial set; most apps have a `0001_initial` plus `0002_initial` because of cross-app foreign keys. Delete any old local DB rather than trying to migrate it forward.
- Tests build data with factories in `common/tests/factories.py`. A test acting as a user must create its data under `user.company`, otherwise the scoped API correctly hides it.
- `PriceRecommendationService.apply_decay_pricing` compounds markdowns if run repeatedly; it is not scheduled for that reason.
- Shopify access tokens are stored unencrypted in `sync.ShopifyConnection`.
