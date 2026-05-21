"""Per-key daily quota tracking (e.g. Gemini free tier 1500/day)."""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ApiKey, KeyDailyUsage

logger = logging.getLogger(__name__)

# Stakeholder default for Gemini free tier display / warning threshold
DEFAULT_DAILY_LIMIT = 1500
WARN_FRACTION = 0.80


def today_utc_date() -> dt.date:
    """Calendar date in UTC for quota bucketing."""
    return dt.datetime.utcnow().date()


def get_today_usage(db: Session, api_key_id: int) -> int:
    """Return request count for ``api_key_id`` for today (UTC)."""
    d = today_utc_date()
    row = db.execute(
        select(KeyDailyUsage).where(
            KeyDailyUsage.api_key_id == api_key_id,
            KeyDailyUsage.usage_date == d,
        )
    ).scalar_one_or_none()
    return int(row.request_count) if row else 0


def increment_usage(db: Session, api_key_id: int, delta: int = 1) -> int:
    """
    Increment today's usage for ``api_key_id`` and return the new total.

    Commits are left to the caller.
    """
    d = today_utc_date()
    row = db.execute(
        select(KeyDailyUsage).where(
            KeyDailyUsage.api_key_id == api_key_id,
            KeyDailyUsage.usage_date == d,
        )
    ).scalar_one_or_none()
    if row is None:
        row = KeyDailyUsage(api_key_id=api_key_id, usage_date=d, request_count=0)
        db.add(row)
    row.request_count = int(row.request_count) + delta
    db.flush()
    logger.debug("Quota key_id=%s date=%s count=%s", api_key_id, d, row.request_count)
    return int(row.request_count)


def quota_warning(used: int, limit: int = DEFAULT_DAILY_LIMIT) -> bool:
    """True if usage is at or above 80% of ``limit``."""
    if limit <= 0:
        return False
    return used >= int(limit * WARN_FRACTION)


def attach_usage_to_keys(
    db: Session,
    keys: list[ApiKey],
    daily_limit: int = DEFAULT_DAILY_LIMIT,
) -> list[dict]:
    """
    Build JSON-serializable rows for the settings UI: id, label, masked key,
    usage today, limit, warn flag.
    """
    from app.core.crypto import decrypt_secret, mask_secret

    out: list[dict] = []
    for k in keys:
        try:
            plain = decrypt_secret(k.encrypted_key)
            masked = mask_secret(plain)
        except Exception:
            masked = "(decrypt error)"
        used = get_today_usage(db, k.id)
        out.append(
            {
                "id": k.id,
                "label": k.label,
                "provider": k.provider,
                "masked_key": masked,
                "is_active": k.is_active,
                "usage_today": used,
                "daily_limit": daily_limit,
                "quota_warning": quota_warning(used, daily_limit),
            }
        )
    return out
