"""FastAPI application entry point."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

# Load `.env` before `app.config` builds cached settings (FERNET_KEY, DATABASE_URL, …).
_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env")

from app.config import BASE_DIR, settings
from app.database import init_db
from app.deps import templates
from app.routes import audit, settings as settings_routes, vendor


def _setup_logging() -> None:
    log_dir = BASE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, (settings.LOG_LEVEL or "INFO").upper(), logging.INFO)
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    root = logging.getLogger()
    root.setLevel(level)
    fh = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
    fh.setFormatter(logging.Formatter(fmt))
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter(fmt))
    root.handlers.clear()
    root.addHandler(fh)
    root.addHandler(sh)


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
    description="Internal VRA tool for Compliance — OSINT vendor risk reports.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_dir = BASE_DIR / "static"
static_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(vendor.router)
app.include_router(settings_routes.router)
app.include_router(audit.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    """Liveness/readiness probe."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index_page(request: Request) -> HTMLResponse:
    """Vendor form and batch upload."""
    return templates.TemplateResponse(
        request,
        "index.html",
        {"active": "generate"},
    )


@app.get("/result", response_class=HTMLResponse)
def result_page(
    request: Request,
    pdf: str = "",
    vendor: str = "",
    audit_id: str = "",
) -> HTMLResponse:
    """Download page after successful generation."""
    return templates.TemplateResponse(
        request,
        "result.html",
        {
            "active": "generate",
            "pdf": pdf,
            "vendor": vendor,
            "audit_id": audit_id,
        },
    )
