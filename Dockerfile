FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

RUN useradd --create-home --uid 1000 app
COPY --chown=app:app . .

# Collect static files for the admin and API docs (served by WhiteNoise).
# The key is only needed so settings can load; it is not kept in the image environment.
RUN SECRET_KEY=build-only-key-never-used-at-runtime-0123456789 python manage.py collectstatic --noinput

USER app
EXPOSE 8000

# ASGI server (HTTP + websockets). docker-compose overrides this for the worker and beat services.
CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]
