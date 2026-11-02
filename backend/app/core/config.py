from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# backend/app/core/config.py -> repository root, so the root `.env` is found from any cwd.
ROOT_DIR = Path(__file__).resolve().parents[3]

_POSTGRES_SCHEMES = ("postgresql://", "postgres://")


class Settings(BaseSettings):
    """Runtime configuration: process environment first, then the root `.env` file.

    Nothing secret has a default. An unset value stays empty and the code that needs it must
    fail loudly instead of silently falling back to a guessable credential.
    """

    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_json: bool = False

    database_url: SecretStr = SecretStr("")
    db_pool_size: int = Field(default=5, ge=1, le=50)
    db_max_overflow: int = Field(default=5, ge=0, le=50)
    redis_url: SecretStr = SecretStr("")
    secrets_encryption_key: SecretStr = SecretStr("")

    supabase_url: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_secret: SecretStr = SecretStr("")
    jwt_audience: str = "authenticated"
    jwt_leeway_seconds: int = Field(default=10, ge=0, le=60)

    # Comma separated in the environment. Empty means "no cross-origin access".
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip().rstrip("/") for item in value.split(",") if item.strip()]
        return value

    @field_validator("supabase_url", "supabase_jwks_url")
    @classmethod
    def _clean_url(cls, value: str) -> str:
        return value.strip().rstrip("/")

    @property
    def sqlalchemy_database_url(self) -> str:
        """DATABASE_URL normalised for SQLAlchemy with psycopg 3, the driver this project ships."""
        url = self.database_url.get_secret_value().strip()
        for scheme in _POSTGRES_SCHEMES:
            if url.startswith(scheme):
                return "postgresql+psycopg://" + url[len(scheme) :]
        return url

    @property
    def jwt_issuer(self) -> str | None:
        return f"{self.supabase_url}/auth/v1" if self.supabase_url else None

    @property
    def jwks_url(self) -> str | None:
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()
