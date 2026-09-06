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
- I am going to host it manually on a virtual private server for now
### Database
- Three Alembic migrations:
  - `0001_initial_schema` — all core ORM models (User, Patient, Study, Series, Instance, UploadJob, Share, etc.)
  - `0002_fix_patient_uniqueness` — drops `UNIQUE(patient_id)`, adds `UNIQUE(patient_id, created_by)` to prevent race-condition duplicates when concurrent Celery tasks ingest anonymous DICOM files
  - `0003_chat_tables` — `friendships` and `messages` tables with all indexes and constraints
- `0004_series_phases` — adds `parent_series_id` (self-FK CASCADE) and `owner_id` (FK SET NULL) to `series` plus partial indexes; enables the Phases feature without any new tables
- `0005_shares_messaging` · `0006_scope_dicom_uniqueness`
- `0007_instance_parent_link` — adds `instances.parent_instance_id` (self-FK CASCADE, partial index) so a phase row records which parent slice it overrides; backfills the rows the old `instance_number` match could resolve
- `0008_message_voice` — adds `messages.voice_object_key` / `voice_mime_type` / `voice_duration_ms` / `voice_size_bytes` plus a CHECK keeping the four all-null or all-set

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
- **Cache-first, queue-second.** `POST /processing/apply` and `POST /processing/segmentation/apply` return **200** with the finished result when the derived object already exists, and **202 + `{job_id}`** when it has to be computed. Follow a queued job over the WebSocket (`filter.*` / `segmentation.*`) or by polling `GET /processing/jobs/{job_id}`
- Heavy work runs on the `pixels` Celery queue (`dicom-pixel-worker`, concurrency 1), never in the API process
- Pixel-job state lives in Redis under `procjob:<id>` with a 1 h TTL (`app/services/job_store.py`) — no table, because the result is a content-addressed key that can always be recomputed. A 404 from the poll endpoint means "expired or never existed"; re-issue the apply and it will hit the cache
- Presigned URLs are minted on read, never stored in the job record
- Every run logs per-stage timings (`ms_download`, `ms_algorithm`, `ms_encode`, `ms_upload`, `ms_total`) via `app/core/timing.py`
- `POST /api/v1/processing/apply` runs heavy algorithms only — cheap pixel ops (blur/contrast/brightness/sharpen) are CSS `filter` rules in the viewer
- Supported: `top_hat`, `kmeans`, `fcm`, `pfcm`, `febds`, `breast_mask` — all from `medical-image-std`
- Every tunable constructor argument is forwarded from the request's free-form `params` (`kmeans`: `max_iter`/`tol`; `fcm`: `m`/`max_iter`/`tol`; `pfcm`: `m`/`eta`/`a`/`b`/`tau`/`max_iter`), defaulting to the algorithm classes' own defaults
- `ProcessingService` downloads source DICOM, runs algorithm, writes derived DICOM under a content-addressed key (`{owner}/{study}/{series}/derived/{sop}--{filter}-{hash}.dcm`)
- Same `{filter, params}` hits the cached object — repeat calls return the existing presigned URL

