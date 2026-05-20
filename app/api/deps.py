"""Shared FastAPI dependencies (DB session, settings, storage, current user)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import TokenType, decode_token
from app.db.models.user import User, UserRole
from app.db.session import get_db
from app.services.storage_service import StorageService

# auto_error=False so we raise AuthenticationError (uniform envelope) instead of
# FastAPI's default 403 with `{detail: "Not authenticated"}`.
bearer_scheme = HTTPBearer(auto_error=False)


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> StorageService:
    return StorageService(settings)


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)] = None,
) -> User:
    """Resolve the user from the ``Authorization: Bearer <jwt>`` header.

    401 on every failure mode (missing header, bad signature, expired, wrong
    type, user disabled or deleted). 403 is reserved for valid-token-but-
    insufficient-role and is enforced by ``require_role`` below.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Authentication required.")

    payload = decode_token(credentials.credentials, settings=settings)
    if payload.get("type") != TokenType.ACCESS:
        raise AuthenticationError("Invalid access token.")
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid access token.") from exc

    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise AuthenticationError("User no longer exists or is disabled.")
    return user


def require_role(role: str):
    """Factory: dependency that 403s unless the current user has ``role``."""

    async def _checker(user: Annotated[User, Depends(get_current_user)]) -> User:
        if user.role != role:
            raise PermissionDeniedError("Insufficient privileges.")
        return user

    return _checker


def get_client_ip(request: Request) -> str | None:
    """Best-effort client IP. Trusts ``X-Forwarded-For`` first hop (Railway/CF)."""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip() or None
    if request.client is None:
        return None
    return request.client.host


def get_user_agent(request: Request) -> str | None:
    return request.headers.get("user-agent")


DBSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[StorageService, Depends(get_storage)]
CurrentUser = Annotated[User, Depends(get_current_user)]
CurrentAdmin = Annotated[User, Depends(require_role(UserRole.ADMIN))]
ClientIP = Annotated[str | None, Depends(get_client_ip)]
UserAgentHeader = Annotated[str | None, Depends(get_user_agent)]
