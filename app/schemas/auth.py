"""Pydantic schemas for authentication endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    """Body of POST /auth/login."""

    email: EmailStr = Field(..., description="User email address.")
    password: str = Field(
        ..., min_length=1, max_length=200, description="Plaintext password — verified server-side."
    )


class RefreshRequest(BaseModel):
    """Body of POST /auth/refresh."""

    refresh_token: str = Field(..., description="The previously issued refresh token.")


class LogoutRequest(BaseModel):
    """Body of POST /auth/logout — revokes the current refresh family."""

    refresh_token: str = Field(..., description="The refresh token whose family should be revoked.")


class UserResponse(BaseModel):
    """Public-safe user representation. Never carries the password hash."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    first_name: str
    last_name: str


class TokenPair(BaseModel):
    """A short-lived access token paired with a long-lived refresh token."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class AuthResponse(TokenPair):
    """Returned by POST /auth/login — token pair plus the authenticated user."""

    user: UserResponse
