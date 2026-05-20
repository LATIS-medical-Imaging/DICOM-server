"""Bootstrap the first admin user.

Run once after ``alembic upgrade head`` to seed the very first administrator:

    ADMIN_BOOTSTRAP_EMAIL=admin@example.com \\
    ADMIN_BOOTSTRAP_PASSWORD=<strong-pw> \\
    python -m app.cli.seed_admin

Idempotent: if a user with the given email already exists, nothing is changed
and the script exits 0. All other accounts are created through admin-only
flows once auth is live — there is no public ``/auth/register`` endpoint.
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.core.security import PasswordHasherService
from app.db.models.user import User, UserRole
from app.db.session import SessionLocal

logger = get_logger(__name__)


def _read_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"error: {name} is required.", file=sys.stderr)
        sys.exit(2)
    return value


async def _seed() -> int:
    email = _read_env("ADMIN_BOOTSTRAP_EMAIL").lower()
    password = _read_env("ADMIN_BOOTSTRAP_PASSWORD")
    first_name = os.environ.get("ADMIN_BOOTSTRAP_FIRST_NAME", "Admin").strip() or "Admin"
    last_name = os.environ.get("ADMIN_BOOTSTRAP_LAST_NAME", "User").strip() or "User"

    if len(password) < 12:
        print("error: ADMIN_BOOTSTRAP_PASSWORD must be at least 12 characters.", file=sys.stderr)
        return 2

    settings = get_settings()
    hasher = PasswordHasherService(settings)

    async with SessionLocal() as db:
        existing = await db.execute(select(User).where(User.email == email))
        if existing.scalar_one_or_none() is not None:
            print(f"User '{email}' already exists — nothing to do.")
            return 0

        user = User(
            email=email,
            password_hash=hasher.hash(password),
            email_verified=True,
            first_name=first_name,
            last_name=last_name,
            role=UserRole.ADMIN,
            is_active=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        print(f"Admin '{email}' created (id={user.id}).")
        logger.info("admin_seeded", email=email, user_id=str(user.id))
        return 0


def main() -> None:
    configure_logging(get_settings())
    sys.exit(asyncio.run(_seed()))


if __name__ == "__main__":
    main()
