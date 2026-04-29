# Code conventions

## Python
- `from __future__ import annotations` at the top of every Python file
- ORM columns: always `Mapped[T]` + `mapped_column()` — SQLAlchemy 2.0 style, no legacy `Column()`
- Schemas (Pydantic) live in `app/schemas/` — keep strictly separate from ORM models
- Logging: `get_logger(__name__)` from `app.core.logging` (structlog), key=value pairs — never `print()`
- Raise `HTTPException` directly in endpoints; use `app.core.exceptions` for domain errors

## Sync vs async endpoints
- `async def` for endpoints that hit the DB or Redis
- Plain `def` for endpoints that only call the MinIO SDK — the SDK is blocking and doesn't benefit from async

## Dependency injection
Use the three `Annotated` aliases from `app/api/deps.py`:
- `DBSession` — async SQLAlchemy session
- `SettingsDep` — cached Settings instance
- `StorageDep` — StorageService instance

## Alembic
- The `import app.db.models  # noqa: F401` line in `alembic/env.py` is intentional — ruff will try to remove it as unused but it's required for autogenerate to detect model changes. Never remove it.
- Migration files in `alembic/versions/` are excluded from lint and mypy

## Dev tools
- All dev tool versions are pinned with `==` in `pyproject.toml` (ruff, black, mypy, pytest, etc.)
- Never change them to `>=` — this causes local/CI drift where CI installs a newer version with different rules
- After changing `pyproject.toml`, run `make build-tools` to rebuild the tools Docker image

## Adding new endpoints
1. Create `app/api/v1/endpoints/<domain>.py`
2. Register the router in `app/api/v1/router.py`
