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

### Friendship model
- Single `friendships` table, status-driven: `pending` → `accepted`; delete = reject or unfriend
- Canonical ordering: `user_a_id < user_b_id` enforced by a CHECK constraint and a `UNIQUE(user_a_id, user_b_id)` — uniqueness holds regardless of who initiated
- `requested_by` column records the initiator; the `direction` field in API responses is derived from it

### Redis (`app/core/redis.py`)
- Async client via `redis.asyncio` (part of the `redis[hiredis]` package already in dependencies)
- Module-level singleton — one connection pool per process
- Closed cleanly in the FastAPI lifespan `finally` block via `close_redis()`
- Current uses: WS ticket store. Future: pub/sub bridge for multi-replica WebSocket fan-out
