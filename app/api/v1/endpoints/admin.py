"""Admin-only user management endpoints.

All routes require ``role = admin``.  There is no public registration endpoint —
only admins may create new accounts (doctors or additional admins).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from app.api.deps import CurrentAdmin, DBSession
from app.core.config import get_settings
from app.core.exceptions import ConflictError
from app.core.security import PasswordHasherService
from app.db.models.user import User, UserRole
from app.schemas.admin import (
    AdminUserListResponse,
    AdminUserResponse,
    CreateUserRequest,
    ResetPasswordRequest,
    UpdateUserRequest,
)

router = APIRouter()

_VALID_ROLES = {UserRole.ADMIN, UserRole.DOCTOR}


async def _get_user_or_404(user_id: uuid.UUID, db: DBSession) -> User:
    result = await db.execute(select(User).where(User.id == user_id, User.deleted_at.is_(None)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.get("/users", response_model=AdminUserListResponse)
async def list_users(
    _admin: CurrentAdmin,
    db: DBSession,
    role: str | None = Query(None, description="Filter by role (doctor / admin)."),
    active_only: bool = Query(False, description="Exclude deactivated accounts."),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> AdminUserListResponse:
    """Return all non-deleted users, optionally filtered."""
    base = select(User).where(User.deleted_at.is_(None))
    if role is not None and role in _VALID_ROLES:
        base = base.where(User.role == role)
    if active_only:
        base = base.where(User.is_active.is_(True))

    count_q = select(func.count()).select_from(base.subquery())
    total = (await db.execute(count_q)).scalar_one()

    rows = (
        (await db.execute(base.order_by(User.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )

    return AdminUserListResponse(
        items=[AdminUserResponse.model_validate(u) for u in rows],
        total=total,
    )


@router.post("/users", response_model=AdminUserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    _admin: CurrentAdmin,
    db: DBSession,
) -> AdminUserResponse:
    """Create a new user account (doctor or admin)."""
    settings = get_settings()

    # Uniqueness check — same email must not already exist (even soft-deleted).
    existing = (
        await db.execute(select(User).where(User.email == body.email.lower()))
    ).scalar_one_or_none()
    if existing is not None:
        raise ConflictError(f"A user with email '{body.email}' already exists.")

    hasher = PasswordHasherService(settings)
    user = User(
        email=body.email.lower(),
        password_hash=hasher.hash(body.password),
        email_verified=True,  # admin-created accounts are pre-verified
        first_name=body.first_name,
        last_name=body.last_name,
        role=body.role,
        title=body.title,
        specialty=body.specialty,
        institution=body.institution,
        phone=body.phone,
        is_active=True,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return AdminUserResponse.model_validate(user)


@router.get("/users/{user_id}", response_model=AdminUserResponse)
async def get_user(
    user_id: uuid.UUID,
    _admin: CurrentAdmin,
    db: DBSession,
) -> AdminUserResponse:
    """Fetch a single user by ID."""
    user = await _get_user_or_404(user_id, db)
    return AdminUserResponse.model_validate(user)


@router.patch("/users/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: uuid.UUID,
    body: UpdateUserRequest,
    _admin: CurrentAdmin,
    db: DBSession,
) -> AdminUserResponse:
    """Partially update a user (name, role, active status, profile fields)."""
    user = await _get_user_or_404(user_id, db)

    updates = body.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(user, field, value)

    await db.commit()
    await db.refresh(user)
    return AdminUserResponse.model_validate(user)


@router.post("/users/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: uuid.UUID,
    body: ResetPasswordRequest,
    _admin: CurrentAdmin,
    db: DBSession,
) -> None:
    """Set a new password for any user (admin-only — no old-password check)."""
    settings = get_settings()
    user = await _get_user_or_404(user_id, db)
    hasher = PasswordHasherService(settings)
    user.password_hash = hasher.hash(body.new_password)
    await db.commit()


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: uuid.UUID,
    _admin: CurrentAdmin,
    db: DBSession,
) -> None:
    """Deactivate a user account (soft-disable, not permanent deletion).

    An active admin cannot deactivate their own account to prevent lockout.
    """
    user = await _get_user_or_404(user_id, db)

    # Guard: require at least one other active admin to remain.
    if user.role == UserRole.ADMIN and user.is_active:
        other_admins = (
            await db.execute(
                select(func.count()).where(
                    User.role == UserRole.ADMIN,
                    User.is_active.is_(True),
                    User.id != user_id,
                    User.deleted_at.is_(None),
                )
            )
        ).scalar_one()
        if other_admins == 0:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot deactivate the last active admin account.",
            )

    user.is_active = False
    await db.commit()
