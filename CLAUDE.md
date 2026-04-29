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
- Presigned upload/download endpoints live at `POST /api/v1/dicom/presign-upload` and `GET /api/v1/dicom/presign-download`

## What's next

Ingestion flow:
1. Upload job tracking endpoints (`POST /uploads`, `GET /uploads/{job_id}`)
2. Celery task `ingest_dicom_instance`: download from MinIO → parse with pydicom → upsert Patient/Study/Series/Instance → generate thumbnail → update UploadJob status

## Known pitfalls

- `bitnami/minio:latest` manifest fails in GitHub Actions CI — MinIO service was removed from the test job entirely; tests needing storage must mock it
- pgAdmin rejects `.local` TLD emails — use a normal domain in `PGADMIN_EMAIL`
- ruff strips the `import app.db.models` in `alembic/env.py` as unused — it must stay (see conventions)
