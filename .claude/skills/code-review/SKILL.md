---
name: code-review
description: Use when reviewing backend changes (a diff, branch or PR) for tenancy, auth, correctness and test gaps.
---

# Backend review checklist

Run `git diff main...HEAD` and check, in this order:

1. **Tenancy**: new tenant model in `TENANT_LOOKUPS`? viewset uses `TenantScopedMixin`, serializer `TenantModelSerializer`? any `.objects.all()`/raw query bypassing scope? company never taken from the request body or a token claim? other-company access is 404?
2. **Auth/roles**: new endpoint added to `role_access.py` only where intended? `AllowAny` limited to login, password-reset confirm, invitation accept, verify-email? no token/code/hash logged or returned? revocation not inside an atomic block that later raises?
3. **Data integrity**: `StockMovement` append-only; `PriceChangeLog` approval flow intact; unique constraints include `company`; migrations present and `makemigrations --check` clean; idempotent tasks/commands.
4. **API conventions**: paginated, deterministic ordering, error shape, no N+1 (check `select_related`/`prefetch_related` on serializers).
5. **Security**: user-supplied URLs (SSRF), file uploads (extension + magic bytes), secrets in code/logs, `SECRET_KEY` handling.
6. **Tests**: new behaviour covered, including a cross-company case; mocks for external services; `pytest` and `ruff check .` pass.

Report findings by severity with file:line and a concrete failure scenario; separate must-fix from nice-to-have.
