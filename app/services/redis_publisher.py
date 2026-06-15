"""Synchronous Redis publisher — for use from Celery tasks.

Celery workers run in a separate process from the FastAPI app, so they cannot
call the in-process WebSocketHub directly.  Instead, they publish a JSON
notification to the ``ws:notifications`` Redis channel.  The FastAPI lifespan
subscriber (see ``main.py``) reads that channel and forwards each message to
``WebSocketHub.deliver()``.
"""

from __future__ import annotations

import json
from typing import Any

import redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_WS_CHANNEL = "ws:notifications"


def publish_ws_event(user_id: str, event_type: str, data: dict[str, Any]) -> None:
    """Publish a WS envelope to the Redis pub/sub bridge channel.

    Best-effort — never raises; a failure here must not affect the Celery task.
    """
    try:
        settings = get_settings()
        client = redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        payload = json.dumps({"user_id": user_id, "type": event_type, "data": data})
        client.publish(_WS_CHANNEL, payload)
        client.close()
    except Exception as exc:
        logger.warning(
            "ws_publish_failed",
            event_type=event_type,
            user_id=user_id,
            error=str(exc),
        )
