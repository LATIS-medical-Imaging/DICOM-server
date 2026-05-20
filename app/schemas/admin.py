"""Pydantic schemas for admin-only user management endpoints."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class CreateUserRequest(BaseModel):
    """Body of POST /admin/users — create a doctor or admin account."""

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=200)
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    role: Literal["doctor", "admin"] = "doctor"
    title: str | None = Field(None, max_length=20)
    specialty: str | None = Field(None, max_length=100)
    institution: str | None = Field(None, max_length=200)
    phone: str | None = Field(None, max_length=20)


class UpdateUserRequest(BaseModel):
    """Body of PATCH /admin/users/{id} — all fields optional."""

    first_name: str | None = Field(None, min_length=1, max_length=100)
    last_name: str | None = Field(None, min_length=1, max_length=100)
    role: Literal["doctor", "admin"] | None = None
    is_active: bool | None = None
    title: str | None = None
    specialty: str | None = None
    institution: str | None = None
    phone: str | None = None


class ResetPasswordRequest(BaseModel):
    """Body of POST /admin/users/{id}/reset-password."""

    new_password: str = Field(..., min_length=8, max_length=200)


class AdminUserResponse(BaseModel):
    """Full user record returned to admins (still no password hash)."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
    first_name: str
    last_name: str
    title: str | None
    specialty: str | None
    institution: str | None
    phone: str | None


class AdminUserListResponse(BaseModel):
    """Paginated list of users."""

    items: list[AdminUserResponse]
    total: int
