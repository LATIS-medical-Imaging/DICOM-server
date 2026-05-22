"""Async Redis client — module-level singleton.

One connection pool is created lazily on the first call to ``get_redis()`` and
shared across all requests in the process.  The pool is closed cleanly during
application shutdown via ``close_redis()``, which is called from the FastAPI
lifespan context manager.

``decode_responses=True`` means every value coming back from Redis is already
a ``str`` — no manual ``.decode()`` calls needed in callers.
"""

from __future__ import annotations

import redis.asyncio as aioredis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """Return the shared async Redis client, creating it on first call."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        logger.info(
            "redis_client_created",
            host=settings.redis_host,
            port=settings.redis_port,
        )
    return _client


async def close_redis() -> None:
    """Close the connection pool.  Safe to call multiple times."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
        logger.info("redis_client_closed")
