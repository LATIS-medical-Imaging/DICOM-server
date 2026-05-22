"""WebSocket gateway for the chat module.

Authentication uses single-use tickets issued by ``POST /ws-ticket`` rather
than the raw JWT.  The browser ``WebSocket`` API cannot set custom headers on
the upgrade handshake, so credentials must travel in the URL.  Putting the JWT
there exposes it in server access logs; tickets are short-lived, single-use,
and carry no information — a leaked ticket is already dead by the time any log
is read.

Protocol
--------
* server → client: ``{"type": <event>, "data": {...}}`` JSON envelopes.
* client → server: nothing meaningful in v1.  The server sends a ping envelope
  every 30 seconds to prevent NAT/proxy timeouts.  Actual message sending goes
  through ``POST /messages`` so the auth interceptor, validation, and DB-write
  path all apply uniformly.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.api.v1.endpoints.ws_ticket import consume_ticket
from app.core.logging import get_logger
from app.db.models.user import User
from app.db.session import SessionLocal
from app.services.ws_hub import get_ws_hub

router = APIRouter()
logger = get_logger(__name__)

PING_INTERVAL_SECONDS: int = 30


async def _resolve_user(user_id: uuid.UUID) -> User | None:
    """Load an active, non-deleted user by primary key.  Returns ``None`` on any miss."""
    async with SessionLocal() as db:
        result = await db.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


@router.websocket("/chat")
async def chat_socket(
    websocket: WebSocket,
    ticket: str = Query(..., min_length=1, description="Single-use WS handshake ticket."),
) -> None:
    """Accept and serve a chat WebSocket connection.

    The ``ticket`` query parameter is exchanged for a user identity via a Redis
    ``GETDEL``.  The operation is atomic — any replay attempt (even within the
    30-second TTL) is rejected with ``1008 Policy Violation``.
    """
    # Step 1: consume the ticket — one atomic GETDEL in Redis.
    user_id = await consume_ticket(ticket)
    if user_id is None:
        logger.warning("ws_ticket_rejected", reason="missing_or_expired")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Step 2: verify the user still exists and is active in the DB.
    user = await _resolve_user(user_id)
    if user is None:
        logger.warning("ws_ticket_rejected", user_id=str(user_id), reason="user_inactive")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    # Step 3: accept the connection and register with the hub.
    await websocket.accept()
    hub = get_ws_hub()
    await hub.register(user.id, websocket)
    logger.info("ws_connected", user_id=str(user.id))

    try:
        while True:
            # Race a client-frame read against the periodic ping interval.
            # Whichever resolves first wins; the other task is cancelled.
            recv_task = asyncio.create_task(websocket.receive_text())
            ping_task = asyncio.create_task(asyncio.sleep(PING_INTERVAL_SECONDS))

            done, pending = await asyncio.wait(
                {recv_task, ping_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if recv_task in done:
                # v1 has no client→server messages; drain the frame and continue.
                # Any exception (disconnect, etc.) propagates to the outer handler.
                try:
                    recv_task.result()
                except WebSocketDisconnect:
                    raise
                except Exception:  # noqa: BLE001
                    raise
            else:
                # Ping interval elapsed — send a keep-alive envelope.
                try:
                    await websocket.send_json({"type": "ping", "data": {}})
                except Exception:  # noqa: BLE001
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(user.id, websocket)
        logger.info("ws_disconnected", user_id=str(user.id))
