"""Application configuration, paths, and environment loading."""

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def get_base_dir() -> Path:
    """Project root directory (parent of ``app``)."""
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = get_base_dir()


def get_writable_dir() -> Path:
    """Return the writable base directory.

    On AWS Lambda (Netlify Functions) the project root is read-only;
    only ``/tmp`` is writable.  Locally the project root is used as-is.
    """
    if os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        tmp = Path("/tmp")
        tmp.mkdir(exist_ok=True)
        return tmp
    return BASE_DIR


WRITABLE_DIR: Path = get_writable_dir()


class AppSettings(BaseSettings):
    """Settings loaded from environment and optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    FERNET_KEY: str = ""
    GEMINI_API_KEY: str = ""
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = ""
    # Hybrid pipeline: collectors + compact synthesis (no search grounding).
    USE_HYBRID_MODE: bool = False

    def resolved_database_url(self) -> str:
        """
        Resolve the SQLAlchemy database URL.

        Relative SQLite paths under ``data/`` are anchored to ``WRITABLE_DIR``
        so the DB is placed under ``/tmp`` on Lambda (read-only filesystem)
        and under the project root locally.
        """
        raw = (self.DATABASE_URL or "").strip()
        if not raw:
            path = WRITABLE_DIR / "data" / "vra.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path.resolve()}"

        if raw.startswith("sqlite:///./"):
            relative = raw.removeprefix("sqlite:///./")
            path = (WRITABLE_DIR / relative).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"

        return raw


@lru_cache
def get_settings() -> AppSettings:
    """Return cached application settings (singleton per process)."""
    return AppSettings()


settings: AppSettings = get_settings()
