# Architecture decisions

## DICOM serving — two channels, no PNG conversion
- Metadata (study/series/instance fields) → JSON via FastAPI REST
- Pixel data → browser fetches .dcm directly from MinIO via presigned GET URL
- Never convert DICOM to PNG/JPEG for viewer delivery — lossy, clinically unsafe
- Thumbnails (JPEG) are acceptable only for study-list previews, stored in `thumbnails` bucket

## MinIO presigned URLs
- Internal Docker hostname (`minio:9000`) must be rewritten to `MINIO_EXTERNAL_ENDPOINT` before returning URLs to the browser
- `_to_external_url()` in `StorageService` handles this — always call it on presigned URLs
- `MINIO_EXTERNAL_ENDPOINT` is env-driven so staging/prod can override it

## Database connections — two DSNs
- App (FastAPI, async): connects through **PgBouncer** on port 6432 via `database_url_async`
- Alembic (sync migrations): connects **directly to postgres** on port 5432 via `database_url_sync` — bypasses PgBouncer because DDL + transaction pooling don't mix

## Object key layout
`{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm` — defined in `StorageService.dicom_object_key()`

## Celery
- `task_acks_late=True` + `worker_prefetch_multiplier=1` — tasks are not acknowledged until complete, one at a time per worker
- Tasks auto-discovered from `app.workers.tasks`
