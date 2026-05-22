"""In-process WebSocket connection registry — single API replica, for now.

The hub maps ``user_id`` to the set of live WebSocket connections that
identified as that user.  A single user can have several tabs open at once, so
delivery fans out to all of them.

For horizontal scaling we will eventually wrap this in a Redis pub/sub bridge,
but the public surface (``register`` / ``unregister`` / ``deliver``) is what
callers depend on — that won't change.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger

logger = get_logger(__name__)


class WebSocketHub:
    def __init__(self) -> None:
        self._connections: dict[uuid.UUID, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def register(self, user_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            self._connections[user_id].add(ws)
        logger.info(
            "ws_registered", user_id=str(user_id), open_sockets=len(self._connections[user_id])
        )

    async def unregister(self, user_id: uuid.UUID, ws: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if sockets is None:
                return
            sockets.discard(ws)
            if not sockets:
                self._connections.pop(user_id, None)
        logger.info("ws_unregistered", user_id=str(user_id))

    async def deliver(self, user_id: uuid.UUID, envelope: dict[str, Any]) -> int:
        """Send ``envelope`` to every open socket for ``user_id``.

        Returns the number of sockets that actually accepted the frame — a
        useful debug signal but never raised: a closed socket on send is just a
        socket we'll clean up on the next disconnect.
        """
        async with self._lock:
            sockets = list(self._connections.get(user_id, ()))

        if not sockets:
            return 0

        delivered = 0
        for ws in sockets:
            try:
                await ws.send_json(envelope)
                delivered += 1
            except Exception as exc:
                logger.warning(
                    "ws_send_failed",
                    user_id=str(user_id),
                    error=str(exc),
                )
        return delivered


_hub: WebSocketHub | None = None


def get_ws_hub() -> WebSocketHub:
    global _hub
    if _hub is None:
        _hub = WebSocketHub()
    return _hub
