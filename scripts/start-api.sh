#!/bin/sh
# Entrypoint for the API service on managed platforms (Render free tier, etc.).
# Background worker services are not available on the free plan, so the Celery
# worker runs as a background process inside the same container.
set -e

echo "==> Running database migrations"
alembic upgrade head

echo "==> Starting Celery worker (background)"
celery -A app.workers.celery_app.celery_app worker \
    --loglevel=info \
    --concurrency=1 &

echo "==> Starting API server"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips "*"
