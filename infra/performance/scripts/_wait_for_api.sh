#!/usr/bin/env bash
# Wait for the API to become ready (healthcheck)
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
MAX_RETRIES="${MAX_RETRIES:-60}"
RETRY_INTERVAL="${RETRY_INTERVAL:-3}"

echo "  Waiting for API at $API_URL/api/v1/health/live ..."

for i in $(seq 1 "$MAX_RETRIES"); do
  if curl -sf "$API_URL/api/v1/health/live" > /dev/null 2>&1; then
    echo "  API is live (attempt $i/$MAX_RETRIES)"

    # Also check readiness
    if curl -sf "$API_URL/api/v1/health/ready" > /dev/null 2>&1; then
      echo "  API is ready!"
      return 0 2>/dev/null || exit 0
    fi
  fi

  if [ "$i" -eq "$MAX_RETRIES" ]; then
    echo "  ERROR: API did not become ready after $MAX_RETRIES attempts"
    exit 1
  fi

  sleep "$RETRY_INTERVAL"
done