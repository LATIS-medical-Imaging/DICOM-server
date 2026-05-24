# CLAUDE.md

FastAPI backend for a DICOM medical imaging platform (ENISo PFE 2026).  
Stack: FastAPI · async SQLAlchemy 2.0 · PostgreSQL 16 · PgBouncer · MinIO · Celery + Redis.

@.claude/rules/architecture.md
@.claude/rules/conventions.md

---

## Current state

### Infrastructure
- Full Docker stack (api, worker, postgres, pgbouncer, redis, minio, minio-init, pgadmin)
- GitHub Actions CI: lint → test → docker → security
- `make ci` mirrors CI locally — all targets run in Docker, no local Python needed
- Railway hosts api + worker + postgres + redis; Angular viewer on Cloudflare Pages
- Cloudflare R2 replaces MinIO in production (S3-compatible, no code change beyond endpoint env vars)

### Database
- Three Alembic migrations:
  - `0001_initial_schema` — all core ORM models (User, Patient, Study, Series, Instance, UploadJob, Share, etc.)
  - `0002_fix_patient_uniqueness` — drops `UNIQUE(patient_id)`, adds `UNIQUE(patient_id, created_by)` to prevent race-condition duplicates when concurrent Celery tasks ingest anonymous DICOM files
  - `0003_chat_tables` — `friendships` and `messages` tables with all indexes and constraints
- `0004_series_phases` — adds `parent_series_id` (self-FK CASCADE) and `owner_id` (FK SET NULL) to `series` plus partial indexes; enables the Phases feature without any new tables

### Authentication (`app/services/auth_service.py`, `app/api/v1/endpoints/auth.py`)
- JWT (HS256) access tokens (15 min) + refresh tokens (7 days) via `app/core/security.py`
- Argon2id password hashing (`argon2-cffi`) with configurable cost parameters
- Refresh-token rotation with family-level reuse detection — a reused refresh token nukes the whole family
- `GET /auth/me` · `POST /auth/login` · `POST /auth/refresh` · `POST /auth/logout`
- `CurrentUser` dependency in `app/api/deps.py` — decodes Bearer JWT, looks up active user
- Admin seeding: `docker compose exec api python -m app.cli.seed_admin` (reads credentials from `.env`)

### Ingestion flow
- `POST /api/v1/presign/upload` — returns presigned PUT URL for direct browser→MinIO upload
- `POST /api/v1/uploads` — creates UploadJob row, enqueues `ingest_dicom_instance` Celery task
- `GET  /api/v1/uploads/{job_id}` — polls ingestion progress
- `IngestionService` — upserts Patient/Study/Series/Instance using `INSERT … ON CONFLICT DO NOTHING` to handle concurrent ingestion of the same patient (anonymous DICOM `patient_id='0'` race)
- `UploadService` — creates UploadJob and enqueues task
- Thumbnail generation: JPEG stored in `thumbnails` bucket under same key structure

### Metadata read endpoints
- `GET /api/v1/studies` — list studies for the authenticated user (owner derived from JWT)
- `GET /api/v1/studies/{study_id}` — single study
- `GET /api/v1/studies/{study_id}/series` — series ordered by series_number
- `GET /api/v1/studies/{study_id}/series/{series_id}/instances` — instances ordered by instance_number
- `GET /api/v1/presign/download` — presigned GET URL for direct MinIO→browser download

### Image processing
- `POST /api/v1/processing/apply` runs heavy algorithms only — cheap pixel ops (blur/contrast/brightness/sharpen) are CSS `filter` rules in the viewer
- Supported: `top_hat`, `kmeans`, `fcm`, `pfcm`, `febds`, `breast_mask` — all from `medical-image-std`
- `ProcessingService` downloads source DICOM, runs algorithm, writes derived DICOM under a content-addressed key (`{owner}/{study}/{series}/derived/{sop}--{filter}-{hash}.dcm`)
- Same `{filter, params}` hits the cached object — repeat calls return the existing presigned URL

