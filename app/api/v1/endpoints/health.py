"""Liveness and readiness probes.

- ``/live`` returns 200 as long as the process is running — used by Docker
  healthcheck / orchestrators to detect crashed containers.
- ``/ready`` additionally checks every external dependency (DB, Redis, MinIO)
  and only reports healthy when the service can actually serve traffic.
"""

from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import text

from app import __version__
from app.api.deps import DBSession, SettingsDep
from app.schemas.common import DependencyCheck, HealthResponse, ReadinessResponse
from app.services.storage_service import StorageService

router = APIRouter()


@router.get("/live", response_model=HealthResponse, status_code=status.HTTP_200_OK)
async def liveness(settings: SettingsDep) -> HealthResponse:
    return HealthResponse(
        status="ok",
        version=__version__,
        environment=settings.app_env.value,
    )


@router.get("/ready", response_model=ReadinessResponse)
async def readiness(db: DBSession, settings: SettingsDep) -> ReadinessResponse:
    checks: list[DependencyCheck] = []

    # --- PostgreSQL ---
    try:
        await db.execute(text("SELECT 1"))
        checks.append(DependencyCheck(name="postgres", healthy=True))
    except Exception as exc:
        checks.append(DependencyCheck(name="postgres", healthy=False, detail=str(exc)))

    # --- Redis ---
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(  # type: ignore[no-untyped-call]
            settings.redis_url, decode_responses=True
        )
        try:
            await client.ping()
            checks.append(DependencyCheck(name="redis", healthy=True))
        finally:
            await client.aclose()
    except Exception as exc:
        checks.append(DependencyCheck(name="redis", healthy=False, detail=str(exc)))

    # --- MinIO ---
    try:
        storage = StorageService(settings)
        storage.client.list_buckets()
        checks.append(DependencyCheck(name="minio", healthy=True))
    except Exception as exc:
        checks.append(DependencyCheck(name="minio", healthy=False, detail=str(exc)))

    overall = "ok" if all(c.healthy for c in checks) else "degraded"
    return ReadinessResponse(status=overall, checks=checks)
