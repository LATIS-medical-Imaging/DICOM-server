#!/usr/bin/env bash
# Run spike/peak test: sudden traffic bursts, observe recovery
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERF_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PERF_DIR/docker-compose.perf.yml"
ENV_FILE="$PERF_DIR/.env.perf"
PROFILE="${1:-cpu}"
RESULTS_DIR="$PERF_DIR/results/spike"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

echo "=== DICOM Server Spike Test ==="
echo "Profile: $PROFILE"
echo ""

set -a; source "$ENV_FILE"; set +a
mkdir -p "$RESULTS_DIR"

DC="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE --profile $PROFILE"

echo "[1/4] Starting infrastructure (profile: $PROFILE)..."
$DC up -d --build --wait api
$DC up -d prometheus grafana cadvisor node-exporter postgres-exporter redis-exporter 2>/dev/null || true

echo "[2/4] Waiting for API readiness..."
"$SCRIPT_DIR/_wait_for_api.sh"

echo "[3/4] Running k6 spike test..."
$DC run --rm \
  -e K6_OUT="json=/results/k6_spike_${TIMESTAMP}.json" \
  k6 run /scripts/spike_test.js \
  --summary-export="/results/k6_summary_spike_${TIMESTAMP}.json" || true

cp "$PERF_DIR/results/k6_summary_spike_${TIMESTAMP}.json" "$RESULTS_DIR/" 2>/dev/null || true

echo "[4/4] Collecting metrics snapshot..."
"$SCRIPT_DIR/_collect_metrics.sh" "$RESULTS_DIR" "$TIMESTAMP" "$PROFILE" "spike"

echo ""
echo "=== Spike test complete ==="
echo "Results: $RESULTS_DIR"