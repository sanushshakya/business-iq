---
name: backend-testing
description: Use when writing or running backend tests (pytest), choosing factories, or reproducing the CI test/coverage gate.
---

# Backend testing

```bash
export SECRET_KEY=dev-secret            # required, no default
pytest                                   # use this, not manage.py test
pytest authentication/tests.py::PasswordResetConfirmTests::test_valid_token_resets_password
pytest -k tenancy
pytest --cov=authentication --cov=common --cov-fail-under=70   # CI gate
ruff check .
RUN_LIVE_TESTS=1 pytest common/tests/test_live_integrations.py # AlAdhan + UK tariff, opt-in only
```

## Conventions
- Files are `tests.py` or `test_*.py` (`pytest.ini`, `DJANGO_SETTINGS_MODULE=config.settings`). Layout varies per app: single `tests.py`, a `tests/` package (`common`, `sales`) or `test_*.py` beside the code.
- Build data with factories in `common/tests/factories.py` (`CompanyFactory`, `UserFactory`, `ProductFactory`, ...). Add a factory there for new models.
- **A test acting as a user must create its data under `user.company`**, otherwise the scoped API correctly hides it and you get confusing 404s.
- Always add a second company and assert it cannot see or reference the first company's rows.
- Auth endpoints are throttled; tests that hit them repeatedly patch `ScopedRateThrottle.THROTTLE_RATES` with `mock.patch.object` (see `ROOMY_RATES` in `tenants/test_operators.py`) instead of sleeping.
- External services (Shopify, HMRC, AlAdhan, freight) are mocked; only `test_live_integrations.py` hits the network. Tariff fixture: `common/tests/fixtures/tariff_0804100099.json`.
- CI runs on Postgres 16 with `DEBUG=True`; the suite should pass on SQLite and Postgres.
- Run the `ruff` and `makemigrations --check --dry-run` gates before pushing.
