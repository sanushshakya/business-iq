# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Run from the repo root. `SECRET_KEY` has no default and must be set (via `.env` or the environment), otherwise settings fail to import.

```bash
export SECRET_KEY=dev-secret          # or copy .env.example to .env
python manage.py migrate              # SQLite by default (db.sqlite3)
python manage.py runserver
python manage.py seed_demo_data       # idempotent demo company + owner (lives in tenants/)
python manage.py seed_cultural_events # idempotent placeholder CulturalEvents (lives in demand/)

pytest                                # whole suite (use this, not `manage.py test`)
pytest authentication/tests.py::PasswordResetConfirmTests::test_valid_token_resets_password
pytest -k tenancy
pytest --cov=authentication --cov=common --cov-fail-under=70   # the CI gate

python manage.py makemigrations --check --dry-run
python manage.py spectacular --file schema.yml                 # OpenAPI; should print no errors/warnings
celery -A config worker -l info       # needs Redis; `celery -A config beat` for the schedule
ruff check .                          # CI lint step (config in ruff.toml)
```

`manage.py test` also works, but CI and the Makefile use pytest. A local `venv/` is git-ignored; API docs are served at `/api/docs/`.

## Architecture

Django 5.2+/DRF monolith. `config/` is only the project package (settings, urls, asgi, wsgi, celery); every app is a top-level directory. 

**Multi-tenancy is by company, not by schema.** `tenants.Company` is the tenant; `AUTH_USER_MODEL = tenants.CustomUser` (email login, has a `company` FK). `common/tenancy.py` is the single source of truth: `TENANT_LOOKUPS` maps each tenant-owned model to the ORM path reaching its company (e.g. `StockMovement -> batch__product__company`).
- Viewsets for tenant data must use `TenantScopedMixin` (filters the queryset, stamps `company` on create, requires a company) and serializers must extend `TenantModelSerializer` (limits related-object fields to the user's company, makes `company` read-only). Other companies' rows are 404, never 403.
- Adding a tenant-owned model means adding it to `TENANT_LOOKUPS`; a test in `common/tests/test_tenancy.py` fails if a lookup path is wrong.
- Shared reference data (`ProductCategory`, `CulturalEvent`, `PricingPlan`, `demand_calendar`) is unscoped: viewsets use `IsStaffOrReadOnly`.
- Superusers bypass scoping; users with no company get 403. The company always comes from `request.user.company` (loaded from the DB), never from a token claim.
- The Django admin is scoped the same way: `TenantAdminMixin`, applied to explicit admins and auto-registered for every other `TENANT_LOOKUPS` model in `common.apps.CommonConfig.ready()`; `CustomUser` has a dedicated `CustomUserAdmin` in `tenants/admin.py` that hides superusers, `is_superuser`, groups, permissions and `company` from company staff (anti-escalation; covered by `CustomUserAdminTests`).
- Default DRF permission is `IsAuthenticated`. Only login, password-reset confirm, invitation accept and verify-email-token are `AllowAny`.

**Auth.** `POST /auth/login/` returns a short-lived HS256 access token (signed with `SECRET_KEY`, `authentication/jwt_handler.py`) plus an opaque refresh token. `authentication.authentication.JWTAuthentication` is the first DRF authenticator, followed by session and basic; unauthenticated requests therefore get 401. Refresh tokens (`authentication/refresh_tokens.py`, model `RefreshToken`, only a SHA-256 stored) are single use: `POST /auth/token/refresh/` rotates within a "family", and replaying a used token revokes the family. Both token types embed/store a stamp of the password hash (`password_stamp`), so any password change invalidates every earlier token. `POST /auth/logout/` revokes a family; access tokens are stateless and live until expiry. Revocation must happen outside `transaction.atomic()` blocks that then raise, or it is rolled back. A daily Celery task prunes dead tokens.

**API conventions.** Lists are paginated (`StandardResultsSetPagination`: `count/next/previous/results`, `page_size` capped at 100) and viewsets must have a deterministic order (the tenancy mixins fall back to `pk`). Non-validation errors are `{code, message, status_code}` via `common.exceptions.custom_exception_handler`; validation errors keep DRF's per-field shape.

**App layout.** Each app follows models / serializers / views (ModelViewSets) / urls (`DefaultRouter`). URL prefixes are in `config/urls.py` (`auth/`, `inventory/`, `demand/`, `calendar/`, `logistics/`, `pricing/`, `sync/`, `common/`). `common` also holds cross-app pieces: `DemandAlert`/`StockAlert` models, business `services/` (price recommendation, cost calculation, HMRC tariff, Hijri calendar, Shopify, verification tokens), the websocket consumer (`consumers.py`, `routing.py`), and `tenancy.py`.

**Background work.** Celery tasks live in each app's `tasks.py` (autodiscovered): `common` (low-stock and demand alerts), `logistics` (freight rates), `pricing` (`sync_approved_prices` pushes approved `PriceChangeLog`s to Shopify via `ShopifyService`), `sync`, `authentication` (prunes dead refresh tokens). The beat schedule is defined in `config/celery.py`. `config/__init__.py` imports the Celery app.

**Realtime.** `config/asgi.py` routes websockets through `common/routing.py` (`/ws/sync/`). The channel layer is in-memory unless `USE_REDIS_CHANNELS=True`.

**Encrypted fields.** `common.fields.EncryptedTextField` (Fernet, keys from `FIELD_ENCRYPTION_KEYS`, falling back to a key derived from `SECRET_KEY`) is used for `ShopifyConnection.access_token`; it cannot be filtered on, and legacy plaintext values still read fine.

**Static files** are served by WhiteNoise (`STATIC_ROOT=staticfiles/`, collected in the Docker build).

**Settings** read everything through `python-decouple` (`.env`). External-service settings (`HMRC_*`, `FREIGHT_RATES_API_URL`, `RATE_CHANGE_THRESHOLD`, `DEFAULT_CUSTOMS_DUTY_RATE`, `REDIS_*`) are listed in `.env.example`.

## Gotchas

- Migrations are a fresh initial set; most apps have a `0001_initial` plus `0002_initial` because of cross-app foreign keys (`authentication` and `sync` add later migrations on top). Delete any old local DB rather than migrating it forward.
- Docker: `docker compose up --build` runs Postgres, Redis, daphne, a worker and beat; compose overrides `DB_*`/`REDIS_*` to the containers and requires `SECRET_KEY` and `DB_PASSWORD`. Code is baked into the image (no bind mount), so rebuild after changes. `scripts/healthcheck.py` sends the first `ALLOWED_HOSTS` entry as the Host header. `.github/workflows/deploy.yml` builds a `Dockerfile.dev` that does not exist.
- Dependencies are split: `requirements.txt` (runtime/image) and `requirements-dev.txt` (tests, lint). Settings support Postgres via `DB_ENGINE`/`DB_*`; the full test suite passes on both SQLite and Postgres.
- Tests build data with factories in `common/tests/factories.py`. A test acting as a user must create its data under `user.company`, otherwise the scoped API correctly hides it.
- `PriceRecommendationService.apply_decay_pricing` is idempotent: markdowns are computed from the pre-markdown price recorded in `PriceChangeLog`, so it is safe to run repeatedly.
- The Shopify variant payload and the Hijri/HMRC endpoint URLs are unverified against the real services; they are only covered by mocked tests.
