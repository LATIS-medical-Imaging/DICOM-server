# Code conventions

## Python
- `from __future__ import annotations` at top of every file
- ORM: `Mapped[T]` + `mapped_column()` only — no legacy `Column()`
- Schemas (Pydantic) in `app/schemas/` — never mix with ORM models
- Logging: `get_logger(__name__)` (structlog, key=value) — never `print()`
- `HTTPException` in endpoints; `app.core.exceptions` for domain errors

## Typing
- Full annotations on all functions — avoid `Any` except for untyped third-party libs (e.g. Celery `self`)
- `str | None` not `Optional[str]`; `list[str]` not `List[str]`
- Pydantic schemas: `model_config = {"from_attributes": True}` for ORM compatibility
- `# type: ignore[error-code]` — always include the error code, never bare ignore

## Endpoints
- `async def` when hitting DB/Redis; plain `def` for blocking MinIO SDK calls
- Query params with `Annotated`: put **only metadata** (alias, description, validators like `ge=`, `le=`) inside `Query()`; put the **default value** on the `=` side of the parameter — never inside `Query()`. FastAPI ≥ 0.115 raises `AssertionError` at startup if a default is set inside `Query()` when using `Annotated`.

  ```python
  # Correct
  limit: Annotated[int, Query(ge=1, le=200)] = 50
  before: Annotated[datetime | None, Query(description="...")] = None

  # Wrong — AssertionError at startup
  limit: Annotated[int, Query(50, ge=1, le=200)]
  ```

- DI aliases from `app/api/deps.py`: `DBSession`, `SettingsDep`, `StorageDep`, `CurrentUser`, `CurrentAdmin`
- New endpoint: create `app/api/v1/endpoints/<domain>.py`, register in `app/api/v1/router.py`

## Service layer
Business logic in `app/services/` — never in endpoints or tasks.
- **Endpoints** — validate input, call one service method, return response
- **Tasks** — manage job status + error handling, delegate work to a service
- **Services** — own all DB queries, business logic, cross-cutting concerns

Services are classes; deps injected via `__init__`. Endpoints: `Service(db).method(...)`. Tasks: instantiate inside `with get_sync_db() as db:`.

## Comments & docstrings
- Comment "why" only — never "what"
- Single-line docstring for non-obvious return values or tricky contracts; omit if self-explanatory
- No step-by-step inline comments, no section dividers (`# ---`), no AI-style intros

## Alembic
- `import app.db.models  # noqa: F401` in `alembic/env.py` — never remove, required for autogenerate
- `alembic/versions/` excluded from lint and mypy

## Dev tools
- Versions pinned with `==` in `pyproject.toml` — never `>=` (causes local/CI drift)
- Run `make build-tools` after any `pyproject.toml` change
