"""WebSocket connection registry, bridged across processes by Redis pub/sub.

The hub maps ``user_id`` to the set of live WebSocket connections that
identified as that user.  A single user can have several tabs open at once, so
delivery fans out to all of them.

The socket set is necessarily per-process — a socket lives in the worker that
accepted it — so ``deliver`` publishes to the ``ws:notifications`` channel
instead of writing to local sockets directly, and every process forwards what
it reads there into its own ``deliver_local``.  That is what makes running the
API with more than one uvicorn worker safe: without it, a chat message sent
through worker 1 would never reach a recipient whose tab is held by worker 2.

The public surface (``register`` / ``unregister`` / ``deliver``) is unchanged,
exactly as this module always promised.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

from fastapi import WebSocket

from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

WS_CHANNEL = "ws:notifications"


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
        """Fan ``envelope`` out to every process holding a socket for ``user_id``.

        Publishes to Redis; each process's forwarder calls `deliver_local`,
        including this one — so the local sockets are served by the same path as
        the remote ones and never get the frame twice. Returns the number of
        subscribers reached, or the local delivery count when Redis is down.
        """
        try:
            client = await get_redis()
            payload = json.dumps(
                {"user_id": str(user_id), "type": envelope["type"], "data": envelope["data"]}
            )
            return int(await client.publish(WS_CHANNEL, payload))
        except Exception as exc:
            # A single-replica deployment still works without Redis; falling
            # back keeps chat alive instead of silently dropping frames.
            logger.warning("ws_publish_failed_delivering_locally", error=str(exc))
            return await self.deliver_local(user_id, envelope)

    async def deliver_local(self, user_id: uuid.UUID, envelope: dict[str, Any]) -> int:
        """Send ``envelope`` to sockets held by *this* process.

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
