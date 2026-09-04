# dicom-server

FastAPI backend for the **DICOM Medical Imaging Platform**. This service handles 
authentication, patient/study/series/instance management, DICOM file storage,
annotations, sharing, and heavy image-processing jobs.

It consumes the companion scientific library
[`medical-image-std`](https://pypi.org/project/medical-image-std/) (GitHub repo: https://github.com/LATIS-DocumentAI-Group/medical-image-std) for
GPU-accelerated algorithms, and is paired with an Angular 19 + CornerstoneJS frontend
(`dicom-viewer`).

---

## Table of Contents

- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [Database & Migrations](#database--migrations)
- [Background Workers](#background-workers)
- [Object Storage](#object-storage)
- [API Documentation](#api-documentation)
- [Development Workflow](#development-workflow)
- [Testing](#testing)
- [Project Conventions](#project-conventions)

---

## Architecture

```
                   ┌────────────────┐
 Angular 19  ───▶    FastAPI (api)    ◀── JSON / OpenAPI
                   └───────┬────────┘
                           │
          ┌────────────────┼───────────────────┐
          ▼                ▼                   ▼
     PgBouncer        Redis 7            MinIO (S3)
          │          (cache, pubsub,      (DICOM + thumbnails)
          ▼           Celery broker)
     PostgreSQL 16
          ▲
          │
     ┌────┴────┐
     │ worker  │  Celery — DICOM ingestion + medical-image-std jobs
     └─────────┘
```

All services run in a single `docker-compose` stack. The API and worker share the
same image built from a multi-stage `Dockerfile`.

---

## Tech Stack

| Layer          | Choice                       | Notes                                        |
|----------------|------------------------------|----------------------------------------------|
| Web framework  | **FastAPI**                  | Async, auto-OpenAPI, Pydantic v2             | 
| ORM            | **SQLAlchemy 2.0** (async)   | `asyncpg` driver via PgBouncer               | 
| Migrations     | **Alembic**                  | Sync driver (`psycopg2`) directly on Postgres|
| Database       | **PostgreSQL 16**            | JSONB, GIN indexes, partitioned audit log    |
| Pool           | **PgBouncer**                | Transaction mode                             |
| Cache / PubSub | **Redis 7**                  | Also Celery broker & result backend          |
| Object store   | **MinIO**                    | S3-compatible, pre-signed URLs               |
| Background jobs| **Celery**                   | Heavy DICOM + processing workloads           | 
| Auth           | **JWT** + **Argon2id**       | Access + refresh with token-family rotation  |
| Validation     | **Pydantic v2**              | Runtime + static type safety                 |
| Logging        | **structlog**                | JSON in production, pretty in dev            |
| Tooling        | **Ruff, Black, Mypy, Pytest**| Strict type checking, 100-col line length    |

---

## Project Structure

```
dicom-server/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI factory + lifespan
│   ├── core/                      # config, security, logging, exceptions
│   │   ├── config.py              # Pydantic Settings
│   │   ├── security.py            # Argon2 + JWT
│   │   ├── logging.py             # structlog wiring
│   │   └── exceptions.py          # Domain errors + handlers
│   ├── db/
│   │   ├── base.py                # DeclarativeBase + mixins
│   │   ├── session.py             # Async engine + SessionLocal
│   │   └── models/                # 10 ORM models (users, studies, …)
│   ├── schemas/                   # Pydantic request/response models
│   ├── api/
│   │   ├── deps.py                # FastAPI dependencies
│   │   └── v1/
│   │       ├── router.py          # Aggregator
│   │       └── endpoints/         # Route modules
│   ├── services/                  # Business logic (no FastAPI imports)
│   ├── workers/
│   │   ├── celery_app.py          # Celery factory
│   │   └── tasks/                 # Task modules
│   └── middleware/                # Request-ID, etc.
├── alembic/                       # Migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
├── tests/
├── Dockerfile                     # Multi-stage, shared api+worker
├── docker-compose.yml             # Full local stack
├── alembic.ini
├── pyproject.toml
├── .env.example
└── README.md
```

## Getting Started

### Prerequisites

- Docker 24+ and Docker Compose v2
- (Optional, for local dev without Docker) Python 3.11+

### Quick start — Docker stack

```bash
cp .env.example .env
# edit .env and set a strong JWT_SECRET_KEY

docker compose up -d --build
docker compose exec api alembic upgrade head
```

Endpoints:

| Service       | URL                                       |
|---------------|-------------------------------------------|
| API           | http://localhost:8000                     |
| OpenAPI       | http://localhost:8000/api/docs            |
| ReDoc         | http://localhost:8000/api/redoc           |
| Liveness      | http://localhost:8000/api/v1/health/live  |
| Readiness     | http://localhost:8000/api/v1/health/ready |
| MinIO console | http://localhost:9001                     |

### Seed the first admin account

There is no open registration endpoint — only admins can create user accounts.
Add the following variables to your `.env` file before running the seed command:

```env
ADMIN_BOOTSTRAP_EMAIL=admin@example.com      # required
ADMIN_BOOTSTRAP_PASSWORD=changeme123         # required, min 12 chars
ADMIN_BOOTSTRAP_FIRST_NAME=Admin             # optional, defaults to "Admin"
ADMIN_BOOTSTRAP_LAST_NAME=User               # optional, defaults to "User"
```

Then run:
```bash
docker compose exec api python -m app.cli.seed_admin
```

Stop the stack:

```bash
docker compose down           # keep volumes
docker compose down -v        # wipe postgres + minio data
```

### Local dev (without Docker)

```bash
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# point .env to localhost services instead of container names
cp .env.example .env
# POSTGRES_HOST=localhost  POSTGRES_PORT=5432
# REDIS_HOST=localhost
# MINIO_ENDPOINT=localhost:9000

alembic upgrade head
uvicorn app.main:app --reload
```

---

## Development Workflow

Every check runs **inside Docker** — no local Python install required. The first
run builds the tools image (~1 min); subsequent runs are fast.

```bash
make build-tools   # one-time (rerun after pyproject.toml changes)
make ci            # mirrors GitHub CI: lint + format + types + tests
make fix           # auto-repair lint + formatting issues
```

---

## Running the Full Platform

The platform is two repositories: this API and the Angular viewer
(`DICOM-viewer`, served separately). Bring the backend up first — the viewer
expects it on `http://localhost:8000`.

### 1. Backend

```bash
cd DICOM-server
cp .env.example .env          # set JWT_SECRET_KEY and the ADMIN_BOOTSTRAP_* vars

make up                       # builds and starts every service
docker compose exec api alembic upgrade head
docker compose exec api python -m app.cli.seed_admin
```

`make up` probes the host and starts the GPU-enabled stack when there is a GPU
*and* the NVIDIA container runtime; otherwise it starts the CPU stack. Force
either with `make up-gpu` / `make up-cpu`, and check what the container actually
resolved with `make gpu-check` — the host having a GPU is not the same as the
container getting one.

Services started:

| Container            | Role                                                    |
|----------------------|---------------------------------------------------------|
| `dicom-api`          | FastAPI app                                             |
| `dicom-worker`       | Celery worker — DICOM ingestion (`default` queue)       |
| `dicom-pixel-worker` | Celery worker — filters + segmentation (`pixels` queue) |
| `dicom-postgres`     | PostgreSQL 16                                           |
| `dicom-pgbouncer`    | Connection pooler (app connects through this)           |
| `dicom-redis`        | Celery broker, WS tickets, pixel-job state              |
| `dicom-minio`        | Object storage (`dicom-files`, `thumbnails` buckets)    |
| `dicom-pgadmin`      | Optional DB UI on :5050                                 |

The two Celery workers are deliberately separate: a segmentation run of tens of
seconds must not queue in front of a DICOM upload.

### 2. Viewer

```bash
cd ../DICOM-viewer
docker compose up -d --build          # http://localhost:4200
```

Log in with the `ADMIN_BOOTSTRAP_EMAIL` / `ADMIN_BOOTSTRAP_PASSWORD` you seeded.

### 3. Verify

```bash
curl http://localhost:8000/api/v1/health/ready
docker compose logs -f api worker pixel-worker
```

Open http://localhost:8000/api/docs for the live API reference.

### Stopping

```bash
make down                     # keep data
docker compose down -v        # wipe postgres + minio volumes
```

### Tuning knobs

All optional — the defaults work out of the box.

| Variable                           | Default   | What it does                                                              |
|------------------------------------|-----------|---------------------------------------------------------------------------|
| `UVICORN_WORKERS`                  | `2`       | API processes. Each carries its own torch runtime (~1–1.5 GB resident)    |
| `TORCH_NUM_THREADS`                | `0`       | `0` = torch's default. Set to the container's real vCPU quota             |
| `DEEP_SEGMENTATION_DEVICE`         | `auto`    | `auto` / `cpu` / `cuda`                                                   |
| `DEEP_SEGMENTATION_PRELOAD_MODELS` | *(empty)* | Comma-separated checkpoints loaded at boot so the first request is fast   |
| `CELERY_PIXEL_QUEUE`               | `pixels`  | Queue served by `dicom-pixel-worker`                                      |

### Troubleshooting

| Symptom                                    | Cause and fix                                                                                     |
|--------------------------------------------|---------------------------------------------------------------------------------------------------|
| First segmentation takes 10–30 s extra      | Cold start: torch import + checkpoint download. Set `DEEP_SEGMENTATION_PRELOAD_MODELS`             |
| `make gpu-check` says `resolved: cpu`       | Either no GPU passed through or a CPU-only torch build. `make up-gpu` rebuilds against CUDA wheels |
| Filter/segmentation never returns           | Check `docker compose logs pixel-worker` — the job runs there, not in the API                     |
| Upload stuck on "Processing on server 0 / N" | The ingestion worker is down. `docker compose ps` — `dicom-worker` must be up and healthy; `docker compose up -d worker` restarts it. Queued tasks drain as soon as it returns |
| `PermissionError` on the checkpoint cache   | Stale root-owned volume: `docker volume rm dicom-platform_model-cache`, then `make up`             |
