You are a senior Backend Performance Engineer, MLOps Engineer, and Infrastructure Engineer.

Your task is to implement a complete production-grade performance testing and benchmarking infrastructure for an existing medical imaging backend project focused on mammography and microcalcification detection.

==================================================
PROJECT CONTEXT
==================================================

The project currently uses:
- Python
- uv (Astral package manager)
- pyproject.toml
- uv.lock
- Docker
- GPU-enabled inference support
- DICOM processing
- mammography detection workflows

The backbone framework (`medical-image-std`) is already installed through PyPI and referenced inside pyproject.toml. but you can check it inside `/home/bobmarley/PycharmProjects/medical-image-std/medical_image/`

You MUST:
- inspect pyproject.toml
- inspect uv.lock
- inspect Docker configuration
- inspect API routes
- inspect inference workflows
- inspect GPU handling
- inspect upload endpoints
- inspect worker/inference architecture
- adapt the benchmarking system to the existing implementation

==================================================
PACKAGE MANAGEMENT REQUIREMENTS
==================================================

The project uses:
- uv
- pyproject.toml
- uv.lock

You MUST:
- use uv everywhere
- preserve the existing dependency workflow
- avoid pip unless absolutely necessary

Examples:
- uv sync
- uv add locust
- uv add prometheus-client
- uv run python ...
- uv run locust ...

Do NOT introduce:
- poetry
- pipenv
- conda

==================================================
MAIN OBJECTIVE
==================================================

Implement a realistic production-like benchmarking environment to validate the backend before deployment.

The goal is to simulate:
- a cheap VPS deployment
- limited CPU and RAM
- realistic DICOM workloads
- concurrent mammography uploads
- inference-heavy traffic
- GPU vs CPU execution

The system must allow evaluating:
- throughput
- latency
- memory usage
- GPU acceleration benefits
- resource saturation
- stability over time
- deployment feasibility

==================================================
TARGET DEPLOYMENT TO SIMULATE
==================================================

Simulate a deployment similar to:
- 2 CPU cores
- 4 GB RAM

The performance environment will run on a dedicated workstation used to emulate production conditions.

==================================================
PERFORMANCE TEST TYPES
==================================================

Implement support for:

1. LOAD TEST
- Normal expected traffic
- Concurrent uploads and inference requests

2. STRESS TEST
- Progressively increase load until failure or saturation

3. ENDURANCE / SOAK TEST
- Long-running tests
- Detect memory leaks and degradation

4. SPIKE / PEAK TEST
- Sudden traffic bursts
- Observe recovery and queue behavior

==================================================
GPU VS CPU BENCHMARKING
==================================================

VERY IMPORTANT.

The framework supports GPU inference.

The benchmarking infrastructure MUST support:

A. CPU-only mode
- GPU disabled
- CUDA hidden

B. GPU-enabled mode
- NVIDIA runtime support
- CUDA enabled
- GPU metrics collection if available

The reports MUST clearly compare:
- CPU inference
- GPU inference

Measure:
- inference latency
- throughput
- VRAM usage
- GPU utilization
- RAM usage
- CPU usage

==================================================
DATASET SUPPORT
==================================================

The user will later provide local paths containing DICOM mammography studies.

`/home/bobmarley/PycharmProjects/micro-informed-vit/data`

The implementation must support:
- mounting external dataset directories
- recursive DICOM discovery
- randomized sampling
- configurable dataset paths
- configurable sampling strategies

Do NOT hardcode dataset paths.

==================================================
IMPLEMENTATION REQUIREMENTS
==================================================

==================================================
1. CREATE PERFORMANCE INFRASTRUCTURE
==================================================

Create a dedicated structure similar to:

infra/
└── performance/
    ├── docker-compose.perf.yml
    ├── .env.perf
    ├── grafana/
    ├── prometheus/
    ├── locust/
    ├── k6/
    ├── scripts/
    ├── logs/
    ├── reports/
    ├── results/
    ├── datasets/
    ├── utils/
    └── README.md

The implementation must be organized and maintainable.

==================================================
2. DOCKER PERFORMANCE ENVIRONMENT
==================================================

Implement a dedicated docker-compose.perf.yml.

The stack must include:
- existing API service
- PostgreSQL
- Redis if used
- inference workers if used
- Prometheus
- Grafana
- cAdvisor
- benchmark runners

The stack must support:
- CPU profile
- GPU profile

GPU mode must support:
- NVIDIA runtime
- CUDA visibility
- optional GPU metrics

==================================================
3. RESOURCE CONSTRAINT SIMULATION
==================================================

Simulate a realistic cheap VPS environment using Docker constraints.

Use:
- cpus
- mem_limit
- reservations

