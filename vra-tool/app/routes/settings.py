"""Settings UI and JSON API."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.crypto import CryptoError, decrypt_secret, encrypt_secret, get_fernet
from app.core.kv_store import get_value, set_value
from app.core.llm.factory import get_provider
from app.core.llm.gemini import GeminiProvider
from app.core.llm.openrouter import OpenRouterProvider
from app.core.quota import attach_usage_to_keys
from app.core.vra_service import build_key_candidates, build_gemini_key_candidates
from app.database import get_db
from app.deps import templates
from app.models import ApiKey
from app.schemas import SettingsSaveRequest, SettingsStateResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["settings"])


def _fernet_configured() -> bool:
    try:
        get_fernet()
        return True
    except CryptoError:
        return False


def _settings_state(db: Session) -> SettingsStateResponse:
    limit = int(get_value(db, "daily_quota_limit", "1500"))
    provider = (get_value(db, "llm_provider", "gemini") or "gemini").strip().lower()

    # Show keys for whichever provider is currently selected
    key_rows = list(
        db.execute(
            select(ApiKey).where(
                ApiKey.provider == provider,
                ApiKey.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )
    keys = attach_usage_to_keys(db, key_rows, daily_limit=limit)

    last_test = get_value(db, "status_last_test_iso", "") or None
    last_ok_raw = get_value(db, "status_last_test_ok", "")
    last_ok: bool | None
    if last_ok_raw.lower() in ("true", "1"):
        last_ok = True
    elif last_ok_raw.lower() in ("false", "0"):
        last_ok = False
    else:
        last_ok = None
    last_gen = get_value(db, "status_last_generation_iso", "") or None
    last_msg = get_value(db, "status_last_test_message", "") or None
    # Serper key can be in DB (encrypted) or env var
    serper_db = get_value(db, "serper_api_key_enc", "")
    serper_env = (app_settings.SERPER_API_KEY or "").strip()
    serper_configured = bool(serper_env) or bool(serper_db)

    return SettingsStateResponse(
        llm_provider=provider,
        llm_model=get_value(db, "llm_model", "gemini-2.0-flash"),
        temperature=float(get_value(db, "llm_temperature", "0.2")),
        max_output_tokens=int(get_value(db, "llm_max_output_tokens", "16384")),
        daily_quota_limit=limit,
        keys=keys,
        last_test_at=last_test,
        last_test_ok=last_ok,
        last_test_message=last_msg,
        last_generation_at=last_gen,
        fernet_configured=_fernet_configured(),
        serper_configured=serper_configured,
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"active": "settings"},
    )


@router.get("/api/settings", response_model=SettingsStateResponse)
def api_settings_get(db: Session = Depends(get_db)) -> SettingsStateResponse:
    return _settings_state(db)


@router.post("/api/settings")
def api_settings_save(body: SettingsSaveRequest, db: Session = Depends(get_db)) -> dict:
    if not _fernet_configured():
        raise HTTPException(
            status_code=400,
            detail="FERNET_KEY is not configured; cannot encrypt API keys.",
        )
    set_value(db, "llm_provider", body.llm_provider)
    set_value(db, "llm_model", body.llm_model)
    set_value(db, "llm_temperature", str(body.temperature))
    set_value(db, "llm_max_output_tokens", str(body.max_output_tokens))
    set_value(db, "daily_quota_limit", str(body.daily_quota_limit))

    # Save Serper API key encrypted in DB if provided
    if body.serper_api_key and body.serper_api_key.strip():
        enc = encrypt_secret(body.serper_api_key.strip())
        set_value(db, "serper_api_key_enc", enc)

    provider = (body.llm_provider or "gemini").lower()
    for kp in body.keys:
        token = encrypt_secret(kp.key)
        if kp.id is not None:
            row = db.get(ApiKey, kp.id)
            if row is None or row.provider != provider:
                raise HTTPException(status_code=404, detail=f"Unknown key id {kp.id}")
            row.encrypted_key = token
            row.label = kp.label
            db.add(row)
        else:
            db.add(
                ApiKey(
                    provider=provider,
                    label=kp.label,
                    encrypted_key=token,
                )
            )
    db.commit()
    return {"ok": True}


@router.post("/api/settings/test")
async def api_settings_test(db: Session = Depends(get_db)) -> dict:
    provider = (get_value(db, "llm_provider", "gemini") or "gemini").strip().lower()
    candidates = build_key_candidates(db, provider)

    if not candidates:
        raise HTTPException(
            status_code=400,
            detail=f"No {provider.capitalize()} API keys available to test.",
        )

    model = get_value(db, "llm_model", "gemini-2.0-flash")
    temperature = float(get_value(db, "llm_temperature", "0.2"))
    max_out = int(get_value(db, "llm_max_output_tokens", "16384"))
    _row, secret, label = candidates[0]

    prov = get_provider(
        provider,
        api_key=secret,
        model=model,
        temperature=temperature,
        max_output_tokens=max_out,
    )

    detail: str | None = None
    try:
        if isinstance(prov, (GeminiProvider, OpenRouterProvider)):
            ok, detail = await prov.test_connection_detail()
        else:
            ok = await prov.test_connection()
    except Exception as exc:
        logger.warning("Test connection error: %s", exc)
        ok = False
        detail = str(exc)

    set_value(db, "status_last_test_iso", dt.datetime.utcnow().isoformat())
    set_value(db, "status_last_test_ok", "true" if ok else "false")
    if detail:
        set_value(db, "status_last_test_message", detail[:2000])
    db.commit()
    return {"ok": ok, "key_label_used": label, "message": detail}


@router.post("/settings/test")
async def settings_test_alias(db: Session = Depends(get_db)) -> dict:
    """Alias matching stakeholder path ``POST /settings/test``."""
    return await api_settings_test(db)
