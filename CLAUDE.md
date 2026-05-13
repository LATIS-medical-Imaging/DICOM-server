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

Deployment complete:
- Railway hosts api + worker + postgres + redis; the Angular viewer runs on Cloudflare Pages
- Cloudflare R2 replaced MinIO in production (S3-compatible, no code change beyond endpoint env vars)

Image processing:
- `POST /api/v1/processing/apply` runs **heavy algorithms only** — the cheap pixel ops (blur, contrast, brightness, sharpen) live in the viewer as CSS `filter` rules, no round-trip
- Supported: `top_hat`, `kmeans`, `fcm`, `pfcm`, `febds`, `breast_mask` — all from `medical-image-std`
- `ProcessingService` downloads source DICOM, runs the algorithm on CPU, writes the derived DICOM back under a content-addressed key (`{owner}/{study}/{series}/derived/{sop}--{filter}-{hash}.dcm`)
- Same `{filter, params}` hits the cached object; repeat calls return the existing presigned URL
- `params` is `dict[str, Any]` so it accepts strings (`febds.method="dog"`), bools (`breast_mask.mask_only`), and numbers across the same shape

## What's next

- Persist the applied filter on the `Instance` row so it's reapplied on load (the "save" feature)
- Auth (JWT + Argon2) — `app/core/security.py` already has helpers
- Single endpoint returning all series + presigned URLs per study to kill the viewer's N+1

## Known pitfalls

- `bitnami/minio:latest` manifest fails in GitHub Actions CI — MinIO service removed from test job; tests needing storage must mock it
- pgAdmin rejects `.local` TLD emails — use a normal domain in `PGADMIN_EMAIL`
- ruff strips `import app.db.models` in `alembic/env.py` as unused — it must stay (see conventions)
- After changing `pyproject.toml`, run `make build-tools` to rebuild the tools image
- `medical-image-std` pulls torch (CPU wheel in prod) — the image is large; cache `pip install` layers carefully
- Filter results are stored under `derived/` inside the same bucket as the source DICOM — never overwrite the source key
