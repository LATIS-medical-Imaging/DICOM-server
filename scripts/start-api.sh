#!/bin/sh
# Entrypoint for the API service on managed platforms (Railway, Render, etc.).
# Runs Alembic migrations to head before starting the server so the schema
# is always in sync with the deployed code.
set -e

echo "==> Running database migrations"
alembic upgrade head

echo "==> Starting API server"
# Worker count is deployment-specific: each worker carries its own torch runtime
# and its own copy of any preloaded checkpoint, on the order of 1-1.5 GB
# resident, so size it against the container's memory limit rather than its CPU
# count. More than one is only safe because WebSocketHub.deliver() publishes
# through Redis — a socket held by worker 2 still receives what worker 1 sends.
#
# --workers and --reload are mutually exclusive; compose overrides this command
# for local development.
#
# Railway injects $PORT; fall back to 8000 for other platforms.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "${PORT:-8000}" \
    --workers "${UVICORN_WORKERS:-2}" \
    --proxy-headers \
    --forwarded-allow-ips "*"
