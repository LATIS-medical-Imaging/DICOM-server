# Performance Testing Infrastructure

Production-grade benchmarking for the DICOM medical imaging backend.
Simulates a constrained VPS (2 CPU / 4 GB RAM) with realistic mammography workloads.

## Quick Start

```bash
# CPU load test
./infra/performance/scripts/run_load_test.sh cpu

# GPU load test (requires nvidia-container-toolkit)
./infra/performance/scripts/run_load_test.sh gpu

# Stress test
./infra/performance/scripts/run_stress_test.sh

# Soak test (15 min endurance)
./infra/performance/scripts/run_soak_test.sh

# Spike test (traffic bursts)
./infra/performance/scripts/run_spike_test.sh

# GPU vs CPU comparison
./infra/performance/scripts/run_gpu_benchmark.sh
```

## Docker Compose Profiles

```bash
# CPU profile (worker without GPU)
docker compose -f infra/performance/docker-compose.perf.yml --profile cpu up

# GPU profile (worker with NVIDIA runtime)
docker compose -f infra/performance/docker-compose.perf.yml --profile gpu up
```

## Monitoring

- **Grafana:** http://localhost:3000 (admin/admin)
- **Prometheus:** http://localhost:9090
- **cAdvisor:** http://localhost:8080

## Architecture

```
infra/performance/
├── docker-compose.perf.yml    # Full perf stack with CPU/GPU profiles
├── .env.perf                  # All configuration (resource limits, concurrency, etc.)
├── prometheus/                # Prometheus scrape config
├── grafana/                   # Dashboards and provisioning
├── locust/                    # Locust workflow tests (realistic user scenarios)
├── k6/                        # k6 throughput tests (load, stress, soak, spike)
├── scripts/                   # Shell orchestration scripts
├── utils/                     # Python report generation and comparison
├── results/                   # Raw benchmark outputs (cpu/, gpu/, load/, stress/, etc.)
├── reports/                   # Generated reports (JSON, CSV, Markdown)
├── datasets/                  # Mount point for DICOM datasets
└── logs/                      # Runtime logs
```

## Test Types

| Test | Purpose | Duration | VUs |
|------|---------|----------|-----|
| Load | Normal traffic baseline | 3 min | 5 |
| Stress | Find breaking point | 10 min | 5 → 75 |
| Soak | Detect memory leaks | 15 min | 3 |
| Spike | Test burst recovery | 5 min | 2 → 50 → 2 |

## Workloads

- **DICOM Upload:** presign → PUT to MinIO → create job → poll status
- **Metadata Retrieval:** list studies → study detail → series → instances
- **Inference:** apply medical filters (top_hat, kmeans, fcm, pfcm, febds, breast_mask)
- **Mixed:** browse + process in a single user flow

## Configuration

All settings in `.env.perf`:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_CPUS` | 0.8 | CPU limit for API container |
| `API_MEM_LIMIT` | 1024m | Memory limit for API |
| `WORKER_CPUS` | 0.6 | CPU limit for worker |
| `WORKER_MEM_LIMIT` | 1024m | Memory limit for worker |
| `DATASET_PATH` | (local path) | DICOM dataset directory to mount |
| `BENCHMARK_CONCURRENCY` | 5 | Number of concurrent users |
| `BENCHMARK_DURATION` | 60s | Test duration |
| `GPU_ENABLED` | false | Enable GPU mode |
| `CUDA_VISIBLE_DEVICES` | 0 | GPU device index |

## Reports

Generated automatically after each test:

```bash
# Generate reports from existing results
python infra/performance/utils/orchestrator.py --reports-only --test load --profile cpu

# Compare CPU vs GPU
python infra/performance/utils/compare_results.py \
  --cpu-dir infra/performance/results/cpu \
  --gpu-dir infra/performance/results/gpu \
  --output infra/performance/reports/comparison.json
```

## Dataset

Configure `DATASET_PATH` in `.env.perf` to point to your DICOM mammography dataset.
The default points to CBIS-DDSM (573 files, ~18 GB).

Files are discovered recursively and randomly sampled during upload benchmarks.

## Metrics Collected

| Category | Metrics |
|----------|---------|
| Latency | p50, p95, p99, avg, max |
| Throughput | requests/sec |
| Resources | CPU %, memory bytes, disk I/O, network I/O |
| Database | active connections |
| Cache | Redis memory |
| GPU | utilization, VRAM (when available) |
| Stability | container restarts, error rate |