Distribute limits realistically across:
- API
- DB
- workers
- monitoring

Make limits configurable via:
- .env.perf

==================================================
4. LOAD TEST IMPLEMENTATION
==================================================

Implement BOTH:
- k6 benchmarks
- Locust workflows

Use:
- k6 for throughput benchmarking
- Locust for realistic workflows

==================================================
5. REALISTIC MEDICAL IMAGING WORKLOADS
==================================================

The benchmarks MUST target the REAL endpoints discovered from the existing backend.

Do NOT invent fake endpoints.

Automatically adapt to:
- upload endpoints
- inference endpoints
- metadata endpoints

Implement realistic workflows:

A. DICOM Upload Benchmark
- single uploads
- concurrent uploads
- large mammography studies

B. Inference Benchmark
- microcalcification detection
- preprocessing latency
- inference latency

C. Metadata Retrieval Benchmark
- list studies
- retrieve studies
- retrieve annotations

D. Mixed Workload Benchmark
- uploads + inference + retrieval together

==================================================
6. METRICS COLLECTION
==================================================

Implement observability using:
- Prometheus
- Grafana
- cAdvisor

Collect:
- CPU usage
- RAM usage
- disk I/O
- network I/O
- request latency
- inference latency
- DB metrics
- container restart count

If GPU available:
- GPU utilization
- VRAM usage

==================================================
7. AUTOMATED REPORT GENERATION
==================================================

VERY IMPORTANT.

At the end of each benchmark:
automatically generate reports.

Supported outputs:
- JSON
- CSV
- optional Markdown summary

Reports must include:
- timestamp
- benchmark type
- CPU/GPU mode
- concurrency
- duration
- throughput
- p50 latency
- p95 latency
- p99 latency
- error rate
- max RAM
- avg CPU
- GPU metrics
- failure count

Store results under:
results/

Example:
results/
  cpu/
  gpu/
  load/
  stress/
  soak/
  spike/

==================================================
8. BENCHMARK ORCHESTRATION
==================================================

Implement orchestration scripts such as:

- run_load_test.sh
- run_stress_test.sh
- run_soak_test.sh
- run_spike_test.sh
- run_gpu_benchmark.sh

Also implement a Python orchestration utility that:
1. starts the stack
2. waits for readiness
3. runs benchmarks
4. collects metrics
5. exports reports
6. stops services cleanly

==================================================
9. RESULT AGGREGATION
==================================================

Implement Python utilities that:
- parse benchmark outputs
- aggregate metrics
- compare CPU vs GPU
- generate summary JSON

Optional:
- matplotlib charts

==================================================
10. HEALTHCHECKS
==================================================

Implement:
- readiness checks
- retry logic
- service health validation
- benchmark startup synchronization

==================================================
11. CONFIGURABILITY
==================================================

Everything must be configurable:
- dataset path
- concurrency
- duration
- upload count
- inference count
- GPU mode
- CPU limits
- RAM limits

Use environment variables and configuration files.

==================================================
12. ENGINEERING QUALITY
==================================================

Requirements:
- production quality
- structured logging
- clean architecture
- type hints where useful
- proper error handling
- reproducibility
- maintainability

==================================================
13. IMPORTANT BENCHMARKING REQUIREMENTS
==================================================

This is NOT a simple JSON API benchmark.

The implementation must realistically account for:
- large DICOM payloads
- mammography image sizes
- preprocessing cost
- inference bottlenecks
- disk I/O pressure
- DB pressure
- concurrent uploads
- memory-intensive operations

==================================================
14. IMPORTANT OUTPUT REQUIREMENTS
==================================================

The final implementation must allow commands like:

CPU benchmark:
docker compose -f infra/performance/docker-compose.perf.yml --profile cpu up

GPU benchmark:
docker compose -f infra/performance/docker-compose.perf.yml --profile gpu up

Run load test:
./infra/performance/scripts/run_load_test.sh

Run stress test:
./infra/performance/scripts/run_stress_test.sh

Run soak test:
./infra/performance/scripts/run_soak_test.sh

Run spike test:
./infra/performance/scripts/run_spike_test.sh

==================================================
15. IMPORTANT FINAL GOAL
==================================================

The implementation must allow the user to answer real deployment questions such as:

- How many concurrent mammography uploads can the backend handle?
- What is the inference throughput on CPU vs GPU?
- What is the memory usage under prolonged load?
- Does the system leak memory?
- What is the system breaking point?
- Is a 2-core / 4GB VPS sufficient?
- Should inference be isolated into dedicated workers?
- What is the performance gain from GPU acceleration?

The implementation must provide real engineering insight and realistic production benchmarking.