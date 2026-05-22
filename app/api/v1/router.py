"""Aggregates all v1 endpoint routers."""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    auth,
    friendships,
    health,
    messages,
    presign,
    processing,
    studies,
    uploads,
    users,
    ws_chat,
)

api_router = APIRouter()
api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(admin.router, prefix="/admin", tags=["admin"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(friendships.router, prefix="/friendships", tags=["friendships"])
api_router.include_router(messages.router, prefix="/messages", tags=["messages"])
api_router.include_router(presign.router, prefix="/presign", tags=["presign"])
api_router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
api_router.include_router(studies.router, prefix="/studies", tags=["studies"])
api_router.include_router(processing.router, prefix="/processing", tags=["processing"])
api_router.include_router(ws_chat.router, prefix="/ws", tags=["ws"])
