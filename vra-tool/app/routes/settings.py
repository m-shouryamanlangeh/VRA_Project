"""Settings UI and JSON API."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import CryptoError, encrypt_secret, get_fernet
from app.core.kv_store import get_value, set_value
from app.core.llm.factory import get_provider
from app.core.llm.gemini import GeminiProvider
from app.core.quota import attach_usage_to_keys
from app.core.vra_service import build_gemini_key_candidates
from app.database import get_db
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
    # Include inactive keys too — the Settings UI shows them so the user can
    # see which keys were auto-deactivated (e.g. on API_KEY_INVALID) and
    # decide whether to reset or delete them.
    gemini_rows = list(
        db.execute(
            select(ApiKey).where(
                ApiKey.provider == "gemini",
            )
        )
        .scalars()
        .all()
    )
    keys = attach_usage_to_keys(db, gemini_rows, daily_limit=limit)
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
    return SettingsStateResponse(
        llm_provider=get_value(db, "llm_provider", "gemini"),
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
    candidates = build_gemini_key_candidates(db)
    if not candidates:
        raise HTTPException(status_code=400, detail="No Gemini API keys available to test.")
    model = get_value(db, "llm_model", "gemini-2.0-flash")
    temperature = float(get_value(db, "llm_temperature", "0.2"))
    max_out = int(get_value(db, "llm_max_output_tokens", "16384"))
    _row, secret, label = candidates[0]
    prov = get_provider(
        "gemini",
        api_key=secret,
        model=model,
        temperature=temperature,
        max_output_tokens=max_out,
    )
    detail: str | None = None
    try:
        if isinstance(prov, GeminiProvider):
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


@router.delete("/api/settings/keys/{key_id}")
def api_settings_delete_key(key_id: int, db: Session = Depends(get_db)) -> dict:
    """Hard-delete a stored Gemini key."""
    row = db.get(ApiKey, key_id)
    if row is None or row.provider != "gemini":
        raise HTTPException(status_code=404, detail=f"Unknown key id {key_id}")
    label = row.label
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": label}


@router.post("/api/settings/keys/reset")
def api_settings_reset_keys(db: Session = Depends(get_db)) -> dict:
    """Reactivate every Gemini key whose ``is_active`` flag is False.

    Pair to the auto-deactivation that fires on persistent API_KEY_INVALID
    (see ``app.core.vra_service._deactivate_bad_key``). After the user has
    investigated / fixed the underlying issue (rotated quota, swapped a
    bad key), this endpoint flips the flag back on so the rotation can
    use the keys again.
    """
    rows = list(
        db.execute(
            select(ApiKey).where(
                ApiKey.provider == "gemini",
                ApiKey.is_active.is_(False),
            )
        )
        .scalars()
        .all()
    )
    for r in rows:
        r.is_active = True
        db.add(r)
    db.commit()
    return {"ok": True, "reactivated": [r.label for r in rows], "count": len(rows)}
