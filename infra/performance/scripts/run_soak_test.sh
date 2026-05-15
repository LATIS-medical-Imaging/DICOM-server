#!/usr/bin/env bash
# Run soak/endurance test: long-running, detect memory leaks
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERF_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PERF_DIR/docker-compose.perf.yml"
ENV_FILE="$PERF_DIR/.env.perf"
PROFILE="${1:-cpu}"
RESULTS_DIR="$PERF_DIR/results/soak"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

echo "=== DICOM Server Soak Test ==="
echo "Profile: $PROFILE"
echo "Duration: 15 minutes"
echo ""

set -a; source "$ENV_FILE"; set +a
mkdir -p "$RESULTS_DIR"

echo "[1/4] Starting infrastructure (profile: $PROFILE)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" up -d \
  postgres pgbouncer redis minio minio-init api worker prometheus cadvisor grafana \
  node-exporter postgres-exporter redis-exporter 2>/dev/null || \
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" up -d \
  postgres pgbouncer redis minio minio-init api prometheus cadvisor grafana \
  node-exporter postgres-exporter redis-exporter

echo "[2/4] Waiting for API readiness..."
"$SCRIPT_DIR/_wait_for_api.sh"

echo "[3/4] Running k6 soak test (15 min)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" run --rm \
  -e K6_OUT="json=/results/soak/k6_soak_${TIMESTAMP}.json" \
  k6 run /scripts/soak_test.js \
  --summary-export="/results/soak/k6_summary_${TIMESTAMP}.json" || true

echo "[4/4] Collecting metrics snapshot..."
"$SCRIPT_DIR/_collect_metrics.sh" "$RESULTS_DIR" "$TIMESTAMP" "$PROFILE" "soak"

echo ""
echo "=== Soak test complete ==="
echo "Results: $RESULTS_DIR"