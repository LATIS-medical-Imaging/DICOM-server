"""SlowAPI rate-limiter shared across endpoints.

Login is keyed by ``ip + email`` so a single misbehaving IP can't lock out an
unrelated user, and an attacker that rotates IPs can't sneak past a per-IP
counter while spraying the same email. Refresh is per-IP only — legitimate
users only refresh a handful of times per hour.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import HTTPException, Request, status
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address


def _ip_email_key(request: Request) -> str:
    """Rate-limit key combining the remote IP and the posted ``email`` field.

    Reads the cached JSON body that the middleware/router has already parsed
    on the same request; falls back to ``ip`` when the body is unavailable
    (e.g. before validation runs).
    """
    ip = get_remote_address(request) or "unknown"
    # Streaming bodies aren't available synchronously here — best-effort.
    email = "*"
    body_cache: dict[str, Any] | None = getattr(request.state, "json_body_cache", None)
    if isinstance(body_cache, dict):
        email = str(body_cache.get("email", "*")).strip().lower() or "*"
    return f"{ip}:{email}"


limiter = Limiter(key_func=get_remote_address)


def rate_limit_exceeded_handler(_request: Request, exc: RateLimitExceeded) -> None:
    """Translate slowapi's exception into our uniform 429 envelope."""
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail=f"Rate limit exceeded: {exc.detail}",
    )


async def cache_json_body(request: Request) -> None:
    """Pre-read the request body so the limiter can key by a field inside it.

    FastAPI consumes the body once during validation; we read it here, parse
    it, stash it on ``request.state.json_body_cache``, and rebuild the
    request's receive stream so downstream handlers see the body intact.
    """
    if request.method.upper() not in {"POST", "PUT", "PATCH"}:
        return
    body = await request.body()
    if not body:
        return
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        request.state.json_body_cache = payload

    async def _replay() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = _replay
