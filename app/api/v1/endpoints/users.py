"""User-facing endpoints (non-admin) — currently just doctor search."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.schemas.chat import UserSearchListResponse
from app.services.chat_service import ChatService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


@router.get("/search", response_model=UserSearchListResponse)
async def search_doctors(
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    q: str = Query("", min_length=0, max_length=120, description="Name or email substring."),
    limit: int = Query(20, ge=1, le=50),
) -> UserSearchListResponse:
    """Case-insensitive search across first_name / last_name / email.

    Restricted to other active doctors — admin accounts and the caller are
    never returned.
    """
    service = ChatService(db, get_ws_hub(), storage, settings)
    items = await service.search_doctors(user.id, q, limit)
    return UserSearchListResponse(items=items)
