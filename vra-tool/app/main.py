"""FastAPI application entry point."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load `.env` before `app.config` builds cached settings (FERNET_KEY, DATABASE_URL, …).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

from app.config import WRITABLE_DIR, settings
from app.database import init_db
from app.routes import audit, settings as settings_routes, vendor

_IS_LAMBDA = bool(os.getenv("AWS_LAMBDA_FUNCTION_NAME"))


def _setup_logging() -> None:
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    # Always log to stderr (works on Lambda and locally).
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(fmt))
    root.addHandler(sh)
    # Add a file handler only when the filesystem is writable (i.e. not on Lambda).
    if not _IS_LAMBDA:
        log_dir = WRITABLE_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        root.addHandler(fh)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize resources on startup."""
    _setup_logging()
    init_db()
    log = logging.getLogger(__name__)
    log.info("VRA application started (DB initialized)")
    yield


app = FastAPI(
    title="Paytm Vendor Risk Assessment",
    description="Internal VRA tool for Compliance — OSINT vendor risk reports (JSON API).",
    version="1.0.0",
    lifespan=lifespan,
)

# The React frontend (Vite dev on :5173, Netlify in prod) calls this API across origins.
# CORS_ALLOW_ORIGINS is a comma-separated list. "*" disables credentialed CORS.
_default_dev_origins = [
    "http://127.0.0.1:5173",
    "http://localhost:5173",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]
_env_origins = os.getenv("CORS_ALLOW_ORIGINS", "").strip()
if _env_origins:
    _cors_origins = [o.strip() for o in _env_origins.split(",") if o.strip()]
elif _IS_LAMBDA:
    _cors_origins = ["*"]
else:
    _cors_origins = _default_dev_origins

_wildcard = _cors_origins == ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=not _wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(vendor.router)
app.include_router(settings_routes.router)
app.include_router(audit.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok"}
