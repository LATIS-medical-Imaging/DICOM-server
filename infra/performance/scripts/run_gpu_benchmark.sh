#!/usr/bin/env bash
# Run GPU vs CPU comparison benchmark
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PERF_DIR="$(dirname "$SCRIPT_DIR")"
COMPOSE_FILE="$PERF_DIR/docker-compose.perf.yml"
ENV_FILE="$PERF_DIR/.env.perf"
RESULTS_DIR_CPU="$PERF_DIR/results/cpu"
RESULTS_DIR_GPU="$PERF_DIR/results/gpu"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

echo "=== DICOM Server GPU vs CPU Benchmark ==="
echo ""

set -a; source "$ENV_FILE"; set +a
mkdir -p "$RESULTS_DIR_CPU" "$RESULTS_DIR_GPU"

# --- Phase 1: CPU benchmark ---
echo "=========================================="
echo "Phase 1: CPU Benchmark"
echo "=========================================="

# Clean previous run
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile cpu down -v 2>/dev/null || true

echo "[CPU 1/4] Starting CPU stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile cpu up -d

echo "[CPU 2/4] Waiting for API..."
"$SCRIPT_DIR/_wait_for_api.sh"

echo "[CPU 3/4] Running k6 load test (CPU)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile cpu run --rm \
  -e K6_OUT="json=/results/cpu/k6_cpu_${TIMESTAMP}.json" \
  k6 run /scripts/load_test.js \
  --summary-export="/results/cpu/k6_summary_${TIMESTAMP}.json" || true

echo "[CPU 4/4] Collecting CPU metrics..."
"$SCRIPT_DIR/_collect_metrics.sh" "$RESULTS_DIR_CPU" "$TIMESTAMP" "cpu" "gpu-comparison"

# Stop CPU stack
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile cpu down -v 2>/dev/null || true

# --- Phase 2: GPU benchmark ---
echo ""
echo "=========================================="
echo "Phase 2: GPU Benchmark"
echo "=========================================="

# Check NVIDIA runtime
if ! docker info 2>/dev/null | grep -q nvidia; then
  echo "WARNING: NVIDIA Docker runtime not detected. Skipping GPU benchmark."
  echo "Install nvidia-container-toolkit to enable GPU benchmarks."
  echo ""
  echo "=== GPU benchmark skipped — CPU results available in $RESULTS_DIR_CPU ==="
  exit 0
fi

echo "[GPU 1/4] Starting GPU stack..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile gpu up -d

echo "[GPU 2/4] Waiting for API..."
"$SCRIPT_DIR/_wait_for_api.sh"

echo "[GPU 3/4] Running k6 load test (GPU)..."
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile gpu run --rm \
  -e K6_OUT="json=/results/gpu/k6_gpu_${TIMESTAMP}.json" \
  k6 run /scripts/load_test.js \
  --summary-export="/results/gpu/k6_summary_${TIMESTAMP}.json" || true

echo "[GPU 4/4] Collecting GPU metrics..."
"$SCRIPT_DIR/_collect_metrics.sh" "$RESULTS_DIR_GPU" "$TIMESTAMP" "gpu" "gpu-comparison"

# Stop GPU stack
docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" --profile gpu down -v 2>/dev/null || true

# --- Phase 3: Compare ---
echo ""
echo "=========================================="
echo "Phase 3: Comparison Report"
echo "=========================================="

if command -v python3 &>/dev/null; then
  python3 "$PERF_DIR/utils/compare_results.py" \
    --cpu-dir "$RESULTS_DIR_CPU" \
    --gpu-dir "$RESULTS_DIR_GPU" \
    --output "$PERF_DIR/reports/gpu_comparison_${TIMESTAMP}.json"
else
  echo "Python3 not found — skipping automated comparison."
  echo "CPU results: $RESULTS_DIR_CPU"
  echo "GPU results: $RESULTS_DIR_GPU"
fi

echo ""
echo "=== GPU vs CPU benchmark complete ==="