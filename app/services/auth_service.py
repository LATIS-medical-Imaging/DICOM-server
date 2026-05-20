"""Authentication service — login, refresh-token rotation, logout.

The access token is stateless (JWT, signature + ``exp``); the refresh token is
both a JWT and a row in ``user_sessions``. On every refresh we mint a new pair,
mark the old session row inactive, and link the family. If a *revoked* refresh
token is ever presented again (= theft suspected) we revoke the entire family,
forcing the user to log in again. This is the OWASP-recommended refresh-token
reuse-detection pattern.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AuthenticationError
from app.core.logging import get_logger
from app.core.security import (
    PasswordHasherService,
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.db.models.user import User
from app.db.models.user_session import UserSession
from app.schemas.auth import AuthResponse, TokenPair, UserResponse

logger = get_logger(__name__)


def _hash_refresh_token(token: str) -> str:
    """SHA-256 of the JWT — stored in user_sessions for O(1) lookup + revocation.

    We never store the raw refresh token; if the DB leaks, an attacker still
    needs the JWT signing key to forge one.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AuthService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._hasher = PasswordHasherService(settings)

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------
    async def login(
        self,
        *,
        email: str,
        password: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> AuthResponse:
        user = await self._get_user_by_email(email)
        if user is None or not self._hasher.verify(password, user.password_hash):
            # Same message for both branches — don't leak whether the email exists.
            raise AuthenticationError("Email or password is incorrect.")
        if not user.is_active:
            raise AuthenticationError("Account is disabled.")

        # Optional: opportunistic Argon2 parameter upgrade.
        if self._hasher.needs_rehash(user.password_hash):
            user.password_hash = self._hasher.hash(password)

        user.last_login_at = datetime.now(UTC)

        token_family = uuid.uuid4()
        access, refresh = await self._issue_token_pair(
            user_id=user.id,
            token_family=token_family,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._db.commit()

        logger.info("auth_login", user_id=str(user.id), family=str(token_family))
        return AuthResponse(
            access_token=access,
            refresh_token=refresh,
            user=UserResponse.model_validate(user),
        )

    # ------------------------------------------------------------------
    # Refresh (with rotation + reuse detection)
    # ------------------------------------------------------------------
    async def refresh(
        self,
        *,
        refresh_token: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> TokenPair:
        payload = self._decode_refresh(refresh_token)
        user_id = uuid.UUID(payload["sub"])
        family_id = uuid.UUID(payload["family"])
        token_hash = _hash_refresh_token(refresh_token)

        session = await self._get_session_by_hash(token_hash)

        # Case 1 — token unknown to us: either forged, or already pruned. Reject.
        if session is None:
            logger.warning("auth_refresh_unknown_token", user_id=str(user_id))
            raise AuthenticationError("Invalid refresh token.")

        # Case 2 — token belongs to a *revoked* row: classic reuse-detection signal.
        # Kill the whole family so the legitimate user is forced to re-login,
        # and the attacker who stole the leaked token also loses access.
        if not session.is_active or session.revoked_at is not None:
            await self._revoke_family(family_id)
            await self._db.commit()
            logger.warning(
                "auth_refresh_reuse_detected",
                user_id=str(user_id),
                family=str(family_id),
            )
            raise AuthenticationError("Refresh token has been revoked.")

        # Case 3 — expired (defence-in-depth; JWT exp should already catch this).
        if session.expires_at <= datetime.now(UTC):
            session.is_active = False
            session.revoked_at = datetime.now(UTC)
            await self._db.commit()
            raise AuthenticationError("Refresh token has expired.")

        # Happy path — rotate: revoke this row, issue a new pair in the same family.
        session.is_active = False
        session.revoked_at = datetime.now(UTC)

        access, refresh = await self._issue_token_pair(
            user_id=user_id,
            token_family=family_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._db.commit()

        return TokenPair(access_token=access, refresh_token=refresh)

    # ------------------------------------------------------------------
    # Logout
    # ------------------------------------------------------------------
    async def logout(self, *, refresh_token: str) -> None:
        """Revoke the entire family the presented refresh token belongs to.

        Best-effort: never raises on invalid input. The frontend always calls
        this on user-initiated logout, and we don't want a stale or malformed
        token to keep the session alive.
        """
        try:
            payload = decode_token(refresh_token, settings=self._settings)
            if payload.get("type") != TokenType.REFRESH or "family" not in payload:
                return
            family_id = uuid.UUID(payload["family"])
        except (AuthenticationError, ValueError, KeyError):
            return

        await self._revoke_family(family_id)
        await self._db.commit()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    async def _get_user_by_email(self, email: str) -> User | None:
        # users.email is case-insensitively unique by convention — normalise here.
        normalised = email.strip().lower()
        result = await self._db.execute(
            select(User).where(User.email == normalised, User.deleted_at.is_(None))
        )
        return result.scalar_one_or_none()

    async def _get_session_by_hash(self, token_hash: str) -> UserSession | None:
        result = await self._db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == token_hash)
        )
        return result.scalar_one_or_none()

    async def _revoke_family(self, family_id: uuid.UUID) -> None:
        now = datetime.now(UTC)
        await self._db.execute(
            update(UserSession)
            .where(UserSession.token_family == family_id, UserSession.is_active.is_(True))
            .values(is_active=False, revoked_at=now)
        )

    async def _issue_token_pair(
        self,
        *,
        user_id: uuid.UUID,
        token_family: uuid.UUID,
        ip_address: str | None,
        user_agent: str | None,
    ) -> tuple[str, str]:
        access = create_access_token(user_id, settings=self._settings)
        refresh = create_refresh_token(user_id, token_family=token_family, settings=self._settings)
        expires_at = datetime.now(UTC) + timedelta(
            days=self._settings.jwt_refresh_token_expire_days
        )
        session = UserSession(
            user_id=user_id,
            refresh_token_hash=_hash_refresh_token(refresh),
            token_family=token_family,
            ip_address=ip_address,
            user_agent=(user_agent or None) if user_agent is None else user_agent[:500],
            is_active=True,
            expires_at=expires_at,
        )
        self._db.add(session)
        return access, refresh

    def _decode_refresh(self, token: str) -> dict[str, Any]:
        payload = decode_token(token, settings=self._settings)
        if payload.get("type") != TokenType.REFRESH:
            raise AuthenticationError("Invalid refresh token.")
        if "sub" not in payload or "family" not in payload:
            raise AuthenticationError("Invalid refresh token.")
        return payload

    # ------------------------------------------------------------------
    # /auth/me
    # ------------------------------------------------------------------
    async def get_user(self, user_id: uuid.UUID) -> User:
        result = await self._db.execute(
            select(User).where(User.id == user_id, User.deleted_at.is_(None))
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise AuthenticationError("User no longer exists or is disabled.")
        return user
