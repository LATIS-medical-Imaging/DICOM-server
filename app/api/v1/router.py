"""Aggregates all v1 endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints import health, presign, studies, uploads

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(presign.router, prefix="/presign", tags=["presign"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(studies.router, prefix="/studies", tags=["studies"])
