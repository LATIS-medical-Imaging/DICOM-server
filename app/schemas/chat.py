"""Pydantic schemas for the chat module (search, friendships, messages, WS)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, EmailStr, Field

# Share schemas reference chat's UserSearchResult, so we can't import the
# other way here — use a forward ref string annotation on MessageResponse.share
# and call ``MessageResponse.model_rebuild()`` from ``shares.py`` once both
# modules are loaded.
if TYPE_CHECKING:
    from app.schemas.shares import ShareEmbeddedDto


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
    """Chat message wire shape.

    When ``share`` is set the message is a share attachment (chat bubble
    renders a share card and ``body`` is an optional caption).  When
    ``share`` is None the message is a plain text bubble.
    """

    model_config = {"from_attributes": True}

    id: uuid.UUID
    sender_id: uuid.UUID
    recipient_id: uuid.UUID
    body: str
    sent_at: datetime
    read_at: datetime | None
    # Forward ref — resolved by ``ShareEmbeddedDto.model_rebuild`` call at the
    # bottom of ``app/schemas/shares.py``.
    share: ShareEmbeddedDto | None = None


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


class WsTicketResponse(BaseModel):
    """Single-use WebSocket handshake ticket.

    Issued by ``POST /ws-ticket`` and consumed exactly once by the WebSocket
    gateway during the upgrade handshake.  Expires after 30 seconds whether
    used or not.
    """

    ticket: str


WsEnvelopeType = Literal[
    # Generic chat — also used for shares (a message with embedded share row
    # = a share-card bubble; the frontend reducer doesn't need to branch).
    "message.new",
    "friendship.invited",
    "friendship.accepted",
    "friendship.removed",
    # Share lifecycle — mutate the existing share-bubble in place.
    "share.accepted",
    "share.removed",
]


class WsEnvelope(BaseModel):
    """Standard server→client push payload."""

    type: WsEnvelopeType
    data: dict[str, Any]
