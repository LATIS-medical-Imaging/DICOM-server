"""Sharing — business logic.

A Share row grants a friend access to a study or series owned by the caller
(or re-shares one they themselves hold with MANAGE).  Every share is created
PENDING — the grantee must accept (POST /shares/{id}/accept) before it grants
visibility.  Revoking any link cascades REVOKED down the ``parent_share_id``
tree atomically so downstream re-shares lose access in the same transaction.

WebSocket fan-out lives here (after every DB commit) so the chat UI updates
in real time:

* ``message.new``    → each grantee + sender echo on create.  We piggyback on
                       the existing chat envelope: a Message row with
                       ``share_id`` set carries the embedded ``share`` DTO,
                       so the chat reducer renders the bubble as a share card
                       without any new envelope type or state branch.
* ``share.accepted`` → both parties on accept (mutates the existing message's
                       embedded share.status).
* ``share.removed``  → both parties on revoke (one envelope per revoked id).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.db.models.friendship import Friendship, FriendshipStatus
from app.db.models.message import Message
from app.db.models.series import Series
from app.db.models.share import Share, SharePermission, ShareStatus
from app.db.models.study import Study
from app.db.models.user import User
from app.schemas.chat import MessageResponse, UserSearchResult
from app.schemas.shares import (
    CreateShareRequest,
    ShareEmbeddedDto,
    ShareListResponse,
    ShareResponse,
    ShareTargetSummary,
    ShareTargetTypeLiteral,
)
from app.services.ws_hub import WebSocketHub


def _canonical_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) < str(b) else (b, a)


class ShareService:
    def __init__(self, db: AsyncSession, hub: WebSocketHub) -> None:
        self._db = db
        self._hub = hub

    # ── Create ──────────────────────────────────────────────────────────

    async def create_shares(
        self,
        caller_id: uuid.UUID,
        payload: CreateShareRequest,
    ) -> list[ShareResponse]:
        """Bulk-create one Share + one Message per grantee, in one transaction.

        Validates: caller can share the target (owner or MANAGE), grantees are
        all accepted friends, target exists and isn't a phase, re-share
        permission doesn't exceed caller's own.
        """
        if not payload.grantee_ids:
            raise ValidationError("At least one grantee is required.")
        if caller_id in payload.grantee_ids:
            raise ValidationError("You cannot share with yourself.")
        if len(set(payload.grantee_ids)) != len(payload.grantee_ids):
            raise ValidationError("Duplicate grantee.")

        # Resolve target + caller's permission on it.
        target_summary, caller_permission, parent_share_id = await self._resolve_target(
            caller_id, payload.target_type, payload.target_id
        )

        # Re-share permission ceiling.
        if SharePermission.rank(payload.permission) > SharePermission.rank(caller_permission):
            raise PermissionDeniedError(
                "Cannot grant a permission higher than your own on this resource."
            )

        # Validate every grantee is an accepted friend.
        await self._assert_all_friends(caller_id, payload.grantee_ids)

        # Validate every grantee actually exists and is active.
        grantee_rows = await self._load_users(payload.grantee_ids)
        if len(grantee_rows) != len(payload.grantee_ids):
            raise NotFoundError("One or more grantees not found.")

        caller_user = await self._db.get(User, caller_id)
        if caller_user is None:
            raise NotFoundError("Caller not found.")

        # ── Atomic: N shares + N messages ────────────────────────────────
        created_shares: list[Share] = []
        created_messages: list[Message] = []
        for grantee in grantee_rows:
            share = Share(
                grantor_id=caller_id,
                grantee_id=grantee.id,
                study_id=payload.target_id if payload.target_type == "study" else None,
                series_id=payload.target_id if payload.target_type == "series" else None,
                instance_id=None,
                parent_share_id=parent_share_id,
                permission=payload.permission,
                message=payload.message,
                expires_at=payload.expires_at,
                status=ShareStatus.PENDING,
            )
            self._db.add(share)
            created_shares.append(share)
        await self._db.flush()  # populate share.id for the message FK

        for share in created_shares:
            msg = Message(
                sender_id=caller_id,
                recipient_id=share.grantee_id,
                body=payload.message or "",
                share_id=share.id,
            )
            self._db.add(msg)
            created_messages.append(msg)

        await self._db.commit()
        for share in created_shares:
            await self._db.refresh(share)
        for msg in created_messages:
            await self._db.refresh(msg)

        responses = [
            self._to_share_response(s, caller_user, grantee, target_summary)
            for s, grantee in zip(created_shares, grantee_rows, strict=False)
        ]

        # ── WS fan-out: piggyback on message.new so the chat reducer renders
        #    each share as a normal message bubble (with .share embedded → the
        #    bubble template switches to share-card markup).
        for share, msg, grantee, _ in zip(
            created_shares, created_messages, grantee_rows, responses, strict=False
        ):
            embedded = self.to_embedded_dto(share, caller_user, target_summary)
            message_response = MessageResponse(
                id=msg.id,
                sender_id=msg.sender_id,
                recipient_id=msg.recipient_id,
                body=msg.body,
                sent_at=msg.sent_at,
                read_at=msg.read_at,
                share=embedded,
            )
            envelope = {
                "type": "message.new",
                "data": message_response.model_dump(mode="json"),
            }
            await self._hub.deliver(grantee.id, envelope)
            await self._hub.deliver(caller_id, envelope)  # sender echo
        return responses

    # ── Accept ──────────────────────────────────────────────────────────

    async def accept_share(
        self,
        caller_id: uuid.UUID,
        share_id: uuid.UUID,
    ) -> ShareResponse:
        share = await self._get_or_404(share_id)
        if share.grantee_id != caller_id:
            raise NotFoundError("Share not found.")
        if share.status == ShareStatus.REVOKED or share.status == ShareStatus.EXPIRED:
            raise ConflictError("Share is no longer available.")

        if share.status == ShareStatus.PENDING:
            share.status = ShareStatus.ACTIVE
            share.accepted_at = datetime.now(UTC)
            await self._db.commit()
            await self._db.refresh(share)

        response = await self._materialise_response(share)

        payload = {
            "share_id": str(share.id),
            "accepted_at": share.accepted_at.isoformat() if share.accepted_at else None,
        }
        await self._hub.deliver(share.grantee_id, {"type": "share.accepted", "data": payload})
        await self._hub.deliver(share.grantor_id, {"type": "share.accepted", "data": payload})
        return response

    # ── Revoke / dismiss ────────────────────────────────────────────────

    async def revoke_share(
        self,
        caller_id: uuid.UUID,
        share_id: uuid.UUID,
    ) -> None:
        share = await self._get_or_404(share_id)
        is_grantor = share.grantor_id == caller_id
        is_grantee = share.grantee_id == caller_id
        if not (is_grantor or is_grantee):
            raise NotFoundError("Share not found.")
        if share.status in (ShareStatus.REVOKED, ShareStatus.EXPIRED):
            return  # idempotent

        # Collect this share + every descendant via BFS through parent_share_id.
        # We mark them all REVOKED in one transaction and notify each
        # (grantor, grantee) pair after the commit.
        to_revoke: list[Share] = [share]
        frontier_ids: list[uuid.UUID] = [share.id]
        while frontier_ids:
            children = (
                (
                    await self._db.execute(
                        select(Share).where(Share.parent_share_id.in_(frontier_ids))
                    )
                )
                .scalars()
                .all()
            )
            to_revoke.extend(children)
            frontier_ids = [
                c.id
                for c in children
                if c.status == ShareStatus.ACTIVE or c.status == ShareStatus.PENDING
            ]

        now = datetime.now(UTC)
        for s in to_revoke:
            if s.status in (ShareStatus.PENDING, ShareStatus.ACTIVE):
                s.status = ShareStatus.REVOKED
                s.revoked_at = now
        await self._db.commit()

        by_role: Literal["grantor", "grantee"] = "grantor" if is_grantor else "grantee"
        for s in to_revoke:
            payload = {"share_id": str(s.id), "by_role": by_role}
            await self._hub.deliver(s.grantor_id, {"type": "share.removed", "data": payload})
            await self._hub.deliver(s.grantee_id, {"type": "share.removed", "data": payload})

    # ── Listing ─────────────────────────────────────────────────────────

    async def list_incoming(
        self,
        caller_id: uuid.UUID,
        status: str | None,
        limit: int,
        offset: int,
    ) -> ShareListResponse:
        return await self._list(
            caller_id, role="grantee", status=status, limit=limit, offset=offset
        )

    async def list_outgoing(
        self,
        caller_id: uuid.UUID,
        status: str | None,
        limit: int,
        offset: int,
    ) -> ShareListResponse:
        return await self._list(
            caller_id, role="grantor", status=status, limit=limit, offset=offset
        )

    # ── Visibility helper (used by StudyService) ────────────────────────

    async def caller_permission_for_study(
        self,
        caller_id: uuid.UUID,
        study_id: uuid.UUID,
    ) -> str | None:
        """Return the caller's max active permission on this study, or None.

        Considers study-level shares only.  Used by the studies endpoint to
        populate ``StudyResponse.share_source`` and by permission gates in
        phase_service / processing_service.
        """
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(Share).where(
                Share.grantee_id == caller_id,
                Share.study_id == study_id,
                Share.status == ShareStatus.ACTIVE,
                or_(Share.expires_at.is_(None), Share.expires_at > now),
            )
        )
        shares = list(result.scalars().all())
        if not shares:
            return None
        return max((s.permission for s in shares), key=SharePermission.rank)

    async def caller_permission_for_series(
        self,
        caller_id: uuid.UUID,
        series: Series,
    ) -> str | None:
        """Max active permission on a series, considering both the series's
        direct share and the parent study's share (study-level shares cover
        every series within)."""
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(Share).where(
                Share.grantee_id == caller_id,
                or_(Share.series_id == series.id, Share.study_id == series.study_id),
                Share.status == ShareStatus.ACTIVE,
                or_(Share.expires_at.is_(None), Share.expires_at > now),
            )
        )
        shares = list(result.scalars().all())
        if not shares:
            return None
        return max((s.permission for s in shares), key=SharePermission.rank)

    async def active_share_row_for_series(
        self,
        caller_id: uuid.UUID,
        series: Series,
    ) -> Share | None:
        """Most-permissive active share row covering ``series``, or None.

        Considers the series's own share *and* the parent study's — a
        study-level share covers every series in it. The study-only variant
        below misses series-level grants entirely, which is why the viewer
        needs this one to decide what the caller may do with a given series.
        """
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(Share).where(
                Share.grantee_id == caller_id,
                or_(Share.series_id == series.id, Share.study_id == series.study_id),
                Share.status == ShareStatus.ACTIVE,
                or_(Share.expires_at.is_(None), Share.expires_at > now),
            )
        )
        shares = list(result.scalars().all())
        if not shares:
            return None
        # Rank by permission, then prefer the more specific (series-level) row
        # so "remove from sidebar" revokes the grant that actually put it there.
        return max(
            shares,
            key=lambda s: (SharePermission.rank(s.permission), s.series_id is not None),
        )

    async def active_share_row_for_study(
        self,
        caller_id: uuid.UUID,
        study_id: uuid.UUID,
    ) -> Share | None:
        """Return the most-permissive active share row on ``study_id`` for
        ``caller_id``, or None.  Used when building ``StudyResponse.share_source``
        so the frontend can show the grantor name + permission badge."""
        now = datetime.now(UTC)
        result = await self._db.execute(
            select(Share).where(
                Share.grantee_id == caller_id,
                Share.study_id == study_id,
                Share.status == ShareStatus.ACTIVE,
                or_(Share.expires_at.is_(None), Share.expires_at > now),
            )
        )
        shares = list(result.scalars().all())
        if not shares:
            return None
        return max(shares, key=lambda s: SharePermission.rank(s.permission))

    # ── Internals ───────────────────────────────────────────────────────

    async def _list(
        self,
        caller_id: uuid.UUID,
        *,
        role: Literal["grantor", "grantee"],
        status: str | None,
        limit: int,
        offset: int,
    ) -> ShareListResponse:
        col = Share.grantor_id if role == "grantor" else Share.grantee_id
        filters = [col == caller_id]
        if status is not None:
            filters.append(Share.status == status)

        rows = (
            (
                await self._db.execute(
                    select(Share)
                    .where(*filters)
                    .order_by(Share.created_at.desc())
                    .offset(offset)
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )

        responses = [await self._materialise_response(s) for s in rows]
        return ShareListResponse(items=responses, total=len(responses))

    async def _resolve_target(
        self,
        caller_id: uuid.UUID,
        target_type: ShareTargetTypeLiteral,
        target_id: uuid.UUID,
    ) -> tuple[ShareTargetSummary, str, uuid.UUID | None]:
        """Validate the target + return (summary, caller_permission, parent_share_id).

        ``caller_permission`` is either 'manage' (a synthetic ceiling for owners,
        but represented as ``MANAGE`` for the rank check) or the actual permission
        from an active MANAGE share.  Returns 'owner' as a sentinel so the rank
        check still works (owner outranks everything via SharePermission.rank
        applied through the same map).
        """
        if target_type == "study":
            study = await self._db.get(Study, target_id)
            if study is None or study.deleted_at is not None:
                raise NotFoundError("Study not found.")
            summary = ShareTargetSummary(
                target_type="study",
                target_id=study.id,
                name=study.study_description or study.accession_number or study.study_instance_uid,
                modality=study.modality,
                study_date=(
                    datetime.combine(study.study_date, datetime.min.time())
                    if study.study_date
                    else None
                ),
                instance_count=study.total_instance_count,
            )
            if study.owner_id == caller_id:
                return summary, SharePermission.MANAGE, None  # owner ⇒ no parent
            # Re-share path: caller must hold an active MANAGE share on this study
            caller_share = await self.active_share_row_for_study(caller_id, study.id)
            if caller_share is None or caller_share.permission != SharePermission.MANAGE:
                raise PermissionDeniedError("You don't have permission to share this study.")
            return summary, caller_share.permission, caller_share.id

        # series
        series = await self._db.get(Series, target_id)
        if series is None:
            raise NotFoundError("Series not found.")
        if series.parent_series_id is not None:
            raise ValidationError("Phases cannot be shared.")
        study = await self._db.get(Study, series.study_id)
        if study is None or study.deleted_at is not None:
            raise NotFoundError("Series not found.")
        summary = ShareTargetSummary(
            target_type="series",
            target_id=series.id,
            name=series.series_description or f"Series {series.series_number or '?'}",
            modality=series.modality,
            study_date=(
                datetime.combine(study.study_date, datetime.min.time())
                if study.study_date
                else None
            ),
            instance_count=series.instance_count,
        )
        if study.owner_id == caller_id:
            return summary, SharePermission.MANAGE, None
        # Re-share path on a series: any series-or-study MANAGE share counts.
        caller_permission = await self.caller_permission_for_series(caller_id, series)
        if caller_permission != SharePermission.MANAGE:
            raise PermissionDeniedError("You don't have permission to share this series.")
        # Pick the parent_share_id from the matching MANAGE share (prefer the
        # most-specific — series-level over study-level).
        now = datetime.now(UTC)
        parent_share = (
            (
                await self._db.execute(
                    select(Share)
                    .where(
                        Share.grantee_id == caller_id,
                        Share.permission == SharePermission.MANAGE,
                        Share.status == ShareStatus.ACTIVE,
                        or_(Share.expires_at.is_(None), Share.expires_at > now),
                        or_(Share.series_id == series.id, Share.study_id == series.study_id),
                    )
                    .order_by(Share.series_id.is_(None))  # series-scoped first
                )
            )
            .scalars()
            .first()
        )
        return summary, caller_permission, (parent_share.id if parent_share else None)

    async def _assert_all_friends(
        self,
        caller_id: uuid.UUID,
        grantee_ids: list[uuid.UUID],
    ) -> None:
        # Build per-grantee canonical pair set; query for accepted rows.
        canonical = [_canonical_pair(caller_id, g) for g in grantee_ids]
        rows = (
            await self._db.execute(
                select(Friendship.user_a_id, Friendship.user_b_id).where(
                    Friendship.status == FriendshipStatus.ACCEPTED,
                    or_(
                        *[
                            (Friendship.user_a_id == a) & (Friendship.user_b_id == b)
                            for a, b in canonical
                        ]
                    ),
                )
            )
        ).all()
        found_pairs = {(r[0], r[1]) for r in rows}
        for a, b in canonical:
            if (a, b) not in found_pairs:
                raise ValidationError(
                    "All recipients must be accepted friends before you can share."
                )

    async def _load_users(self, ids: Iterable[uuid.UUID]) -> list[User]:
        rows = (await self._db.execute(select(User).where(User.id.in_(list(ids))))).scalars().all()
        return list(rows)

    async def _get_or_404(self, share_id: uuid.UUID) -> Share:
        share = await self._db.get(Share, share_id)
        if share is None:
            raise NotFoundError("Share not found.")
        return share

    async def _materialise_response(self, share: Share) -> ShareResponse:
        grantor = await self._db.get(User, share.grantor_id)
        grantee = await self._db.get(User, share.grantee_id)
        if grantor is None or grantee is None:
            raise NotFoundError("Share parties not found.")
        target_summary = await self._build_target_summary(share)
        return self._to_share_response(share, grantor, grantee, target_summary)

    async def _build_target_summary(self, share: Share) -> ShareTargetSummary:
        if share.study_id is not None:
            study = await self._db.get(Study, share.study_id)
            if study is None:
                raise NotFoundError("Shared study no longer exists.")
            return ShareTargetSummary(
                target_type="study",
                target_id=study.id,
                name=study.study_description or study.accession_number or study.study_instance_uid,
                modality=study.modality,
                study_date=(
                    datetime.combine(study.study_date, datetime.min.time())
                    if study.study_date
                    else None
                ),
                instance_count=study.total_instance_count,
            )
        # series
        assert share.series_id is not None
        series = await self._db.get(Series, share.series_id)
        if series is None:
            raise NotFoundError("Shared series no longer exists.")
        study = await self._db.get(Study, series.study_id)
        return ShareTargetSummary(
            target_type="series",
            target_id=series.id,
            name=series.series_description or f"Series {series.series_number or '?'}",
            modality=series.modality,
            study_date=(
                datetime.combine(study.study_date, datetime.min.time())
                if study and study.study_date
                else None
            ),
            instance_count=series.instance_count,
        )

    @staticmethod
    def _to_share_response(
        share: Share,
        grantor: User,
        grantee: User,
        target: ShareTargetSummary,
    ) -> ShareResponse:
        return ShareResponse(
            id=share.id,
            grantor=UserSearchResult.model_validate(grantor),
            grantee=UserSearchResult.model_validate(grantee),
            permission=share.permission,
            status=share.status,
            message=share.message,
            target=target,
            parent_share_id=share.parent_share_id,
            created_at=share.created_at,
            accepted_at=share.accepted_at,
            revoked_at=share.revoked_at,
            expires_at=share.expires_at,
        )

    @staticmethod
    def to_embedded_dto(
        share: Share,
        grantor: User,
        target: ShareTargetSummary,
    ) -> ShareEmbeddedDto:
        return ShareEmbeddedDto(
            id=share.id,
            grantor=UserSearchResult.model_validate(grantor),
            grantee_id=share.grantee_id,
            permission=share.permission,
            status=share.status,
            target=target,
            parent_share_id=share.parent_share_id,
            created_at=share.created_at,
            accepted_at=share.accepted_at,
            revoked_at=share.revoked_at,
        )
