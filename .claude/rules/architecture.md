# Architecture decisions

## DICOM serving — two channels, no PNG conversion
- Metadata (study/series/instance fields) → JSON via FastAPI REST
- Pixel data → browser fetches `.dcm` directly from MinIO via presigned GET URL
- Never convert DICOM to PNG/JPEG for viewer delivery — lossy, clinically unsafe
- Thumbnails (JPEG) are acceptable only for study-list previews, stored in `thumbnails` bucket

## MinIO presigned URLs
- Internal Docker hostname (`minio:9000`) must be rewritten to `MINIO_EXTERNAL_ENDPOINT` before returning URLs to the browser
- `_to_external_url()` in `StorageService` handles this — always call it on presigned URLs
- `MINIO_EXTERNAL_ENDPOINT` is env-driven so staging/prod can override it

## Database connections — two DSNs
- App (FastAPI, async): connects through **PgBouncer** on port 6432 via `database_url_async`
- Alembic (sync migrations): connects **directly to Postgres** on port 5432 via `database_url_sync` — bypasses PgBouncer because DDL + transaction pooling don't mix

## Object key layout
`{owner_id}/{study_uid}/{series_uid}/{sop_uid}.dcm` — defined in `StorageService.dicom_object_key()`

## Celery
- `task_acks_late=True` + `worker_prefetch_multiplier=1` — tasks are not acknowledged until complete, one at a time per worker
- Tasks auto-discovered from `app.workers.tasks`

## Authentication
- Access tokens (JWT HS256, 15 min) + refresh tokens (7 days, rotated on every use)
- Refresh-token families: each rotation issues a new family member; reuse of an old member revokes the entire family (reuse-detection logout)
- `CurrentUser` dependency (`app/api/deps.py`) — decodes Bearer JWT, loads the user from DB; used as a FastAPI `Depends` in every protected endpoint
- `CurrentAdmin` — same as `CurrentUser` but also asserts `role == 'admin'`
- Password hashing: Argon2id via `argon2-cffi` with cost parameters from `Settings`

## Series Phases — saved modifications

### Why no new tables
A *Phase* is conceptually "a named branch of a parent series" — the cleanest representation is to reuse the `series` table itself with two new columns:

* `parent_series_id` (nullable self-FK, ON DELETE CASCADE) — NULL = original DICOM-ingested series; non-NULL = a phase derived from that parent.
* `owner_id` (nullable FK to `users`, ON DELETE SET NULL) — the doctor who saved the phase. NULL for originals (their visibility comes from the parent study).

Annotations attach to phase-owned Instance rows via the existing `instance_id` FK — no schema change to the `annotations` table.

### Per-instance handling
A phase Instance row is created for every slice the doctor *touched* (filter applied OR annotation drawn).

Every phase Instance row carries `parent_instance_id` — the parent slice it overrides. This is what the merge keys on. `instance_number` cannot serve that role: it mirrors the DICOM `InstanceNumber` tag, which is absent from plenty of real files, and a NULL on both sides means the override never matches and the saved result renders as the untouched parent.

* Pixel-modified slices: `file_path` = derived MinIO key under `derived/`.
* Annotation-only slices: `file_path` = same key as the parent's instance (zero bytes duplicated, just a row).

Each phase Instance gets a freshly-generated `sop_instance_uid` (via `pydicom.uid.generate_uid()`) because rows live under the phase's own series; the parent's SOP UIDs are not reused.

### Rendering contract — backend merges
`PhaseService.list_instances_rendered(series)` is the single rendering helper:

* For an original series → returns its instances ordered by `instance_number` (same as `StudyService.list_instances`).
* For a phase → returns the parent's instances with the phase's overrides spliced in by matching `parent_instance_id` (falling back to `instance_number` for rows saved before that column existed). Same shape, frontend doesn't branch.

The studies endpoint `GET /studies/{}/series/{}/instances` routes through this helper, so the existing route serves both kinds of series transparently.

### Save semantics
* `PATCH /phases/{id}` with `instances` present → in-place save. Atomically deletes existing phase Instance rows (cascading their annotations) and inserts the new set in one transaction.
* `PATCH /phases/{id}` without `instances` → rename only.
* `POST /series/{parent_id}/phases` → always creates a new phase. The frontend's "Save As" routes here.

### Visibility
Phases are **private to their creator**. Reads enforce `WHERE parent_series_id = ? AND owner_id = current_user.id`. The parent study still has to be visible to the caller (owner-or-share check delegated to `StudyService.get_visible_study`).

