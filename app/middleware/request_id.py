"""Assigns a correlation ID to every request and propagates it to logs.

- Reuses an incoming ``X-Request-ID`` header if the client sent one.
- Otherwise generates a fresh UUIDv4.
- Binds it to structlog's contextvars so every log line in the request
  scope carries ``request_id=...``.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        response = await call_next(request)
        response.headers[HEADER] = request_id
        return response
