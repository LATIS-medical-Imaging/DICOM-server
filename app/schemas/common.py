"""Shared Pydantic schemas."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ORMModel(BaseModel):
    """Base model for responses derived from SQLAlchemy rows."""

    model_config = ConfigDict(from_attributes=True)


class PaginationParams(BaseModel):
    page: int = Field(1, ge=1)
    per_page: int = Field(20, ge=1, le=100)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.per_page


class Page(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    per_page: int


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class DependencyCheck(BaseModel):
    name: str
    healthy: bool
    detail: str | None = None


class ReadinessResponse(BaseModel):
    status: str
    checks: list[DependencyCheck]
