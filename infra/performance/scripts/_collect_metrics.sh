#!/usr/bin/env bash
# Collect a metrics snapshot from Prometheus and docker stats
set -euo pipefail

RESULTS_DIR="${1:-.}"
TIMESTAMP="${2:-$(date +%Y%m%d_%H%M%S)}"
PROFILE="${3:-cpu}"
TEST_TYPE="${4:-unknown}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"

OUTPUT_FILE="$RESULTS_DIR/metrics_${TIMESTAMP}.json"

echo "  Collecting metrics snapshot -> $OUTPUT_FILE"

# Container stats snapshot
DOCKER_STATS=$(docker stats --no-stream --format '{"container":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","mem_pct":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}"}' 2>/dev/null | head -20 || echo '{}')

# Query Prometheus for key metrics
query_prom() {
  local query="$1"
  curl -sf "${PROMETHEUS_URL}/api/v1/query?query=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$query'))" 2>/dev/null || echo "$query")" 2>/dev/null || echo '{"status":"error"}'
}

CPU_USAGE=$(query_prom 'rate(container_cpu_usage_seconds_total{name=~".*api.*|.*worker.*"}[1m])')
MEM_USAGE=$(query_prom 'container_memory_usage_bytes{name=~".*api.*|.*worker.*|.*postgres.*"}')

# Build report
cat > "$OUTPUT_FILE" <<EOF
{
  "timestamp": "$TIMESTAMP",
  "test_type": "$TEST_TYPE",
  "profile": "$PROFILE",
  "docker_stats": [$(echo "$DOCKER_STATS" | paste -sd, -)],
  "prometheus": {
    "cpu_usage": $CPU_USAGE,
    "memory_usage": $MEM_USAGE
  }
}
EOF

echo "  Metrics collected: $OUTPUT_FILE"