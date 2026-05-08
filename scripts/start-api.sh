#!/bin/sh
# Entrypoint for the API service on managed platforms (Railway, Render, etc.).
# Runs Alembic migrations to head before starting the server so the schema
# is always in sync with the deployed code.
set -e

echo "==> Running database migrations"
alembic upgrade head

echo "==> Starting API server"
# Railway injects $PORT; fall back to 8000 for other platforms.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --proxy-headers \
    --forwarded-allow-ips "*"
