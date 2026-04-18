"""FastAPI application factory and entrypoint.

The app is created via :func:`create_app` so tests and scripts can spin up
isolated instances without import-time side effects leaking across them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.router import api_router
from app.core.config import Settings, get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger
from app.db.session import dispose_engine
from app.middleware.request_id import RequestIDMiddleware
from app.services.storage_service import StorageService

logger = get_logger(__name__)


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
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage_init_failed", error=str(exc))

    try:
        yield
    finally:
        logger.info("app_shutdown")
        await dispose_engine()


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

    # Middleware (outermost declared last)
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[str(o).rstrip("/") for o in settings.cors_origins],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    register_exception_handlers(app)

    app.include_router(api_router, prefix="/api/v1")

    return app


app = create_app()
