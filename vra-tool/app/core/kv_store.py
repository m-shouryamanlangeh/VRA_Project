"""SQLite key-value settings (``Setting`` ORM rows)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Setting


def get_value(db: Session, key: str, default: str = "") -> str:
    """Return stored value for ``key`` or ``default``."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    return row.value if row else default


def set_value(db: Session, key: str, value: str) -> None:
    """Insert or update a setting value."""
    row = db.execute(select(Setting).where(Setting.key == key)).scalar_one_or_none()
    if row is None:
        db.add(Setting(key=key, value=value))
    else:
        row.value = value
    db.flush()


def next_pdf_sequence(db: Session) -> int:
    """Increment and return the next PDF sequence number (caller commits)."""
    key = "pdf_seq"
    raw = get_value(db, key, "0")
    try:
        n = int(raw) + 1
    except ValueError:
        n = 1
    set_value(db, key, str(n))
    db.flush()
    return n
