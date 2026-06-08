"""Application configuration, paths, and environment loading."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def get_base_dir() -> Path:
    """Project root directory (parent of ``app``)."""
    return Path(__file__).resolve().parent.parent


BASE_DIR: Path = get_base_dir()


class AppSettings(BaseSettings):
    """Settings loaded from environment and optional ``.env`` file."""

    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    FERNET_KEY: str = ""
    GEMINI_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""
    # Serper.dev Google Search API key — free tier: 2,500 searches/month.
    # Sign up at https://serper.dev — when set, replaces unreliable DDG searches.
    SERPER_API_KEY: str = ""
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = ""
    # Hybrid pipeline: collectors + compact synthesis (no search grounding).
    USE_HYBRID_MODE: bool = False

    def resolved_database_url(self) -> str:
        """
        Resolve the SQLAlchemy database URL.

        Relative SQLite paths under ``data/`` are anchored to ``BASE_DIR`` so
        the DB location does not depend on the process working directory.
        """
        raw = (self.DATABASE_URL or "").strip()
        if not raw:
            path = BASE_DIR / "data" / "vra.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path.resolve()}"

        if raw.startswith("sqlite:///./"):
            relative = raw.removeprefix("sqlite:///./")
            path = (BASE_DIR / relative).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path}"

        return raw


@lru_cache
def get_settings() -> AppSettings:
    """Return cached application settings (singleton per process)."""
    return AppSettings()


settings: AppSettings = get_settings()
