"""Friendship lifecycle endpoints — invite, list, accept, delete."""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.schemas.chat import (
    FriendshipListResponse,
    FriendshipResponse,
    InviteRequest,
)
from app.services.chat_service import ChatService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


@router.post(
    "/invite",
    response_model=FriendshipResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite(
    body: InviteRequest,
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> FriendshipResponse:
    service = ChatService(db, get_ws_hub(), storage, settings)
    return await service.invite(user.id, body.user_id)


@router.get("", response_model=FriendshipListResponse)
async def list_friendships(
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    status: Literal["pending", "accepted"] = Query("accepted"),
    q: str | None = Query(default=None, description="Search peer by name or email"),
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> FriendshipListResponse:
    """List the caller's friendships filtered by status.

    The share dialog's friend picker uses ``q`` (peer-name/email search) +
    ``limit`` + ``offset`` to paginate; chat overview calls this without
    those params to get the full accepted-friends list.
    """
    service = ChatService(db, get_ws_hub(), storage, settings)
    items = await service.list_friendships(user.id, status, q=q, limit=limit, offset=offset)
    return FriendshipListResponse(items=items)


@router.post("/{friendship_id}/accept", response_model=FriendshipResponse)
async def accept(
    friendship_id: uuid.UUID,
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> FriendshipResponse:
    service = ChatService(db, get_ws_hub(), storage, settings)
    return await service.accept(user.id, friendship_id)


@router.delete("/{friendship_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_friendship(
    friendship_id: uuid.UUID,
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> None:
    """Reject a pending invitation (recipient only) or unfriend an accepted
    friend (either party)."""
    service = ChatService(db, get_ws_hub(), storage, settings)
    await service.delete_friendship(user.id, friendship_id)
