"""Single-use WebSocket handshake tickets.

Why this exists
---------------
The browser ``WebSocket`` constructor does not allow setting custom headers on
the upgrade request.  The only way to carry credentials is via the URL (query
string or path segment).  Putting the real JWT in a query string exposes it in:

  * every reverse-proxy / load-balancer access log
  * framework-level request logs (uvicorn, etc.)
  * any APM / observability tool that captures full request URLs
  * the browser's network inspector (visible to any XSS payload)

The ticket pattern removes the JWT from the URL entirely:

  1. The client calls ``POST /ws-ticket`` — the Bearer JWT travels in the
     ``Authorization`` header, which is never written to access logs.
  2. The server stores  ``ws_ticket:<random>  →  <user_id>``  in Redis with a
     30-second TTL and returns the random token.
  3. The client opens the WebSocket with ``?ticket=<random>`` instead of the JWT.
  4. The WS gateway calls Redis ``GETDEL`` — atomically fetches and deletes the
     entry.  Expired, already-consumed, or unknown tickets are all treated as
     invalid and result in a ``1008 Policy Violation`` close.

Because the ticket is deleted on first use, replaying it (even within 30 s) is
impossible.  And because it is a random 32-byte hex string — not a JWT — it
carries no information and is useless outside this handshake flow.
"""

from __future__ import annotations

import secrets
import uuid

from fastapi import APIRouter

from app.api.deps import CurrentUser
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.schemas.chat import WsTicketResponse

router = APIRouter()
logger = get_logger(__name__)

# How long (in seconds) a ticket is valid.  Generous enough for slow mobile
# connections; short enough that a stolen ticket from a log is already dead.
_TICKET_TTL_SECONDS: int = 30
_TICKET_KEY_PREFIX: str = "ws_ticket:"


@router.post("", response_model=WsTicketResponse, status_code=201)
async def create_ws_ticket(user: CurrentUser) -> WsTicketResponse:
    """Issue a single-use WebSocket handshake ticket for the authenticated user.

    The ticket is a 256-bit cryptographically random hex string.  It is stored
    in Redis under ``ws_ticket:<ticket>`` with a 30-second TTL.  The WS gateway
    consumes it atomically with ``GETDEL`` so it can never be reused.
    """
    ticket = secrets.token_hex(32)  # 256 bits of entropy
    redis = await get_redis()
    await redis.set(
        f"{_TICKET_KEY_PREFIX}{ticket}",
        str(user.id),
        ex=_TICKET_TTL_SECONDS,
    )
    logger.info("ws_ticket_issued", user_id=str(user.id))
    return WsTicketResponse(ticket=ticket)


async def consume_ticket(ticket: str) -> uuid.UUID | None:
    """Atomically fetch-and-delete a ticket from Redis.

    Returns the owning user's UUID, or ``None`` if the ticket is unknown,
    already consumed, or expired.  Co-located here (not in the WS gateway) so
    issuance and consumption logic live together and are easier to audit.
    """
    redis = await get_redis()
    raw: str | None = await redis.getdel(f"{_TICKET_KEY_PREFIX}{ticket}")
    if raw is None:
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        # Shouldn't happen unless Redis was tampered with, but be defensive.
        logger.warning("ws_ticket_invalid_uuid", raw=raw)
        return None
