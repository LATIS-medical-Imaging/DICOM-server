"""Pydantic schemas for the sharing module.

Wire shapes for the share-create flow (one call, N grantees → N share rows +
N chat messages), the chat-embedded share card payload, and the
incoming/outgoing list views.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.chat import UserSearchResult

SharePermissionLiteral = Literal["view", "annotate", "manage"]
ShareStatusLiteral = Literal["pending", "active", "revoked", "expired"]
ShareTargetTypeLiteral = Literal["study", "series"]


# ── Create payload ──────────────────────────────────────────────────────


class CreateShareRequest(BaseModel):
    """One call → N shares (one per ``grantee_ids`` entry).

    Validates server-side:
    * Caller owns ``target_id`` *or* holds an ACTIVE share on it with
      ``permission='manage'`` (re-share path).
    * Every grantee is an accepted friend of the caller.
    * Re-shares cannot grant a permission higher than the caller's own.
    * Phases (parent_series_id IS NOT NULL) are not shareable.
    """

    target_type: ShareTargetTypeLiteral
    target_id: uuid.UUID
    grantee_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=50)
    permission: SharePermissionLiteral = "view"
    message: str | None = Field(default=None, max_length=500)
    expires_at: datetime | None = None


class AcceptShareRequest(BaseModel):
    """Empty body — endpoint is identified by the path id."""


# ── Embedded share card (chat) ──────────────────────────────────────────


class ShareTargetSummary(BaseModel):
    """Minimal metadata the chat card needs to render the share badge.

    Always populated server-side from the joined Study/Series row, so the
    receiver doesn't need read access to the underlying resource just to see
    the preview.  ``modality`` and ``study_date`` are best-effort and may be
    None for sparse uploads.
    """

    target_type: ShareTargetTypeLiteral
    target_id: uuid.UUID
    name: str
    modality: str | None
    study_date: datetime | None
    instance_count: int


class ShareEmbeddedDto(BaseModel):
    """Share payload embedded in a chat ``MessageResponse`` (``message.share``).

    Carries enough state for the share-card UI to render: who/what/permission,
    current status (so the card can show Import / Imported / Revoked), and
    the lineage flag so the UI can render a "re-shared from …" hint.
    """

    id: uuid.UUID
    grantor: UserSearchResult
    grantee_id: uuid.UUID
    permission: SharePermissionLiteral
    status: ShareStatusLiteral
    target: ShareTargetSummary
    parent_share_id: uuid.UUID | None
    created_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None


# ── Full share response (lists, accept, create) ─────────────────────────


class ShareResponse(BaseModel):
    """One Share row with its embedded target summary and grantor/grantee
    profiles.  Returned by create / accept / list endpoints."""

    id: uuid.UUID
    grantor: UserSearchResult
    grantee: UserSearchResult
    permission: SharePermissionLiteral
    status: ShareStatusLiteral
    message: str | None
    target: ShareTargetSummary
    parent_share_id: uuid.UUID | None
    created_at: datetime
    accepted_at: datetime | None
    revoked_at: datetime | None
    expires_at: datetime | None


class ShareListResponse(BaseModel):
    items: list[ShareResponse]
    total: int


# ── Sidebar share source (study DTO extension) ──────────────────────────


class ShareSourceDto(BaseModel):
    """Populated on ``StudyResponse.share_source`` when the caller is *not*
    the study owner.  Drives the sidebar's "Shared by Dr X · Annotate"
    subtitle and the viewer's permission gates."""

    share_id: uuid.UUID
    grantor: UserSearchResult
    permission: SharePermissionLiteral


# ── Resolve forward refs ────────────────────────────────────────────────
# MessageResponse.share is declared with a forward-ref string
# ``"ShareEmbeddedDto | None"`` to break the import cycle (shares.py imports
# from chat.py).  Now that both modules are loaded, rebuild the model so
# Pydantic can resolve the annotation.
from app.schemas.chat import MessageResponse  # noqa: E402
from app.schemas.studies import StudyResponse  # noqa: E402

MessageResponse.model_rebuild()
StudyResponse.model_rebuild()