### Validation
* Cross-series guard: every `parent_instance_id` in the payload must satisfy `instance.series_id == parent_series_id`. Reject 422 otherwise.
* Blob existence: every `derived_object_key` is verified with `StorageService.object_exists()` before insert — prevents dangling rows that would crash the viewer.

### What's NOT done
* No deletion of MinIO `derived/` blobs when a phase is removed — content-addressing means another phase may still reference the same key.
* No display-state persistence — window/level, zoom, rotation reset on reload.
* `series.series_description` doubles as the phase's display name; `series.protocol_name` doubles as the optional description. Lean v1 — a dedicated `display_name` column can be added later.

## Real-time chat

### WebSocket connection
- One persistent WebSocket per logged-in browser at `WS /api/v1/ws/chat?ticket=<ticket>`
- The connection is server→client only in v1; clients send via `POST /messages` (REST)
- Server sends a `{"type":"ping","data":{}}` frame every 30 s to prevent NAT/proxy timeout

### WebSocket ticket authentication
The browser `WebSocket` API does not allow setting custom headers on the upgrade handshake — credentials must travel in the URL.  Putting a JWT there exposes it in every server access log.  The ticket pattern avoids this:

1. Client calls `POST /ws-ticket` — Bearer JWT travels in the `Authorization` header (never logged)
2. Server generates `secrets.token_hex(32)` (256-bit random), stores `ws_ticket:<token> → user_id` in Redis with a **30-second TTL**, returns `{ ticket }`
3. Client opens `wss://…/ws/chat?ticket=<token>` — only the disposable ticket is in the URL
4. WS gateway calls `GETDEL ws_ticket:<token>` — atomically fetches **and deletes**; any replay attempt (even within TTL) is rejected with `1008 Policy Violation`

The ticket carries no information and is single-use; a stolen log entry is already dead.

### WebSocket hub (`app/services/ws_hub.py`)
- `WebSocketHub` maps `user_id → set[WebSocket]` — a single user may have multiple tabs open
- `register(user_id, ws)` / `unregister(user_id, ws)` called by the gateway on connect / disconnect
- `deliver(user_id, envelope)` fans out to all open sockets for that user; errors on individual sockets are swallowed (best-effort)
- Protected by `asyncio.Lock()` to prevent concurrent dict mutations
- Currently **in-process** (single API replica). For horizontal scaling, wrap `deliver` in a Redis pub/sub bridge — the `register`/`unregister`/`deliver` interface is designed so callers won't change

### Voice messages
Audio follows the same split as DICOM pixels: **bytes go browser↔MinIO directly, only metadata touches Postgres.**

1. `POST /messages/voice/presign` with the recorder's `mime_type` → a presigned PUT plus the `object_key` to quote back. The key is `{sender_id}/{uuid4}.{ext}` in the `voice-messages` bucket
2. Browser PUTs the recording straight to MinIO — the API never sees the bytes
3. `POST /messages` with `voice: {object_key, mime_type, duration_ms}` → the Message row is written and pushed over the WebSocket

The sender's id is the **first path segment** of the key so `_resolve_voice_clip` can reject another user's key by prefix, without a lookup. Size is read back from MinIO with `stat_object` rather than taken from the client: a presigned PUT carries no length limit of its own, so the moment the bytes have landed is the only honest place to enforce the quota (`voice_message_max_bytes`, default 10 MB). An over-quota object is deleted rather than left orphaned.

`MessageResponse.voice.url` is a presigned GET **minted per read**, never stored — it expires with `minio_presigned_url_expire_seconds`, so a client holding a thread open past that must refetch rather than cache the URL. The four `messages.voice_*` columns are covered by a CHECK that keeps them all-null or all-set; a half-written row would render as an audio player with no source.

Voice notes reuse the `message.new` envelope, so the chat reducer needs no new branch — same choice the share-card bubbles made.

### Friendship model
- Single `friendships` table, status-driven: `pending` → `accepted`; delete = reject or unfriend
- Canonical ordering: `user_a_id < user_b_id` enforced by a CHECK constraint and a `UNIQUE(user_a_id, user_b_id)` — uniqueness holds regardless of who initiated
- `requested_by` column records the initiator; the `direction` field in API responses is derived from it

### Redis (`app/core/redis.py`)
- Async client via `redis.asyncio` (part of the `redis[hiredis]` package already in dependencies)
- Module-level singleton — one connection pool per process
- Closed cleanly in the FastAPI lifespan `finally` block via `close_redis()`
- Current uses: WS ticket store. Future: pub/sub bridge for multi-replica WebSocket fan-out
