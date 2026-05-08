"""Application settings.

Loaded once at startup via :func:`get_settings`. All configuration flows
from environment variables (see ``.env.example``) so the same image runs
unchanged across development, staging, and production.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated

from pydantic import AliasChoices, AnyHttpUrl, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    app_name: str = "dicom-server"
    app_env: Environment = Environment.DEVELOPMENT
    app_debug: bool = False
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    app_log_level: str = "INFO"

    cors_origins: list[AnyHttpUrl] | list[str] = Field(
        default_factory=lambda: ["http://localhost:4200"]
    )

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------
    jwt_secret_key: str = Field(..., min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536
    argon2_parallelism: int = 4

    # ------------------------------------------------------------------
    # Database
    # Accepts Railway's native PG* vars (PGHOST, PGPORT, …) as aliases so
    # no manual variable references are needed in the Railway dashboard.
    # ------------------------------------------------------------------
    postgres_user: str = Field(
        default="dicom",
        validation_alias=AliasChoices("POSTGRES_USER", "PGUSER"),
    )
    postgres_password: str = Field(
        default="dicom",
        validation_alias=AliasChoices("POSTGRES_PASSWORD", "PGPASSWORD"),
    )
    postgres_db: str = Field(
        default="dicom",
        validation_alias=AliasChoices("POSTGRES_DB", "PGDATABASE"),
    )
    postgres_host: str = Field(
        default="pgbouncer",
        validation_alias=AliasChoices("POSTGRES_HOST", "PGHOST"),
    )
    postgres_port: int = Field(
        default=6432,
        validation_alias=AliasChoices("POSTGRES_PORT", "PGPORT"),
    )

    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: int = 30

    # ------------------------------------------------------------------
    # Redis
    # Accepts Railway's native REDIS* vars as aliases.
    # ------------------------------------------------------------------
    redis_host: str = Field(
        default="redis",
        validation_alias=AliasChoices("REDIS_HOST", "REDISHOST"),
    )
    redis_port: int = Field(
        default=6379,
        validation_alias=AliasChoices("REDIS_PORT", "REDISPORT"),
    )
    redis_db: int = 0
    redis_password: str | None = Field(
        default=None,
        validation_alias=AliasChoices("REDIS_PASSWORD", "REDISPASSWORD"),
    )

    # ------------------------------------------------------------------
    # MinIO / object storage
    # ------------------------------------------------------------------
    minio_endpoint: str = "minio:9000"
    minio_external_endpoint: str = "http://localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_secure: bool = False
    minio_bucket_dicom: str = "dicom-files"
    minio_bucket_thumbnails: str = "thumbnails"
    minio_presigned_url_expire_seconds: int = 3600

    # ------------------------------------------------------------------
    # Celery
    # ------------------------------------------------------------------
    # When not explicitly set, these are derived from REDIS_HOST/PORT so that
    # managed Redis services (Render, Railway, etc.) only need those two vars.
    celery_broker_url: str = ""
    celery_result_backend: str = ""
    celery_task_default_queue: str = "default"

    # ------------------------------------------------------------------
    # Uploads
    # ------------------------------------------------------------------
    max_upload_size_mb: int = 500

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------
    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @model_validator(mode="after")
    def _derive_celery_urls(self) -> Settings:
        """Build Celery broker/backend URLs from Redis connection settings.

        Allows managed Redis services (Render, Railway, …) to supply only
        REDIS_HOST and REDIS_PORT without needing a full composed URL.
        Explicit CELERY_BROKER_URL / CELERY_RESULT_BACKEND env vars take
        precedence when set (e.g. local dev via .env).
        """
        auth = f":{self.redis_password}@" if self.redis_password else ""
        base = f"redis://{auth}{self.redis_host}:{self.redis_port}"
        if not self.celery_broker_url:
            self.celery_broker_url = f"{base}/1"
        if not self.celery_result_backend:
            self.celery_result_backend = f"{base}/2"
        return self

    # ------------------------------------------------------------------
    # Derived values
    # ------------------------------------------------------------------
    @property
    def database_url_async(self) -> str:
        """Async DSN used by the FastAPI application (asyncpg driver)."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def database_url_sync(self) -> str:
        """Sync DSN used by Alembic migrations (psycopg2 driver).

        Migrations bypass PgBouncer and go directly to PostgreSQL on 5432 so
        DDL statements are not affected by transaction pooling limitations.
        """
        return (
            f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
            f"@{self._postgres_direct_host}:5432/{self.postgres_db}"
        )

    @property
    def _postgres_direct_host(self) -> str:
        # Alembic should target postgres directly, not the pooler.
        return "postgres" if self.postgres_host == "pgbouncer" else self.postgres_host

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        return self.app_env is Environment.PRODUCTION


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached :class:`Settings` instance."""
    return Settings()


SettingsDep = Annotated[Settings, "settings"]
