#!/usr/bin/env bash
# Run load test: normal expected traffic
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERF_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PERF_DIR/docker-compose.perf.yml"
ENV_FILE="$PERF_DIR/.env.perf"
PROFILE="${1:-cpu}"
RESULTS_DIR="$PERF_DIR/results/load"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

echo "=== DICOM Server Load Test ==="
echo "Profile: $PROFILE"
echo "Timestamp: $TIMESTAMP"
echo ""

# Export env vars
set -a; source "$ENV_FILE"; set +a

# Override for load test
export BENCHMARK_DURATION="${BENCHMARK_DURATION:-60s}"
export BENCHMARK_CONCURRENCY="${BENCHMARK_CONCURRENCY:-5}"

mkdir -p "$RESULTS_DIR"
mkdir -p "$PERF_DIR/reports"

# Start infrastructure
echo "[1/5] Starting infrastructure (profile: $PROFILE)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" up -d \
  postgres pgbouncer redis minio minio-init api worker prometheus cadvisor grafana \
  node-exporter postgres-exporter redis-exporter 2>/dev/null || \
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" up -d \
  postgres pgbouncer redis minio minio-init api prometheus cadvisor grafana \
  node-exporter postgres-exporter redis-exporter

# Wait for API
echo "[2/5] Waiting for API readiness..."
"$SCRIPT_DIR/_wait_for_api.sh"

# Run Alembic migration so DB tables exist
echo "[2.5/5] Running database migration..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" exec -T api \
  alembic upgrade head 2>/dev/null || true

# Run k6 load test
echo "[3/5] Running k6 load test..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" run --rm \
  -e K6_OUT="json=/results/k6_load_${TIMESTAMP}.json" \
  k6 run /scripts/load_test.js \
  --summary-export="/results/k6_summary_load_${TIMESTAMP}.json" || true

# Copy k6 results to load subdir
cp "$PERF_DIR/results/k6_summary_load_${TIMESTAMP}.json" "$RESULTS_DIR/" 2>/dev/null || true
cp "$PERF_DIR/results/k6_load_${TIMESTAMP}.json" "$RESULTS_DIR/" 2>/dev/null || true

# Run Locust load test
echo "[4/5] Running Locust load test..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile "$PROFILE" run --rm \
  locust || true

# Collect metrics
echo "[5/5] Collecting metrics snapshot..."
"$SCRIPT_DIR/_collect_metrics.sh" "$RESULTS_DIR" "$TIMESTAMP" "$PROFILE" "load"

echo ""
echo "=== Load test complete ==="
echo "Results: $RESULTS_DIR"
echo "Grafana: http://localhost:3000 (dashboard: DICOM Server Performance)"