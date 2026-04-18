"""Application settings.

Loaded once at startup via :func:`get_settings`. All configuration flows
from environment variables (see ``.env.example``) so the same image runs
unchanged across development, staging, and production.
"""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Annotated

from pydantic import AnyHttpUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(str, Enum):
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
    # ------------------------------------------------------------------
    postgres_user: str = "dicom"
    postgres_password: str = "dicom"
    postgres_db: str = "dicom"
    postgres_host: str = "pgbouncer"
    postgres_port: int = 6432

    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout: int = 30

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str | None = None

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
    celery_broker_url: str = "redis://redis:6379/1"
    celery_result_backend: str = "redis://redis:6379/2"
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
    return Settings()  # type: ignore[call-arg]


SettingsDep = Annotated[Settings, "settings"]
