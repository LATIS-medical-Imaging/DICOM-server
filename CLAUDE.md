# CLAUDE.md

FastAPI backend for a DICOM medical imaging platform (ENISo PFE 2026).
Stack: FastAPI · async SQLAlchemy 2.0 · PostgreSQL 16 · PgBouncer · MinIO · Celery + Redis.

@.claude/rules/architecture.md
@.claude/rules/conventions.md

---

## Current state

Infrastructure complete:
- Full Docker stack (api, worker, postgres, pgbouncer, redis, minio, minio-init, pgadmin)
- GitHub Actions CI: lint → test → docker → security
- `make ci` mirrors CI locally — all targets run in Docker, no local Python needed
- Initial Alembic migration covers all 10 ORM models

Ingestion flow complete:
- `POST /api/v1/presign/upload` — returns presigned PUT URL for direct browser→MinIO upload
- `GET  /api/v1/presign/download` — returns presigned GET URL for direct MinIO→browser download
- `POST /api/v1/uploads` — creates UploadJob row, enqueues `ingest_dicom_instance` Celery task
- `GET  /api/v1/uploads/{job_id}` — polls ingestion progress
- `IngestionService` — upserts Patient/Study/Series/Instance, generates JPEG thumbnail
- `UploadService` — creates UploadJob and enqueues task

Metadata read endpoints complete:
- `GET /api/v1/studies?owner_id=...` — list studies (excludes soft-deleted, newest first)
- `GET /api/v1/studies/{study_id}` — single study
- `GET /api/v1/studies/{study_id}/series` — series ordered by series_number
- `GET /api/v1/studies/{study_id}/series/{series_id}/instances` — instances ordered by instance_number
- `StudyService` — all read queries

## What's next

- Wire up the Angular viewer against these endpoints

## Known pitfalls

- `bitnami/minio:latest` manifest fails in GitHub Actions CI — MinIO service removed from test job; tests needing storage must mock it
- pgAdmin rejects `.local` TLD emails — use a normal domain in `PGADMIN_EMAIL`
- ruff strips `import app.db.models` in `alembic/env.py` as unused — it must stay (see conventions)
- After changing `pyproject.toml`, run `make build-tools` to rebuild the tools image
