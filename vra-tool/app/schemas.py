"""Pydantic request/response schemas for VRA and API payloads."""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

GST_RE = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}[Z]{1}[0-9A-Z]{1}$")


class Finding(BaseModel):
    """Single OSINT finding with mandatory source URL."""

    point: str
    source: str
    severity: Literal["HIGH", "MEDIUM", "LOW", "INFO"] = "INFO"


class AdverseFinding(BaseModel):
    """Adverse-media style finding."""

    entity: str
    search_hyperlink: str
    summary: str
    severity: Literal["HIGH", "MEDIUM", "LOW", "INFO"] = "INFO"
    source: Optional[str] = None

    @field_validator("severity", mode="before")
    @classmethod
    def coerce_severity(cls, v: Any) -> str:
        """Coerce non-standard values → INFO (default for clean no-signal rows)."""
        if v is None:
            return "INFO"
        s = str(v).strip().upper()
        if s in ("HIGH", "MEDIUM", "LOW", "INFO"):
            return s
        if s in ("NONE", "N/A", "NA", "INFORMATIONAL", ""):
            return "INFO"
        return "INFO"


class VRAReport(BaseModel):
    """Full structured VRA output expected from the primary LLM call."""

    vendor: dict[str, Any]
    date_of_search: str
    executive_summary: dict[str, Any]
    company_profile: list[Finding] = Field(default_factory=list)
    management: list[Finding] = Field(default_factory=list)
    credit_ratings: list[Finding] = Field(default_factory=list)
    financial_soundness: list[Finding] = Field(default_factory=list)
    borrowings: list[Finding] = Field(default_factory=list)
    funds_raised: list[Finding] = Field(default_factory=list)
    mca_filings: list[Finding] = Field(default_factory=list)
    defaults: list[Finding] = Field(default_factory=list)
    litigations: list[Finding] = Field(default_factory=list)
    statutory_compliance: list[Finding] = Field(default_factory=list)
    adverse_media: list[AdverseFinding] = Field(default_factory=list)
    fraud_aml: list[AdverseFinding] = Field(default_factory=list)
    connected_entities: list[dict[str, Any]] = Field(default_factory=list)
    recommendation: Literal["PROCEED", "CONDITIONAL", "REJECT"]


class AdversePassResult(BaseModel):
    """Structured output from the dedicated adverse-media prompt pass."""

    executive_summary: dict[str, Any] = Field(default_factory=dict)
    findings: list[AdverseFinding] = Field(default_factory=list)


class SynthesisResult(BaseModel):
    """Hybrid-mode LLM output: reasoning only; facts come from collectors."""

    executive_summary: dict[str, Any] = Field(default_factory=dict)
    top_findings: list[str] = Field(default_factory=list)
    top_positives: list[str] = Field(default_factory=list)
    risk_rating: Literal["LOW", "MEDIUM", "HIGH"]
    recommendation: Literal["PROCEED", "CONDITIONAL", "REJECT"]
    news_severity: list[dict[str, Any]] = Field(default_factory=list)


class VendorGenerateRequest(BaseModel):
    """JSON body for ``POST /generate``."""

    vendor_name: str = Field(..., min_length=1, max_length=512)
    gst: str = Field(default="", max_length=15)
    org_type: str = Field(default="Unknown", max_length=64)

    @field_validator("org_type", mode="before")
    @classmethod
    def normalize_org_type(cls, value: Any) -> str:
        """Whitespace-only or explicit empty → ``Unknown`` (GST / OSINT still apply)."""
        if value is None:
            return "Unknown"
        s = str(value).strip()
        return s if s else "Unknown"

    @field_validator("gst", mode="before")
    @classmethod
    def normalize_gst_optional(cls, value: Any) -> str:
        if value is None:
            return ""
        return str(value).strip().upper()

    @field_validator("gst")
    @classmethod
    def validate_gst(cls, value: str) -> str:
        """Empty = unknown GSTIN (name-based OSINT only); if provided, must be valid GSTIN."""
        gst = (value or "").strip().upper()
        if not gst:
            return ""
        if not GST_RE.match(gst):
            raise ValueError(
                "GST must be empty or 15 characters matching Indian GSTIN format "
                "(e.g. 27AAAAA0000A1Z5)."
            )
        return gst


class VendorGenerateResponse(BaseModel):
    """Response after successful generation (includes report and PDF path)."""

    ok: bool = True
    report: VRAReport
    pdf_url: str
    audit_id: int


class ApiKeyPayload(BaseModel):
    """Create or replace an encrypted API key row."""

    id: Optional[int] = None
    label: str = Field(..., min_length=1, max_length=64)
    key: str = Field(..., min_length=8)


class SettingsSaveRequest(BaseModel):
    """Persist LLM preferences and optional key updates."""

    llm_provider: str = "gemini"
    llm_model: str = "gemini-2.0-flash"
    temperature: float = Field(0.2, ge=0.0, le=2.0)
    max_output_tokens: int = Field(16384, ge=256, le=65536)
    daily_quota_limit: int = Field(default=1500, ge=1)
    keys: list[ApiKeyPayload] = Field(default_factory=list)


class SettingsStateResponse(BaseModel):
    """JSON for the settings page (masked keys, quota)."""

    llm_provider: str
    llm_model: str
    temperature: float
    max_output_tokens: int
    daily_quota_limit: int
    keys: list[dict[str, Any]]
    last_test_at: Optional[str] = None
    last_test_ok: Optional[bool] = None
    last_test_message: Optional[str] = None
    last_generation_at: Optional[str] = None
    fernet_configured: bool


class AuditListResponse(BaseModel):
    """Paginated audit rows."""

    total: int
    page: int
    page_size: int
    items: list[dict[str, Any]]
