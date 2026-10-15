---
name: add-tenant-endpoint
description: Use when adding or changing a tenant-owned Django model and its DRF API (model, serializer, viewset, route, role access, tests). Prevents cross-company data leaks.
---

# Add a tenant-owned model + API endpoint

Tenancy is per company (`common/tenancy.py`). Skipping any step below can expose one company's data to another.

## Steps
1. **Model** (`<app>/models.py`): give it a `company` FK, or a path that reaches one. Human-entered identifiers use `UniqueConstraint(fields=['company', ...])`, never a global `unique=True`.
2. **Register it in `TENANT_LOOKUPS`** (`common/tenancy.py`) with the ORM path to the company (e.g. `'inventory.StockBatch': 'product__company'`). `common/tests/test_tenancy.py` fails on a wrong path. The admin is auto-scoped from this list.
3. **Serializer**: extend `TenantModelSerializer` (scopes related-object fields, makes `company` read-only, turns company-unique clashes into a clean 400). Never read `company` from the request body or token.
4. **Viewset**: `TenantScopedMixin` + `ModelViewSet`. It filters the queryset, stamps `company` on create, and falls back to a `pk` order (paginated lists need a deterministic order). Other companies' rows must be 404, never 403. Shared reference data uses `SharedReferenceMixin` / `IsStaffOrReadOnly` instead and is not listed in `TENANT_LOOKUPS`.
5. **URL**: register on the app's `DefaultRouter` in `<app>/urls.py`; the prefix lives in `config/urls.py`.
6. **Operator roles**: new endpoints are closed to operators until listed in `authentication/role_access.py`. See the `operator-roles-access` skill.
7. **Migration**: see `django-migrations`.
8. **Tests**: see `backend-testing`. Always include a cross-company test (company B's user gets 404 on company A's row and cannot reference it in a POST).
9. **Frontend**: add the Vite proxy prefix in `fe/vite.config.mjs` if it is a new top-level URL prefix.

## Conventions
- Lists are paginated (`count/next/previous/results`, `page_size` max 100).
- Non-validation errors are `{code, message, status_code}` via `common.exceptions.custom_exception_handler`; validation errors keep DRF's per-field shape.
- `StockMovement` is append-only; do not add update/delete for it.

## Verify
```bash
pytest -k tenancy
pytest <app>
python manage.py makemigrations --check --dry-run
python manage.py spectacular --file schema.yml   # should print no errors/warnings
ruff check .
```
