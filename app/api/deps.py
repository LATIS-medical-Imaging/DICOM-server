"""Shared FastAPI dependencies (DB session, settings, storage)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_db
from app.services.storage_service import StorageService


def get_storage(settings: Annotated[Settings, Depends(get_settings)]) -> StorageService:
    return StorageService(settings)


DBSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
StorageDep = Annotated[StorageService, Depends(get_storage)]