### Series Phases — saved modifications (`app/services/phase_service.py`, `app/api/v1/endpoints/phases.py`)
- A **Phase** is a doctor-saved snapshot of modifications (filter results + annotations) on a parent series. Implemented as another `series` row with `parent_series_id` set + `owner_id` set — no new tables, just an additive column change in migration `0004_series_phases`
- Each phase Instance row records the slice it overrides in `parent_instance_id`; the merge in `list_instances_rendered` keys on that, **not** on `instance_number`
- `Phase` Instance rows hold only the *touched* slices (filtered or annotated); each row points at the derived blob (filter) or at the parent's blob (annotation-only) — never duplicating bytes in MinIO
- `GET /studies/{}/series/{}/instances` is **phase-aware but symmetric**: serves the merged stack (parent + phase overrides, ordered by `instance_number`) when called on a phase; the frontend doesn't branch
- Annotations are wired up for the first time as part of this work. Existing `annotations` table is used unchanged — phase annotations attach to phase-owned Instance rows via the existing `instance_id` FK
- Endpoints: `GET /series/{id}/phases`, `POST /series/{id}/phases`, `GET /phases/{id}`, `PATCH /phases/{id}` (in-place save), `DELETE /phases/{id}`
- Save scope: server filter result + annotation list per touched slice. Display state (W/L, zoom, rotation, …) is **not persisted**
- Phases are **private to their creator**: every read is scoped `WHERE owner_id = current_user.id` in addition to the parent-study visibility check
- `pydicom.uid.generate_uid()` generates synthetic `SOPInstanceUID` / `SeriesInstanceUID` for phase rows (acceptable for an internal viewer; we're not exporting back to a PACS)
- In-place PATCH is atomic delete-then-insert: the existing phase Instance rows are hard-deleted (cascading their annotations via FK) and the new set is inserted in one transaction
- Derived blobs in MinIO are **never deleted** when a phase is removed — they're content-addressed and may belong to other phases

### Deep segmentation (`app/services/segmentation_service.py`)
- `GET  /api/v1/processing/segmentation/models` — checkpoints advertised by the model server (`DEEP_SEGMENTATION_MODEL_SERVER_URL`, default `http://mcdmodels.ptm.tn:555/`); cached in-process for 5 min, unreachable server → clean 502
- `POST /api/v1/processing/segmentation/apply` — runs `DeepSegmentationAlgorithm` (medical-image-std ≥ 0.7.0) over one instance; returns the derived mask URL plus one `LesionAnnotation` per detected lesion
- Same authorization as `/processing/apply` (owner or active share with ANNOTATE/MANAGE) — both routes share `_authorize_pixel_write`
- Loaded checkpoints are cached **per worker process**, keyed by `model@device` (a load costs a download + `torch.load`; the device is in the key so a CUDA-OOM fallback can hold a CPU copy alongside the GPU one). `DEEP_SEGMENTATION_PRELOAD_MODELS` warms them at boot
- Cache hits need more than the mask, so a `<derived-key>.json` sidecar holding `{lesion_count, annotations}` is written next to it and read back — identical request → `cached: true`, no re-inference
- Inference runs on the `pixels` Celery worker. The service's async `apply_segmentation` still exists for inline callers and hops to `asyncio.to_thread`; the blocking core is `apply_to_key`, which takes a source object key so the task needs no second DB round-trip
- A `<derived-key>.png` sidecar holds the mask as a few-KB PNG (`mask_png_url` in the response, null for masks computed before this existed). **This is now the viewer's primary path**: it composites the binary / heat-map / overlay display modes from the PNG plus the original slice, and only falls back to downloading the full-range uint16 DICOM when the sidecar is missing
- Lesions are **not** written to the `annotations` table. They return in the response and are persisted only if the doctor saves a Phase, via the existing pipeline. No new migration
- `app/services/derived_pixels.py` — the plumbing `ProcessingService` and `SegmentationService` share (`FilterError`, `load_instance`, `clamp_roi`, `rescale_to_dtype`, `DERIVED_PREFIX`)
- Checkpoints persist across restarts via the `model-cache` named volume on the `api` service

### Chat module (`app/api/v1/endpoints/`, `app/services/chat_service.py`, `app/services/ws_hub.py`)
- **Friendships** — `POST /friendships/invite`, `GET /friendships`, `POST /friendships/{id}/accept`, `DELETE /friendships/{id}` (reject / unfriend)
- **Messages** — `POST /messages`, `GET /messages?with=<peer_id>` (auto mark-read, cursor-paginated), `GET /messages/conversations`, `GET /messages/unread-count`
- **User search** — `GET /users/search?q=&limit=` returns active doctors only, excludes caller
- **Voice messages** — `POST /messages/voice/presign` returns a presigned PUT into the `voice-messages` bucket (key `{sender_id}/{uuid4}.{ext}`); the browser uploads the recording directly, then `POST /messages` with `voice: {object_key, mime_type, duration_ms}` attaches it. `MessageResponse.voice` carries a presigned GET minted on every read. Audio bytes never pass through the API — same browser↔MinIO split as DICOM pixels. A voice note may carry a text caption or stand alone
- **WebSocket gateway** — `WS /api/v1/ws/chat?ticket=<ticket>` (see WS ticket section below)
- `ChatService` — all business logic; enforces friendship-gated messaging, canonical pair ordering for uniqueness
- `WebSocketHub` (`app/services/ws_hub.py`) — `deliver()` publishes to the `ws:notifications` Redis channel; every process forwards what it reads there into its own `deliver_local()`, which writes to the sockets that process holds. This is what makes `UVICORN_WORKERS > 1` safe — a socket on worker 2 still receives what worker 1 sends. Falls back to local delivery if Redis is unreachable

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

- Stop writing the derived mask DICOM. The viewer composites its heat-map and overlay display modes client-side from the `.png` sidecar; the ~24 MB uint16 write is only still needed for the viewer's `result` mode and for masks a phase has already saved
- Sweep orphaned `derived/` blobs (filter results and segmentation masks + their `.json` sidecars) left behind by deleted phases
- Sweep orphaned voice clips — a recording that was presigned and uploaded but never attached to a message keeps its bytes with nothing referencing them. Only the over-quota case is cleaned up today
- Persist the applied filter on the `Instance` row so it reapplies on load (the "save filter" feature). The viewer now offers its three display modes on a re-opened phase too, but with nothing recording which algorithm produced a slice it can only label them "Saved result"
- Unit and integration tests for `AuthService`, `ChatService`, `IngestionService`
- **Precompute at ingestion** — chain the default segmentation model as a follow-on task after `ingest_dicom_instance`. Content-addressed keys mean the doctor's click then becomes a cache hit. The Celery pixel task it would chain to already exists
- Drop the derived-DICOM write for segmentation masks once the viewer renders overlays from the polygons or `mask_png_url`
- ONNX Runtime / int8 quantisation for CPU inference (2-4x on a U-Net), or a GPU inference host — see `possible_fixes.md`

## Known pitfalls

- **`instances.instance_number` is NULL whenever the DICOM omits `InstanceNumber`** — routine for single-image studies. Anything that keys off it silently no-ops on those files: the phase merge used to splice overrides by `instance_number`, so a saved filter/segmentation result was written to MinIO *and* to the DB and then dropped at read time, and the viewer re-rendered the untouched original. Use `parent_instance_id`. `StudyService.list_instances` orders `NULLS LAST` with `created_at, id` as tiebreakers so a stack of number-less slices doesn't reshuffle between loads
- **A stopped Celery worker makes uploads hang forever with no error.** `POST /uploads` returns 202 and the task sits in Redis unconsumed; the viewer shows "Processing on server — 0 / N files" indefinitely. `docker compose ps` is the first check: both `dicom-worker` (queue `default`) and `dicom-pixel-worker` (queue `pixels`) must be up. Note `restart: unless-stopped` does *not* bring back a container that was explicitly stopped
- The worker containers run the same image as the API, so they used to inherit its `HEALTHCHECK` (`curl :8000`) — which no worker serves, leaving them permanently "unhealthy" and a genuinely dead worker indistinguishable from a live one. Both now override it in compose with `celery inspect ping -d <node>@$HOSTNAME`
- `bitnami/minio:latest` manifest fails in GitHub Actions CI — MinIO service removed from test job; tests needing storage must mock `StorageService`
- pgAdmin rejects `.local` TLD emails — use a real domain in `PGADMIN_EMAIL`
- ruff strips `import app.db.models` in `alembic/env.py` as unused — it must stay (autogenerate target)
- After changing `pyproject.toml`, run `make build-tools` to rebuild the tools image
- `[tool.black]` must use `extend-exclude`, not `exclude` — plain `exclude` *replaces* black's defaults, sending `make format` off to walk a local `.venv/` (minutes, then a spurious failure). CI never caught this because the runner has no `.venv`
- `medical-image-std` pulls torch (CPU wheel in prod) — cache `pip install` layers carefully in Docker
- **Install `torch` and `torchvision` from the same index.** The Dockerfile installs both from `https://download.pytorch.org/whl/cpu`. Installing only torch there leaves torchvision to resolve from PyPI, and that CUDA-variant build cannot register its C++ ops against a `+cpu` torch — every deep-segmentation request then dies with `operator torchvision::nms does not exist` the moment `segmentation_models_pytorch`/`timm` import it
- The segmentation checkpoint cache lives at `/var/cache/medical-std/models`, created **and chowned to `app`** in the Dockerfile. The container runs as uid 1001, and Docker seeds a named volume with the ownership of the image directory behind it — mount a volume over a path that doesn't exist in the image and its root stays root-owned, so `from_pretrained` fails with `PermissionError`. After changing that path, `docker volume rm dicom-platform_model-cache` or the old root-owned volume is reused
- An **unhandled** exception returns a 500 that never passes through `CORSMiddleware` (Starlette's `ServerErrorMiddleware` sits outside it), so the browser reports a misleading "No 'Access-Control-Allow-Origin' header" instead of the real error. Map foreseeable failures to `HTTPException` — `SegmentationModelError` → 503, `FilterError` → 400 — so the client sees a usable message
- **`POST /processing/apply` and `/segmentation/apply` return 200 *or* 202** — a client that assumes 200-with-a-URL breaks the moment the result isn't cached. The viewer's `PixelJobService` hides this; anything else calling these endpoints must handle both
- **Torch sizes its thread pool from the host's core count, not the container's cgroup quota.** On a 2-vCPU slice it spawns threads it cannot schedule and the request slows down. Set `TORCH_NUM_THREADS`
- **A GPU on the host is not a GPU in the container.** Both a passed-through device *and* a CUDA torch build are required, and either missing silently yields CPU. `make gpu-check` reports what the container actually resolved
- **A presigned PUT enforces no size limit.** MinIO will accept whatever the client sends, so any quota has to be checked *after* the upload with `stat_object` against the object that actually landed — a client-declared `size_bytes` is worth nothing. This is why `VoiceClipRef` has no size field
- **`RequestValidationError.errors()` is not JSON-serializable when a custom `model_validator` raised.** Pydantic puts the raw exception object in each entry's `ctx`, and `json.dumps` then dies *inside the error handler* — turning every such 422 into a 500 with no usable message. The handler in `app/core/exceptions.py` runs `jsonable_encoder` over it first; the first custom validator in the codebase (`SendMessageRequest`) is what exposed this
- Filter results live under `derived/` inside the same bucket as the source DICOM — never overwrite the source key
- PgBouncer transaction pooling: DDL statements must target Postgres directly (port 5432), not PgBouncer (6432) — Alembic's `database_url_sync` is wired correctly for this
- Concurrent DICOM ingestion of anonymous files (`patient_id='0'`) would previously race-insert the same patient row. Fixed in migration `0002` + `INSERT … ON CONFLICT DO NOTHING` in `IngestionService._upsert_patient`
- FastAPI ≥ 0.115 with `Annotated[T, Query(...)]`: do **not** put the default value inside `Query()` — set it with `= <default>` on the parameter instead, or FastAPI raises `AssertionError` at startup
- Phase creation must **not** bump the parent study's aggregate counters (`total_series_count`, `total_instance_count`, `total_size_bytes`) — those describe DICOM-ingested content only
- **A series-level share leaves `StudyResponse.share_source` null.** `active_share_row_for_study` matches `Share.study_id` only, so a study reached through a *series* share reports no share source. `SeriesResponse.share_source` (populated by `active_share_row_for_series`, which considers the series's own share *and* the parent study's) is the field permission checks must consult — the study-level one alone silently downgrades a MANAGE grantee to read-only and hides the share id needed to revoke
- `StudyService.list_series` filters `parent_series_id IS NULL` so the sidebar's top-level series list isn't polluted with every doctor's phases. Phases come from the separate `GET /series/{parent_id}/phases` endpoint
- Deleting a phase does **not** delete its derived blobs in MinIO — they're content-addressed under `derived/` and may be shared with other phases (same filter + params hash). A future maintenance task can sweep orphans
