"""Share endpoints — create / list incoming + outgoing / accept / revoke.

All routes require ``CurrentUser``.  Business logic and WS fan-out live in
:class:`app.services.share_service.ShareService`; these handlers just thread
the request through the service.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Query, status

from app.api.deps import CurrentUser, DBSession
from app.schemas.shares import (
    CreateShareRequest,
    ShareListResponse,
    ShareResponse,
)
from app.services.share_service import ShareService
from app.services.ws_hub import get_ws_hub

router = APIRouter()


def _service(db: DBSession) -> ShareService:
    return ShareService(db, get_ws_hub())


@router.post(
    "",
    response_model=ShareListResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Share a study or series with one or more friends",
)
async def create_shares(
    payload: CreateShareRequest,
    user: CurrentUser,
    db: DBSession,
) -> ShareListResponse:
    items = await _service(db).create_shares(user.id, payload)
    return ShareListResponse(items=items, total=len(items))


@router.get(
    "/incoming",
    response_model=ShareListResponse,
    summary="Shares received by the caller",
)
async def list_incoming(
    user: CurrentUser,
    db: DBSession,
    share_status: Literal["pending", "active", "revoked", "expired"] | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ShareListResponse:
    return await _service(db).list_incoming(user.id, share_status, limit, offset)


@router.get(
    "/outgoing",
    response_model=ShareListResponse,
    summary="Shares created by the caller",
)
async def list_outgoing(
    user: CurrentUser,
    db: DBSession,
    share_status: Literal["pending", "active", "revoked", "expired"] | None = Query(
        default=None, alias="status"
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> ShareListResponse:
    return await _service(db).list_outgoing(user.id, share_status, limit, offset)


@router.post(
    "/{share_id}/accept",
    response_model=ShareResponse,
    summary="Accept (import) a pending share",
)
async def accept_share(
    share_id: uuid.UUID,
    user: CurrentUser,
    db: DBSession,
) -> ShareResponse:
    """Idempotent — returns 200 if the share is already ACTIVE."""
    return await _service(db).accept_share(user.id, share_id)


@router.delete(
    "/{share_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke (grantor) or dismiss (grantee) a share",
)
async def revoke_share(
    share_id: uuid.UUID,
    user: CurrentUser,
    db: DBSession,
) -> None:
    """Cascades REVOKED to every descendant in the ``parent_share_id`` tree
    so re-shares lose access in the same transaction."""
    await _service(db).revoke_share(user.id, share_id)
