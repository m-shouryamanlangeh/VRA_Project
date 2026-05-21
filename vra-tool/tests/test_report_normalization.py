"""Legacy LLM payload normalization for ``VRAReport``."""

from __future__ import annotations

from app.core.report_normalization import normalize_legacy_vra_payload
from app.schemas import BlacklistCheck, VRAReport


def test_normalize_vendor_assessment_and_blacklist_dict() -> None:
    raw = {
        "vendor_assessment": {
            "executive_summary": {"risk_level": "HIGH"},
            "recommendation": "REJECT",
        },
        "blacklist_checks": {
            "cbi_wanted_interpol_red_notice": "NO",
            "mca_struck_off": {"status": "UNVERIFIED", "source": "https://www.mca.gov.in/"},
        },
    }
    out = normalize_legacy_vra_payload(
        raw,
        date_str="2026-04-30",
        vendor_name="ACME",
        gst="27ADKFS8129B1ZY",
        org_type="Partnership",
    )
    report = VRAReport.model_validate(out)
    assert report.recommendation == "REJECT"
    assert report.date_of_search == "2026-04-30"
    assert isinstance(report.blacklist_checks, list)
    assert len(report.blacklist_checks) == 2


def test_blacklist_status_na_coerced() -> None:
    b = BlacklistCheck(list_name="Test", status="N/A", source="https://www.mca.gov.in/")
    assert b.status == "UNVERIFIED"


def test_normalize_passes_through_valid_shape() -> None:
    raw = {
        "vendor": {"name": "X", "gst": "27ADKFS8129B1ZY", "org_type": "Pvt Ltd"},
        "date_of_search": "2026-01-01",
        "executive_summary": {},
        "recommendation": "PROCEED",
        "blacklist_checks": [],
    }
    for k in (
        "company_profile",
        "management",
        "credit_ratings",
        "financial_soundness",
        "borrowings",
        "funds_raised",
        "mca_filings",
        "defaults",
        "litigations",
        "statutory_compliance",
        "adverse_media",
        "fraud_aml",
        "connected_entities",
    ):
        raw[k] = []
    out = normalize_legacy_vra_payload(
        raw,
        date_str="2026-04-30",
        vendor_name="X",
        gst="27ADKFS8129B1ZY",
        org_type="Pvt Ltd",
    )
    VRAReport.model_validate(out)
