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
    # Optional Serper.dev (Google Search API) key. When blank, the web-search
    # collector falls back to DuckDuckGo. This field MUST exist even when unset:
    # web_search_collector reads app_settings.SERPER_API_KEY unconditionally, so
    # a missing attribute raised AttributeError and silently crashed the entire
    # collector (emptying the evidence pack). Also surfaced via serper_configured.
    SERPER_API_KEY: str = ""
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    DATABASE_URL: str = ""
    # Hybrid pipeline: run Python collectors (live GST API, Google News RSS,
    # web search) first, then a single Gemini synthesis call. This is the
    # documented/intended mode (.env.example and README both set it true) and
    # the ONLY path that surfaces collector evidence — without it, a bare name
    # like "KINGFISHER" yields an all-zero report because the legacy path leans
    # entirely on the LLM. Default True so every environment uses it unless an
    # operator explicitly sets USE_HYBRID_MODE=false to fall back to the legacy
    # two-pass + Google-Search-grounding path.
    USE_HYBRID_MODE: bool = True
    # Fully deterministic, LLM-free mode. When False, NO Gemini / OpenRouter /
    # any external LLM call is made during report generation: the pipeline runs
    # collectors → deterministic risk framework (app.core.risk) → templated
    # narrative (app.core.narrative) → PDF, and requires no API key at all.
    # Every score, recommendation and sentence is traceable to cited evidence.
    # When True (default) the documented hybrid LLM-assisted path is used.
    USE_LLM: bool = True

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
