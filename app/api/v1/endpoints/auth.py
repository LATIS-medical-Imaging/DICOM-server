"""Authentication endpoints — login, refresh, logout, me.

``/login`` is rate-limited at 5/min per ``(IP + email)`` and ``/refresh`` at
30/min per IP — both via slowapi (see :mod:`app.core.rate_limit`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    ClientIP,
    CurrentUser,
    DBSession,
    SettingsDep,
    UserAgentHeader,
)
from app.core.rate_limit import _ip_email_key, cache_json_body, limiter
from app.schemas.auth import (
    AuthResponse,
    LoginRequest,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
    UserResponse,
)
from app.services.auth_service import AuthService

router = APIRouter()


@router.post(
    "/login",
    response_model=AuthResponse,
    status_code=status.HTTP_200_OK,
    summary="Exchange email + password for an access/refresh token pair",
    dependencies=[Depends(cache_json_body)],
)
@limiter.limit("5/minute", key_func=_ip_email_key)
async def login(
    request: Request,
    body: LoginRequest,
    db: DBSession,
    settings: SettingsDep,
    ip: ClientIP,
    user_agent: UserAgentHeader,
) -> AuthResponse:
    service = AuthService(db, settings)
    return await service.login(
        email=str(body.email),
        password=body.password,
        ip_address=ip,
        user_agent=user_agent,
    )


@router.post(
    "/refresh",
    response_model=TokenPair,
    status_code=status.HTTP_200_OK,
    summary="Rotate an active refresh token into a new access/refresh pair",
)
@limiter.limit("30/minute")
async def refresh(
    request: Request,
    body: RefreshRequest,
    db: DBSession,
    settings: SettingsDep,
    ip: ClientIP,
    user_agent: UserAgentHeader,
) -> TokenPair:
    service = AuthService(db, settings)
    return await service.refresh(
        refresh_token=body.refresh_token,
        ip_address=ip,
        user_agent=user_agent,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the refresh-token family for the current session",
)
async def logout(body: LogoutRequest, db: DBSession, settings: SettingsDep) -> Response:
    service = AuthService(db, settings)
    await service.logout(refresh_token=body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me",
    response_model=UserResponse,
    status_code=status.HTTP_200_OK,
    summary="Return the authenticated user",
)
async def me(user: CurrentUser) -> UserResponse:
    return UserResponse.model_validate(user)
