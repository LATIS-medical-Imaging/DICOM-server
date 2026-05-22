"""Business logic for the chat module.

Everything authorization-related lives here so the FastAPI endpoints stay thin
and uniform:

* doctor search (excludes self, excludes admins)
* friendship lifecycle (invite / accept / reject / unfriend)
* message send + list (gated by accepted friendship)
* conversation listing with last-message preview + unread counts
* unread badge count

WebSocket fan-out happens *after* the DB commit so a client never gets pushed a
notification for a write that's not yet durable.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, PermissionDeniedError
from app.db.models.friendship import Friendship, FriendshipStatus
from app.db.models.message import Message
from app.db.models.user import User, UserRole
from app.schemas.chat import (
    ConversationResponse,
    FriendshipResponse,
    MessageResponse,
    UserSearchResult,
)
from app.services.ws_hub import WebSocketHub


def _canonical_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Order a pair so ``user_a_id < user_b_id`` matches the table check."""
    return (a, b) if str(a) < str(b) else (b, a)


class ChatService:
    def __init__(self, db: AsyncSession, hub: WebSocketHub) -> None:
        self._db = db
        self._hub = hub

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------
    async def search_doctors(
        self,
        current_user_id: uuid.UUID,
        query: str,
        limit: int,
    ) -> list[UserSearchResult]:
        q = query.strip()
        if not q:
            return []
        pattern = f"%{q.lower()}%"
        stmt = (
            select(User)
            .where(
                User.id != current_user_id,
                User.role == UserRole.DOCTOR,
                User.is_active.is_(True),
                User.deleted_at.is_(None),
                or_(
                    func.lower(User.first_name).like(pattern),
                    func.lower(User.last_name).like(pattern),
                    func.lower(User.email).like(pattern),
                ),
            )
            .order_by(User.first_name, User.last_name)
            .limit(limit)
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return [UserSearchResult.model_validate(u) for u in rows]

    # ------------------------------------------------------------------
    # Friendships
    # ------------------------------------------------------------------
    async def invite(
        self,
        current_user_id: uuid.UUID,
        target_user_id: uuid.UUID,
    ) -> FriendshipResponse:
        if current_user_id == target_user_id:
            raise PermissionDeniedError("You cannot invite yourself.")

        target = await self._db.get(User, target_user_id)
        if (
            target is None
            or target.deleted_at is not None
            or not target.is_active
            or target.role != UserRole.DOCTOR
        ):
            raise NotFoundError("Doctor not found.")

        user_a, user_b = _canonical_pair(current_user_id, target_user_id)
        existing = (
            await self._db.execute(
                select(Friendship).where(
                    Friendship.user_a_id == user_a,
                    Friendship.user_b_id == user_b,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise ConflictError("A friendship or pending invitation already exists.")

        friendship = Friendship(
            user_a_id=user_a,
            user_b_id=user_b,
            requested_by=current_user_id,
            status=FriendshipStatus.PENDING,
        )
        self._db.add(friendship)
        await self._db.commit()
        await self._db.refresh(friendship)

        response = await self._to_friendship_response(friendship, current_user_id)

        # Notify the recipient (the peer) that an invite arrived.
        await self._hub.deliver(
            target_user_id,
            {
                "type": "friendship.invited",
                "data": (await self._to_friendship_response(friendship, target_user_id)).model_dump(
                    mode="json"
                ),
            },
        )
        return response

    async def list_friendships(
        self,
        current_user_id: uuid.UUID,
        status: Literal["pending", "accepted"],
    ) -> list[FriendshipResponse]:
        stmt = (
            select(Friendship)
            .where(
                or_(
                    Friendship.user_a_id == current_user_id,
                    Friendship.user_b_id == current_user_id,
                ),
                Friendship.status == status,
            )
            .order_by(Friendship.updated_at.desc())
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return [await self._to_friendship_response(f, current_user_id) for f in rows]

    async def accept(
        self,
        current_user_id: uuid.UUID,
        friendship_id: uuid.UUID,
    ) -> FriendshipResponse:
        friendship = await self._db.get(Friendship, friendship_id)
        if friendship is None:
            raise NotFoundError("Friendship not found.")
        if friendship.status != FriendshipStatus.PENDING:
            raise ConflictError("Friendship is not pending.")
        if friendship.requested_by == current_user_id:
            raise PermissionDeniedError("Only the recipient can accept an invitation.")
        if current_user_id not in (friendship.user_a_id, friendship.user_b_id):
            raise NotFoundError("Friendship not found.")

        friendship.status = FriendshipStatus.ACCEPTED
        await self._db.commit()
        await self._db.refresh(friendship)

        # Notify the original inviter that the invite was accepted.
        inviter_id = friendship.requested_by
        await self._hub.deliver(
            inviter_id,
            {
                "type": "friendship.accepted",
                "data": (await self._to_friendship_response(friendship, inviter_id)).model_dump(
                    mode="json"
                ),
            },
        )
        return await self._to_friendship_response(friendship, current_user_id)

    async def delete_friendship(
        self,
        current_user_id: uuid.UUID,
        friendship_id: uuid.UUID,
    ) -> None:
        friendship = await self._db.get(Friendship, friendship_id)
        if friendship is None:
            raise NotFoundError("Friendship not found.")
        if current_user_id not in (friendship.user_a_id, friendship.user_b_id):
            raise NotFoundError("Friendship not found.")
        # Pending invites can only be rejected by the recipient (not the inviter
        # — that would be a cancel, which we don't support in v1).
        if (
            friendship.status == FriendshipStatus.PENDING
            and friendship.requested_by == current_user_id
        ):
            raise PermissionDeniedError("Only the recipient can reject a pending invitation.")

        peer_id = (
            friendship.user_b_id
            if friendship.user_a_id == current_user_id
            else friendship.user_a_id
        )
        await self._db.delete(friendship)
        await self._db.commit()

        await self._hub.deliver(
            peer_id,
            {
                "type": "friendship.removed",
                "data": {"friendship_id": str(friendship_id)},
            },
        )

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    async def send_message(
        self,
        current_user_id: uuid.UUID,
        recipient_id: uuid.UUID,
        body: str,
    ) -> MessageResponse:
        if current_user_id == recipient_id:
            raise PermissionDeniedError("You cannot message yourself.")
        await self._require_accepted_friendship(current_user_id, recipient_id)

        message = Message(
            sender_id=current_user_id,
            recipient_id=recipient_id,
            body=body,
        )
        self._db.add(message)
        await self._db.commit()
        await self._db.refresh(message)

        response = MessageResponse.model_validate(message)
        envelope = {"type": "message.new", "data": response.model_dump(mode="json")}

        # Echo to the sender's other tabs *and* push to the recipient.
        await self._hub.deliver(current_user_id, envelope)
        await self._hub.deliver(recipient_id, envelope)
        return response

    async def list_messages(
        self,
        current_user_id: uuid.UUID,
        peer_id: uuid.UUID,
        before: datetime | None,
        limit: int,
    ) -> list[MessageResponse]:
        await self._require_accepted_friendship(current_user_id, peer_id)

        conditions = [
            or_(
                and_(Message.sender_id == current_user_id, Message.recipient_id == peer_id),
                and_(Message.sender_id == peer_id, Message.recipient_id == current_user_id),
            )
        ]
        if before is not None:
            conditions.append(Message.sent_at < before)

        stmt = select(Message).where(*conditions).order_by(Message.sent_at.desc()).limit(limit)
        rows = (await self._db.execute(stmt)).scalars().all()

        # Mark every message addressed to the caller in this window as read.
        await self._db.execute(
            update(Message)
            .where(
                Message.sender_id == peer_id,
                Message.recipient_id == current_user_id,
                Message.read_at.is_(None),
            )
            .values(read_at=datetime.now(UTC))
        )
        await self._db.commit()

        return [MessageResponse.model_validate(m) for m in rows]

    async def list_conversations(
        self,
        current_user_id: uuid.UUID,
    ) -> list[ConversationResponse]:
        # Step 1 — all accepted friends.
        friend_rows = await self.list_friendships(current_user_id, "accepted")

        if not friend_rows:
            return []

        peer_ids = [f.peer.id for f in friend_rows]
        friends_by_id = {f.peer.id: f for f in friend_rows}

        # Step 2 — latest message per peer (in either direction).
        peer_col = case(
            (Message.sender_id == current_user_id, Message.recipient_id),
            else_=Message.sender_id,
        ).label("peer_id")

        latest_subq = (
            select(
                peer_col,
                func.max(Message.sent_at).label("max_sent_at"),
            )
            .where(
                or_(
                    Message.sender_id == current_user_id,
                    Message.recipient_id == current_user_id,
                ),
                or_(
                    Message.sender_id.in_(peer_ids),
                    Message.recipient_id.in_(peer_ids),
                ),
            )
            .group_by(peer_col)
            .subquery()
        )

        msg_peer_col = case(
            (Message.sender_id == current_user_id, Message.recipient_id),
            else_=Message.sender_id,
        )
        latest_msgs_stmt = select(Message).join(
            latest_subq,
            and_(
                msg_peer_col == latest_subq.c.peer_id,
                Message.sent_at == latest_subq.c.max_sent_at,
            ),
        )
        latest_msgs = (await self._db.execute(latest_msgs_stmt)).scalars().all()
        last_by_peer: dict[uuid.UUID, Message] = {}
        for m in latest_msgs:
            peer = m.recipient_id if m.sender_id == current_user_id else m.sender_id
            last_by_peer[peer] = m

        # Step 3 — unread count per peer.
        unread_stmt = (
            select(Message.sender_id, func.count(Message.id))
            .where(
                Message.recipient_id == current_user_id,
                Message.read_at.is_(None),
                Message.sender_id.in_(peer_ids),
            )
            .group_by(Message.sender_id)
        )
        unread_by_peer = {row[0]: row[1] for row in (await self._db.execute(unread_stmt)).all()}

        # Step 4 — assemble, sorted by recency (peers with no messages last).
        conversations: list[ConversationResponse] = []
        for peer_id in peer_ids:
            last = last_by_peer.get(peer_id)
            conversations.append(
                ConversationResponse(
                    peer=friends_by_id[peer_id].peer,
                    last_message=MessageResponse.model_validate(last) if last else None,
                    unread_count=int(unread_by_peer.get(peer_id, 0)),
                )
            )
        conversations.sort(
            key=lambda c: (
                c.last_message.sent_at if c.last_message else datetime.min.replace(tzinfo=UTC)
            ),
            reverse=True,
        )
        return conversations

    async def unread_count(self, current_user_id: uuid.UUID) -> int:
        stmt = select(func.count(Message.id)).where(
            Message.recipient_id == current_user_id,
            Message.read_at.is_(None),
        )
        return int((await self._db.execute(stmt)).scalar_one() or 0)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _require_accepted_friendship(
        self,
        a: uuid.UUID,
        b: uuid.UUID,
    ) -> Friendship:
        user_a, user_b = _canonical_pair(a, b)
        friendship = (
            await self._db.execute(
                select(Friendship).where(
                    Friendship.user_a_id == user_a,
                    Friendship.user_b_id == user_b,
                    Friendship.status == FriendshipStatus.ACCEPTED,
                )
            )
        ).scalar_one_or_none()
        if friendship is None:
            raise PermissionDeniedError("You can only message accepted friends.")
        return friendship

    async def _to_friendship_response(
        self,
        friendship: Friendship,
        viewer_id: uuid.UUID,
    ) -> FriendshipResponse:
        peer_id = (
            friendship.user_b_id if friendship.user_a_id == viewer_id else friendship.user_a_id
        )
        peer_user = await self._db.get(User, peer_id)
        if peer_user is None:  # shouldn't happen — FK CASCADE keeps this clean
            raise NotFoundError("Peer user no longer exists.")
        direction: Literal["incoming", "outgoing"] = (
            "outgoing" if friendship.requested_by == viewer_id else "incoming"
        )
        return FriendshipResponse(
            id=friendship.id,
            status=friendship.status,
            direction=direction,
            peer=UserSearchResult.model_validate(peer_user),
            requested_by=friendship.requested_by,
            created_at=friendship.created_at,
            updated_at=friendship.updated_at,
        )
