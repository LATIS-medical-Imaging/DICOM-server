"""Pydantic schemas for the chat module (search, friendships, messages, WS)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

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


class VoiceClipUploadRequest(BaseModel):
    """Ask for a slot to upload a recording into."""

    mime_type: str = Field(..., max_length=100, description="Container the recorder produced.")


class VoiceClipUploadResponse(BaseModel):
    """Where to PUT the recording, and the key to quote back when sending."""

    upload_url: str
    object_key: str
    bucket: str
    expires_in: int


class VoiceClipRef(BaseModel):
    """A recording the client has already uploaded, referenced by object key.

    ``size_bytes`` is deliberately absent: the server reads the real size back
    from MinIO, because a presigned PUT enforces no length of its own and a
    client-declared one would be the wrong thing to check a quota against.
    """

    object_key: str = Field(..., max_length=512)
    mime_type: str = Field(..., max_length=100)
    duration_ms: int = Field(..., gt=0)


class VoiceClipDto(BaseModel):
    """A voice note as the chat bubble receives it.

    ``url`` is a presigned GET minted per read, so it expires with
    ``minio_presigned_url_expire_seconds`` — a client holding a message list
    open past that must refetch the thread rather than cache the URL.
    """

    url: str
    mime_type: str
    duration_ms: int
    size_bytes: int


class SendMessageRequest(BaseModel):
    """Text bubble, voice note, or a voice note with a text caption.

    ``body`` is optional only because a voice note may stand alone; a message
    carrying neither is rejected.
    """

    recipient_id: uuid.UUID
    body: str = Field(default="", max_length=4000)
    voice: VoiceClipRef | None = None

    @model_validator(mode="after")
    def _require_content(self) -> SendMessageRequest:
        if not self.body.strip() and self.voice is None:
            raise ValueError("A message must have a body, a voice clip, or both.")
        return self


class MessageResponse(BaseModel):
    """Chat message wire shape.

    When ``share`` is set the message is a share attachment (chat bubble
    renders a share card and ``body`` is an optional caption).  When
    ``voice`` is set it is a voice note (audio player, ``body`` again an
    optional caption).  With both None the message is a plain text bubble.
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
    voice: VoiceClipDto | None = None


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
