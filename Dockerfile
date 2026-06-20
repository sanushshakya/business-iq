# Stage 1: Builder Image
FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY supplyiq/ ./

RUN python manage.py collectstatic --noinput --clear

# Stage 2: Production Image
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DJANGO_SECRET_KEY='your_secret_key_here' \
    DATABASE_URL='your_database_url_here'

WORKDIR /app

COPY --from=builder /app/venv /app/venv
COPY --from=builder /app/static /app/static
COPY supplyiq/ ./

RUN chown -R www-data:www-data /app

USER www-data

CMD ["gunicorn", "supplyiq.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "4"]