### Series Phases — saved modifications (`app/services/phase_service.py`, `app/api/v1/endpoints/phases.py`)
- A **Phase** is a doctor-saved snapshot of modifications (filter results + annotations) on a parent series. Implemented as another `series` row with `parent_series_id` set + `owner_id` set — no new tables, just an additive column change in migration `0004_series_phases`
- `Phase` Instance rows hold only the *touched* slices (filtered or annotated); each row points at the derived blob (filter) or at the parent's blob (annotation-only) — never duplicating bytes in MinIO
- `GET /studies/{}/series/{}/instances` is **phase-aware but symmetric**: serves the merged stack (parent + phase overrides, ordered by `instance_number`) when called on a phase; the frontend doesn't branch
- Annotations are wired up for the first time as part of this work. Existing `annotations` table is used unchanged — phase annotations attach to phase-owned Instance rows via the existing `instance_id` FK
- Endpoints: `GET /series/{id}/phases`, `POST /series/{id}/phases`, `GET /phases/{id}`, `PATCH /phases/{id}` (in-place save), `DELETE /phases/{id}`
- Save scope: server filter result + annotation list per touched slice. Display state (W/L, zoom, rotation, …) is **not persisted**
- Phases are **private to their creator**: every read is scoped `WHERE owner_id = current_user.id` in addition to the parent-study visibility check
- `pydicom.uid.generate_uid()` generates synthetic `SOPInstanceUID` / `SeriesInstanceUID` for phase rows (acceptable for an internal viewer; we're not exporting back to a PACS)
- In-place PATCH is atomic delete-then-insert: the existing phase Instance rows are hard-deleted (cascading their annotations via FK) and the new set is inserted in one transaction
- Derived blobs in MinIO are **never deleted** when a phase is removed — they're content-addressed and may belong to other phases

### Chat module (`app/api/v1/endpoints/`, `app/services/chat_service.py`, `app/services/ws_hub.py`)
- **Friendships** — `POST /friendships/invite`, `GET /friendships`, `POST /friendships/{id}/accept`, `DELETE /friendships/{id}` (reject / unfriend)
- **Messages** — `POST /messages`, `GET /messages?with=<peer_id>` (auto mark-read, cursor-paginated), `GET /messages/conversations`, `GET /messages/unread-count`
- **User search** — `GET /users/search?q=&limit=` returns active doctors only, excludes caller
- **WebSocket gateway** — `WS /api/v1/ws/chat?ticket=<ticket>` (see WS ticket section below)
- `ChatService` — all business logic; enforces friendship-gated messaging, canonical pair ordering for uniqueness
- `WebSocketHub` (`app/services/ws_hub.py`) — in-process registry mapping `user_id → set[WebSocket]`; `deliver()` fans out to all open tabs; protected by `asyncio.Lock()`

### WebSocket ticket security (`app/api/v1/endpoints/ws_ticket.py`, `app/core/redis.py`)
- `POST /api/v1/ws-ticket` — authenticated (Bearer JWT in header, never in URL); generates a `secrets.token_hex(32)` (256-bit random), stores `ws_ticket:<token> → user_id` in Redis with 30s TTL, returns `{ ticket }`
- WS gateway calls `GETDEL ws_ticket:<ticket>` — atomically fetches and deletes; expired/consumed/unknown tickets close with `1008`
- Real JWT never appears in any WebSocket URL or server access log
- `app/core/redis.py` — async Redis client singleton (`redis.asyncio`); closed during app lifespan shutdown

### Admin panel (`app/api/v1/endpoints/admin.py`)
- `GET /admin/users` — list all users (admin only)
- `POST /admin/users` — create user with role
- `PATCH /admin/users/{id}` — update profile (name, title, specialty, institution, phone)
- `POST /admin/users/{id}/reset-password` — reset to a new password
- `DELETE /admin/users/{id}` — soft-deactivate

## What's next

- Persist the applied filter on the `Instance` row so it reapplies on load (the "save filter" feature)
- Single endpoint returning all series + presigned URLs per study to eliminate the viewer's N+1 round-trips
- Unit and integration tests for `AuthService`, `ChatService`, `IngestionService`
- Redis pub/sub bridge inside `WebSocketHub` for horizontal API scaling (current hub is in-process; the `register`/`unregister`/`deliver` interface is designed so callers won't change)

## Known pitfalls

- `bitnami/minio:latest` manifest fails in GitHub Actions CI — MinIO service removed from test job; tests needing storage must mock `StorageService`
- pgAdmin rejects `.local` TLD emails — use a real domain in `PGADMIN_EMAIL`
- ruff strips `import app.db.models` in `alembic/env.py` as unused — it must stay (autogenerate target)
- After changing `pyproject.toml`, run `make build-tools` to rebuild the tools image
- `medical-image-std` pulls torch (CPU wheel in prod) — cache `pip install` layers carefully in Docker
- Filter results live under `derived/` inside the same bucket as the source DICOM — never overwrite the source key
- PgBouncer transaction pooling: DDL statements must target Postgres directly (port 5432), not PgBouncer (6432) — Alembic's `database_url_sync` is wired correctly for this
- Concurrent DICOM ingestion of anonymous files (`patient_id='0'`) would previously race-insert the same patient row. Fixed in migration `0002` + `INSERT … ON CONFLICT DO NOTHING` in `IngestionService._upsert_patient`
- FastAPI ≥ 0.115 with `Annotated[T, Query(...)]`: do **not** put the default value inside `Query()` — set it with `= <default>` on the parameter instead, or FastAPI raises `AssertionError` at startup
- Phase creation must **not** bump the parent study's aggregate counters (`total_series_count`, `total_instance_count`, `total_size_bytes`) — those describe DICOM-ingested content only
- `StudyService.list_series` filters `parent_series_id IS NULL` so the sidebar's top-level series list isn't polluted with every doctor's phases. Phases come from the separate `GET /series/{parent_id}/phases` endpoint
- Deleting a phase does **not** delete its derived blobs in MinIO — they're content-addressed under `derived/` and may be shared with other phases (same filter + params hash). A future maintenance task can sweep orphans
