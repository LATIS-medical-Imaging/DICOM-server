"""WebSocket gateway for the chat module.

The browser cannot send an ``Authorization: Bearer`` header on a WebSocket
handshake, so the client passes its access token via the ``?access_token=``
query string.  The token is the same short-lived JWT the REST endpoints use —
its server-side rotation/reuse-detection story still applies.

The protocol is intentionally minimal:

* server → client: ``{ type, data }`` envelopes (see :class:`WsEnvelope`).
* client → server: nothing (a single ping every 30s keeps the socket alive).
  Sending messages goes through the regular ``POST /messages`` route — that
  way the same authorization, validation and DB write path apply.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.security import TokenType, decode_token
from app.db.models.user import User
from app.db.session import SessionLocal
from app.services.ws_hub import get_ws_hub

router = APIRouter()
logger = get_logger(__name__)

PING_INTERVAL_SECONDS = 30


async def _authenticate(token: str) -> User | None:
    """Decode the access token and return the active user, or ``None``."""
    settings = get_settings()
    try:
        payload = decode_token(token, settings=settings)
    except Exception:
        return None
    if payload.get("type") != TokenType.ACCESS:
        return None
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError):
        return None

    async with SessionLocal() as db:
        result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
        user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


@router.websocket("/chat")
async def chat_socket(
    websocket: WebSocket,
    access_token: str = Query(..., min_length=1),
) -> None:
    user = await _authenticate(access_token)
    if user is None:
        # Per RFC 6455, the only way to refuse before accepting is to close
        # with a policy-violation code.  Browsers surface this as a generic
        # connection error, which is fine for our purposes.
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await websocket.accept()
    hub = get_ws_hub()
    await hub.register(user.id, websocket)

    try:
        while True:
            # Two concurrent tasks: wait for a client frame, or send a
            # periodic ping.  Whichever resolves first wins the iteration.
            recv_task = asyncio.create_task(websocket.receive_text())
            ping_task = asyncio.create_task(asyncio.sleep(PING_INTERVAL_SECONDS))

            done, pending = await asyncio.wait(
                {recv_task, ping_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

            if recv_task in done:
                # Clients aren't expected to say anything; we just drain the
                # frame and continue.  If the recv raises, the outer
                # WebSocketDisconnect handler tears us down.
                try:
                    recv_task.result()
                except WebSocketDisconnect:
                    raise
                except Exception:
                    raise
            else:
                # Ping fired — try to send a no-op envelope; failure means
                # the socket has gone away and we should exit.
                try:
                    await websocket.send_json({"type": "ping", "data": {}})
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(user.id, websocket)
