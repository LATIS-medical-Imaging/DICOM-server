# dicom-server

FastAPI backend for the **DICOM Medical Imaging Platform**. This service handles 
authentication, patient/study/series/instance management, DICOM file storage,
annotations, sharing, and heavy image-processing jobs.

It consumes the companion scientific library
[`medical-image-std`](https://github.com/LATIS-DocumentAI-Group/medical-image-std) for
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
