#!/bin/sh
# Entrypoint for the API service on managed platforms (Render, Railway, etc.).
# Runs Alembic migrations to head before starting the server, so the schema
# is always up-to-date with the deployed code.
set -e

echo "==> Running database migrations"
alembic upgrade head

echo "==> Starting API server"
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips "*"
