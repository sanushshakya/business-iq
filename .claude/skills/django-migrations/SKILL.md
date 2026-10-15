---
name: django-migrations
description: Use when changing Django models and creating, checking or applying migrations in this repo, including the fresh-initial-migration quirks.
---

# Migrations

## Workflow
```bash
python manage.py makemigrations <app>        # name it: --name add_product_external_id
python manage.py makemigrations --check --dry-run   # the CI gate; must report no changes
python manage.py migrate
```
Review the generated file before committing. Migrations under `*/migrations/*` are excluded from ruff.

## Repo quirks
- Most apps have `0001_initial` plus `0002_initial` because of cross-app foreign keys; `authentication`, `sync`, `inventory` and `tenants` have later migrations on top. Do not squash or edit applied ones.
- **Delete an old local DB rather than migrating it forward** when history was reset. Never roll back a dev DB that holds data you need: a rollback can drop columns/data (this lost data once).
- For manual runs use a throwaway DB, not `db.sqlite3`: `DB_NAME=/tmp/dev.sqlite3 python manage.py migrate` (`DB_ENGINE`/`DB_*` select Postgres).
- Adding a non-null column to a populated table needs a default or a data migration.
- New tenant model: also add it to `TENANT_LOOKUPS` (see `add-tenant-endpoint`).
- CI runs migrations on Postgres 16; the suite also passes on SQLite, so test on both when touching constraints or raw SQL.
