---
name: deploy-and-ci
description: Use when editing GitHub Actions workflows, the Dockerfile/docker-compose, or debugging the build and ECS deploy pipeline.
---

# CI/CD and Docker

## CI (`.github/workflows/ci.yml`, every push)
- `test`: Postgres 16, `makemigrations --check --dry-run`, `migrate`, `pytest --cov=authentication --cov=common --cov-fail-under=70`.
- `lint`: `ruff check .` (config `ruff.toml`: E and F, line length not enforced).
- `docker`: `docker compose config --quiet`, build the image, run `manage.py check`.
- Reproduce locally with the commands in `backend-testing`.

## Deploy (`deploy.yml`, via `workflow_run` after CI passes on `main`)
Build → push to ECR (tag = commit SHA) → rewrite the ECS task definition images with `jq` → update the service. It skips with a warning unless secrets `AWS_ROLE_ARN`, `AWS_ECR_REPOSITORY_URL`, `AWS_ECS_CLUSTER`, `AWS_ECS_SERVICE` and variable `AWS_REGION` are set. With `ECS_RUN_MIGRATIONS=true` it runs `migrate` as a one-off task first and aborts the deploy on failure. The ECS circuit breaker is always on, and the job fails unless the new task definition is PRIMARY with `rolloutState=COMPLETED`.
- Validate edits: `docker run --rm -v "$PWD":/repo -w /repo rhysd/actionlint:latest`.
- The shell blocks were verified with a stand-in `aws` CLI, not real AWS; do not claim real-AWS verification.
- Never commit secrets or print them in workflow logs.

## Docker
`docker compose up --build` runs Postgres, Redis, daphne, a Celery worker and beat. Compose requires `SECRET_KEY` and `DB_PASSWORD` and overrides `DB_*`/`REDIS_*`. Code is baked into the image (no bind mount): rebuild after changes. `scripts/healthcheck.py` sends the first `ALLOWED_HOSTS` entry as Host. Static files are collected at build and served by WhiteNoise.
