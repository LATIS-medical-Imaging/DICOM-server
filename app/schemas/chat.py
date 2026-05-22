"""Pydantic schemas for the chat module (search, friendships, messages, WS)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


class UserSearchResult(BaseModel):
    """A doctor returned by ``GET /users/search``."""

    model_config = {"from_attributes": True}

    id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str
    title: str | None
    specialty: str | None


class UserSearchListResponse(BaseModel):
    items: list[UserSearchResult]


class InviteRequest(BaseModel):
    user_id: uuid.UUID


class FriendshipResponse(BaseModel):
    """A friendship row plus the *other* user's profile, so the UI never has to
    do a second lookup to render an invite or a friend chip."""

    id: uuid.UUID
    status: Literal["pending", "accepted"]
    direction: Literal["incoming", "outgoing"]
    peer: UserSearchResult
    requested_by: uuid.UUID
    created_at: datetime
    updated_at: datetime


class FriendshipListResponse(BaseModel):
    items: list[FriendshipResponse]


class SendMessageRequest(BaseModel):
    recipient_id: uuid.UUID
    body: str = Field(..., min_length=1, max_length=4000)


class MessageResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    sender_id: uuid.UUID
    recipient_id: uuid.UUID
    body: str
    sent_at: datetime
    read_at: datetime | None


class MessageListResponse(BaseModel):
    items: list[MessageResponse]


class ConversationResponse(BaseModel):
    """A friend + the latest message exchanged with them + unread count."""

    peer: UserSearchResult
    last_message: MessageResponse | None
    unread_count: int


class ConversationListResponse(BaseModel):
    items: list[ConversationResponse]


class UnreadCountResponse(BaseModel):
    count: int


WsEnvelopeType = Literal[
    "message.new",
    "friendship.invited",
    "friendship.accepted",
    "friendship.removed",
]


class WsEnvelope(BaseModel):
    """Standard server→client push payload."""

    type: WsEnvelopeType
    data: dict[str, Any]
