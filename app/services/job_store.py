"""Redis-backed state for asynchronous pixel jobs.

Filter and segmentation work moved off the request path into Celery, so the
browser needs somewhere to read the outcome from. Unlike `UploadJob` these
records are ephemeral — a result is a content-addressed object key that can be
recomputed from the request at any time — so they live in Redis under a TTL
rather than in Postgres. No migration, and nothing to sweep.

Written from Celery (sync client) and read from FastAPI (async client), so both
flavours live here and share one key layout.
"""

from __future__ import annotations

import json
import uuid
from enum import StrEnum
from typing import Any, cast

import redis as sync_redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_KEY_PREFIX = "procjob:"
_TTL_SECONDS = 3600


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def job_key(job_id: str) -> str:
    return f"{_KEY_PREFIX}{job_id}"


def new_job_id() -> str:
    return str(uuid.uuid4())


async def create(job_id: str, owner_id: uuid.UUID, kind: str, **fields: Any) -> dict[str, Any]:
    """Write the initial QUEUED record. Called from the endpoint before enqueueing."""
    record: dict[str, Any] = {
        "job_id": job_id,
        "owner_id": str(owner_id),
        "kind": kind,
        "status": JobStatus.QUEUED,
        "stage": None,
        **fields,
    }
    client = await get_redis()
    await client.set(job_key(job_id), json.dumps(record), ex=_TTL_SECONDS)
    return record


async def read(job_id: str) -> dict[str, Any] | None:
    client = await get_redis()
    raw = await client.get(job_key(job_id))
    if raw is None:
        return None
    decoded: dict[str, Any] = json.loads(raw)
    return decoded


def update_sync(job_id: str, **fields: Any) -> None:
    """Merge fields into an existing record from a Celery worker.

    Best-effort: a job whose record has expired, or a Redis blip, must not fail
    the task that produced a perfectly good derived object.
    """
    try:
        settings = get_settings()
        client = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        key = job_key(job_id)
        # redis-py types these as possibly-awaitable to cover its async client;
        # this one is the sync client, so the values are already concrete.
        raw = cast(bytes | None, client.get(key))
        record: dict[str, Any] = json.loads(raw) if raw else {"job_id": job_id}
        record.update(fields)
        # Preserve the remaining TTL rather than extending it on every progress
        # update, so a stuck job still expires on schedule.
        ttl = cast(int, client.ttl(key))
        client.set(key, json.dumps(record), ex=ttl if ttl > 0 else _TTL_SECONDS)
        client.close()
    except Exception as exc:
        logger.warning("job_store_update_failed", job_id=job_id, error=str(exc))
