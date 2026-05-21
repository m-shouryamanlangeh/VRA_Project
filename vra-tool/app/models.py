"""SQLAlchemy ORM models: application settings, API keys, audit log, quota."""

from __future__ import annotations

import datetime as dt
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Setting(Base):
    """
    Key-value application settings (e.g. default LLM provider, model name).

    The stakeholder spec refers to this concept as *Settings*; the class is
    named ``Setting`` to avoid clashing with ``pydantic_settings.BaseSettings``.
    """

    __tablename__ = "settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=False),
        default=dt.datetime.utcnow,
        onupdate=dt.datetime.utcnow,
        nullable=False,
    )


class ApiKey(Base):
    """Encrypted LLM API keys with provider, label, and usage metadata."""

    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=False),
        default=dt.datetime.utcnow,
        nullable=False,
    )
    last_used_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    daily_usage: Mapped[list[KeyDailyUsage]] = relationship(
        "KeyDailyUsage",
        back_populates="api_key",
        cascade="all, delete-orphan",
    )


class KeyDailyUsage(Base):
    """Request counts per API key per calendar day (quota tracking)."""

    __tablename__ = "key_daily_usage"
    __table_args__ = (UniqueConstraint("api_key_id", "usage_date", name="uq_key_daily_usage"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    api_key_id: Mapped[int] = mapped_column(Integer, ForeignKey("api_keys.id", ondelete="CASCADE"), nullable=False)
    usage_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    api_key: Mapped[ApiKey] = relationship("ApiKey", back_populates="daily_usage")


class AuditLog(Base):
    """Log of VRA generation requests and outcomes."""

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=False),
        default=dt.datetime.utcnow,
        nullable=False,
        index=True,
    )
    vendor_name: Mapped[str] = mapped_column(String(512), nullable=False)
    gst: Mapped[str] = mapped_column(String(32), nullable=False)
    org_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_type: Mapped[str] = mapped_column(String(32), nullable=False, default="SINGLE")
    provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    key_label_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pdf_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    user: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
