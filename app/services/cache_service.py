"""Application-level Redis cache service.

Cache-aside pattern: read from Redis first; on miss, compute and write.
All methods are non-fatal — any Redis error is logged and the caller falls
through to the DB / MinIO as if the cache were empty.

Key prefixes (all in DB 0, no collision with ``ws_ticket:*`` or pub/sub):
    presign:{object_key}      — presigned GET URL string; TTL = URL expiry - 60 s
    study_list:{user_id}      — JSON list of study dicts (with share_source);
                                TTL = 60 s (backed up by event-driven invalidation)
    series_list:{study_id}    — JSON list of series dicts; no TTL (immutable)
    instances:{series_id}     — JSON list of instance dicts; no TTL (immutable)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from redis.asyncio import Redis

from app.core.logging import get_logger

logger = get_logger(__name__)

_PRESIGN_PREFIX = "presign:"
_STUDY_LIST_PREFIX = "study_list:"
_SERIES_PREFIX = "series_list:"
_INSTANCES_PREFIX = "instances:"

_STUDY_LIST_TTL = 60  # seconds — short fallback; invalidation keeps it fresh


class CacheService:
    """Thin async Redis wrapper for the three caching layers."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    # ── Presigned URLs ───────────────────────────────────────────────────
    # Cross-user: the same object_key always yields the same MinIO URL so
    # different users with access to the same object share one cache entry.

    async def get_presign(self, object_key: str) -> str | None:
        try:
            return await self._r.get(f"{_PRESIGN_PREFIX}{object_key}")
        except Exception as exc:
            logger.warning("cache_get_presign_error", object_key=object_key, error=str(exc))
            return None

    async def set_presign(self, object_key: str, url: str, url_ttl_seconds: int) -> None:
        """Store the URL with a TTL 60 s shorter than the URL itself so we
        never serve a URL that has already expired."""
        ttl = max(url_ttl_seconds - 60, 1)
        try:
            await self._r.set(f"{_PRESIGN_PREFIX}{object_key}", url, ex=ttl)
        except Exception as exc:
            logger.warning("cache_set_presign_error", object_key=object_key, error=str(exc))

    # ── Study list (per user) ────────────────────────────────────────────
    # Includes share_source metadata so the value is user-scoped.
    # Short TTL is the fallback; event-driven DELs keep it accurate.

    async def get_study_list(self, user_id: uuid.UUID) -> list[dict[str, Any]] | None:
        try:
            raw = await self._r.get(f"{_STUDY_LIST_PREFIX}{user_id}")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("cache_get_study_list_error", user_id=str(user_id), error=str(exc))
            return None

    async def set_study_list(self, user_id: uuid.UUID, data: list[dict[str, Any]]) -> None:
        try:
            await self._r.set(
                f"{_STUDY_LIST_PREFIX}{user_id}", json.dumps(data), ex=_STUDY_LIST_TTL
            )
        except Exception as exc:
            logger.warning("cache_set_study_list_error", user_id=str(user_id), error=str(exc))

    async def invalidate_study_list(self, *user_ids: uuid.UUID) -> None:
        if not user_ids:
            return
        keys = [f"{_STUDY_LIST_PREFIX}{uid}" for uid in user_ids]
        try:
            await self._r.delete(*keys)
        except Exception as exc:
            logger.warning("cache_invalidate_study_list_error", error=str(exc))

    # ── Series list (per study) ──────────────────────────────────────────
    # Series metadata is immutable after ingestion — no TTL, only deleted
    # when a series is deleted or a new upload arrives for that study.
    # Only cached for study owners (non-owners may receive filtered subsets).

    async def get_series_list(self, study_id: uuid.UUID) -> list[dict[str, Any]] | None:
        try:
            raw = await self._r.get(f"{_SERIES_PREFIX}{study_id}")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("cache_get_series_error", study_id=str(study_id), error=str(exc))
            return None

    async def set_series_list(self, study_id: uuid.UUID, data: list[dict[str, Any]]) -> None:
        try:
            await self._r.set(f"{_SERIES_PREFIX}{study_id}", json.dumps(data))
        except Exception as exc:
            logger.warning("cache_set_series_error", study_id=str(study_id), error=str(exc))

    async def invalidate_series_list(self, *study_ids: uuid.UUID) -> None:
        if not study_ids:
            return
        keys = [f"{_SERIES_PREFIX}{sid}" for sid in study_ids]
        try:
            await self._r.delete(*keys)
        except Exception as exc:
            logger.warning("cache_invalidate_series_error", error=str(exc))

    # ── Instance list (per series) ───────────────────────────────────────
    # Instance metadata is immutable after ingestion — no TTL.
    # Phases (dynamic merged stacks) are NOT cached; only original series.

    async def get_instances(self, series_id: uuid.UUID) -> list[dict[str, Any]] | None:
        try:
            raw = await self._r.get(f"{_INSTANCES_PREFIX}{series_id}")
            return json.loads(raw) if raw else None
        except Exception as exc:
            logger.warning("cache_get_instances_error", series_id=str(series_id), error=str(exc))
            return None

    async def set_instances(self, series_id: uuid.UUID, data: list[dict[str, Any]]) -> None:
        try:
            await self._r.set(f"{_INSTANCES_PREFIX}{series_id}", json.dumps(data))
        except Exception as exc:
            logger.warning("cache_set_instances_error", series_id=str(series_id), error=str(exc))

    async def invalidate_instances(self, *series_ids: uuid.UUID) -> None:
        if not series_ids:
            return
        keys = [f"{_INSTANCES_PREFIX}{sid}" for sid in series_ids]
        try:
            await self._r.delete(*keys)
        except Exception as exc:
            logger.warning("cache_invalidate_instances_error", error=str(exc))
