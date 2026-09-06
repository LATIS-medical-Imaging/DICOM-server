"""Message endpoints — send, list a thread, list conversations, unread count."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DBSession, SettingsDep, StorageDep
from app.schemas.chat import (
    ConversationListResponse,
    MessageListResponse,
    MessageResponse,
    SendMessageRequest,
    UnreadCountResponse,
    VoiceClipUploadRequest,
    VoiceClipUploadResponse,
)
from app.services.chat_service import ChatService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def send_message(
    body: SendMessageRequest,
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> MessageResponse:
    service = ChatService(db, get_ws_hub(), storage, settings)
    return await service.send_message(user.id, body.recipient_id, body.body, body.voice)


@router.post(
    "/voice/presign",
    response_model=VoiceClipUploadResponse,
    status_code=status.HTTP_200_OK,
    summary="Get a presigned PUT URL for a voice recording",
)
async def presign_voice_clip(
    body: VoiceClipUploadRequest,
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> VoiceClipUploadResponse:
    """Step 1 of sending a voice note.

    The browser PUTs the recording straight to MinIO with the returned URL, then
    quotes ``object_key`` back in ``POST /messages`` — audio bytes never pass
    through this server, the same split the DICOM upload path uses.
    """
    service = ChatService(db, get_ws_hub(), storage, settings)
    return await service.create_voice_upload(user.id, body.mime_type)


@router.get("", response_model=MessageListResponse)
async def list_messages(
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
    with_: Annotated[uuid.UUID, Query(alias="with", description="Peer user id.")],
    before: Annotated[
        datetime | None,
        Query(description="Cursor: only messages sent before this timestamp."),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> MessageListResponse:
    """Newest-first slice of the 1:1 thread.

    As a side effect, every message addressed to the caller in the loaded
    window is marked as read — this is what clears the unread badge.
    """
    service = ChatService(db, get_ws_hub(), storage, settings)
    items = await service.list_messages(user.id, with_, before, limit)
    return MessageListResponse(items=items)


@router.get("/conversations", response_model=ConversationListResponse)
async def list_conversations(
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> ConversationListResponse:
    service = ChatService(db, get_ws_hub(), storage, settings)
    items = await service.list_conversations(user.id)
    return ConversationListResponse(items=items)


@router.get("/unread-count", response_model=UnreadCountResponse)
async def unread_count(
    user: CurrentUser,
    db: DBSession,
    storage: StorageDep,
    settings: SettingsDep,
) -> UnreadCountResponse:
    service = ChatService(db, get_ws_hub(), storage, settings)
    return UnreadCountResponse(count=await service.unread_count(user.id))
