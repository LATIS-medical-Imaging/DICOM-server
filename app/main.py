"""FastAPI application factory and entrypoint.

The app is created via :func:`create_app` so tests and scripts can spin up
isolated instances without import-time side effects leaking across them.
"""

from __future__ import annotations

import asyncio
import json
import uuid as _uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.core.rate_limit import limiter, rate_limit_exceeded_handler
from app.core.redis import close_redis, get_redis
from app.core.torch_runtime import configure_threads, device_report
from app.db.session import dispose_engine
from app.middleware.request_id import RequestIDMiddleware
from app.services.segmentation_service import preload_models
from app.services.storage_service import StorageService
from app.services.ws_hub import get_ws_hub

logger = get_logger(__name__)

_WS_CHANNEL = "ws:notifications"


async def _redis_ws_forwarder() -> None:
    """Subscribe to ``ws:notifications`` and forward each message to WebSocketHub.

    Runs as a background asyncio task for the lifetime of the FastAPI process.
    Both Celery workers and other API workers publish here — every WS frame in
    the system arrives through this channel — and we bridge each one into this
    process's own socket set. That is what lets the API run multiple uvicorn
    workers without messages going missing.
    """
    redis = await get_redis()
    pubsub = redis.pubsub()
    await pubsub.subscribe(_WS_CHANNEL)
    logger.info("redis_ws_forwarder_started", channel=_WS_CHANNEL)
    try:
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                payload = json.loads(message["data"])
                user_id = _uuid.UUID(payload["user_id"])
                envelope = {"type": payload["type"], "data": payload["data"]}
                await get_ws_hub().deliver_local(user_id, envelope)
            except Exception as exc:
                logger.warning("redis_ws_forward_error", error=str(exc))
    except asyncio.CancelledError:
        await pubsub.unsubscribe(_WS_CHANNEL)
        logger.info("redis_ws_forwarder_stopped")
        raise


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "app_startup",
        env=settings.app_env.value,
        version=__version__,
        debug=settings.app_debug,
    )

    # Ensure object storage buckets exist — fail fast if MinIO is unreachable.
    try:
        StorageService(settings).ensure_buckets()
    except Exception as exc:
        logger.warning("storage_init_failed", error=str(exc))

    # Resolve the torch device once at boot so the log says whether this
    # container actually got a GPU — a CPU-only image on a GPU host is silent
    # otherwise, and shows up only as slow requests. Backgrounded because the
    # `import torch` behind it costs seconds and must not delay readiness; it
    # doubles as a warm-up so the first filter request doesn't pay for it.
    async def _probe_torch() -> None:
        def _run() -> dict[str, object]:
            configure_threads(settings.torch_num_threads)
            report = device_report(settings.deep_segmentation_device)
            report["preloaded_models"] = preload_models(settings)
            return report

        try:
            logger.info("torch_runtime", **await asyncio.to_thread(_run))
        except Exception as exc:
            logger.warning("torch_runtime_probe_failed", error=str(exc))

    torch_probe = asyncio.create_task(_probe_torch())

    # Bridge Celery → WebSocket: forward Redis pub/sub events to connected clients.
    forwarder = asyncio.create_task(_redis_ws_forwarder())

    try:
        yield
    finally:
        forwarder.cancel()
        torch_probe.cancel()
        await asyncio.gather(forwarder, torch_probe, return_exceptions=True)
        logger.info("app_shutdown")
        await dispose_engine()
        await close_redis()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.app_debug,
        docs_url="/api/docs" if not settings.is_production else None,
        redoc_url="/api/redoc" if not settings.is_production else None,
        openapi_url="/api/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )
    app.state.settings = settings

    # Rate limiter (slowapi). The middleware records hits; the @limiter.limit
    # decorators on specific endpoints define the buckets.
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    # Middleware (outermost declared last)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o).rstrip("/") for o in settings.cors_origins],
        allow_credentials=False,  # Bearer tokens — no cookies, no CSRF surface.
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
