"""Validator helpers and layout sanity checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BASE_DIR
from app.core.prompts import format_adverse_media_prompt
from app.core.validator import is_plausible_url
from app.schemas import VendorGenerateRequest


def test_base_dir_exists() -> None:
    assert (BASE_DIR / "app").is_dir()
    assert (BASE_DIR / "data").is_dir()


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://example.com/path", True),
        ("http://paytm.com", True),
        ("not-a-url", False),
        ("", False),
        ("ftp://x.com", False),
    ],
)
def test_url_plausibility(url: str, ok: bool) -> None:
    assert is_plausible_url(url) is ok


def test_gst_validation_accepts_sample() -> None:
    body = VendorGenerateRequest(
        vendor_name="SHARP PENCIL PRODUCTIONS",
        gst="27ADKFS8129B1ZY",
        org_type="Partnership",
    )
    assert body.gst == "27ADKFS8129B1ZY"


def test_org_type_defaults_to_unknown_when_blank() -> None:
    body = VendorGenerateRequest(
        vendor_name="ACME",
        gst="27ADKFS8129B1ZY",
        org_type="",
    )
    assert body.org_type == "Unknown"
    body2 = VendorGenerateRequest.model_validate(
        {"vendor_name": "ACME", "gst": "27ADKFS8129B1ZY", "org_type": "   "}
    )
    assert body2.org_type == "Unknown"


def test_adverse_prompt_keeps_literal_name_placeholder() -> None:
    """Stakeholder URL pattern uses ``{name}`` for the LLM — not Python format."""
    out = format_adverse_media_prompt("Co", "27AAAAA0000A1Z5", "LLP", "2026-01-01")
    assert "{name}" in out
    assert "Co" in out


def test_gst_validation_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        VendorGenerateRequest(
            vendor_name="X",
            gst="INVALIDGSTNUMBER",
            org_type="LLP",
        )


def test_gst_optional_empty_omitted() -> None:
    body = VendorGenerateRequest(vendor_name="ACME Corp", org_type="LLP")
    assert body.gst == ""
    body2 = VendorGenerateRequest.model_validate({"vendor_name": "ACME Corp"})
    assert body2.gst == ""
    assert body2.org_type == "Unknown"


def test_gst_for_prompt_shows_hint_when_blank() -> None:
    from app.core.prompts import gst_for_prompt

    assert gst_for_prompt("") == gst_for_prompt("   ")
    assert "not provided" in gst_for_prompt("").lower()
    assert gst_for_prompt("27ADKFS8129B1ZY") == "27ADKFS8129B1ZY"


# ── Shared-path quality filter (covers legacy LLM-grounded reports) ───────────

def _airtel_legacy_report():
    """A VRAReport shaped like the legacy/grounded-LLM path output for AIRTEL,
    carrying the exact false positives that wrongly produced HIGH/REJECT."""
    from app.schemas import VRAReport

    def F(point, sev, src="https://www.google.com/search?q=x"):
        return {"point": point, "source": src, "severity": sev}

    def A(summary, sev):
        return {"entity": "AIRTEL",
                "search_hyperlink": "https://www.google.com/search?q=AIRTEL",
                "summary": summary, "severity": sev,
                "source": "https://news.google.com/x"}

    return VRAReport.model_validate({
        "vendor": {"name": "AIRTEL", "gst": "", "org_type": "Unknown"},
        "date_of_search": "2026-06-10",
        "executive_summary": {"summary": "Airtel.", "risk_rating": "HIGH", "confidence": "MEDIUM"},
        "company_profile": [F("No GSTIN provided — name/RSS/OSINT only.", "INFO", "https://www.mca.gov.in/")],
        "management": [F("MCA director scrape unavailable (CAPTCHA).", "INFO", "https://www.mca.gov.in/")],
        "credit_ratings": [F("No credit downgrade or wilful-defaulter records found.", "INFO", "https://www.mca.gov.in/")],
        "financial_soundness": [F("Going concern basis - Airtel Africa plc accounting policies.", "MEDIUM")],
        "borrowings": [F("No SARFAESI / DRT / NPA signals found.", "INFO", "https://www.rbi.org.in/")],
        "funds_raised": [F("No SEBI observations found.", "INFO", "https://www.mca.gov.in/")],
        "mca_filings": [F("No MCA master data retrieved.", "INFO", "https://www.mca.gov.in/")],
        "defaults": [
            F("Who is a Wilful Defaulter as per RBI? - Airtel — a wilful defaulter "
              "deliberately misuses funds.", "HIGH", "https://www.airtel.in/blog/x"),
        ],
        "litigations": [
            F("National Company Law Tribunal - Wikipedia — appeals go to NCLAT.", "HIGH",
              "https://en.wikipedia.org/x"),
            F("IN THE NATIONAL COMPANY LAW TRIBUNAL, MUMBAI — Petition under Section 7 IBC.",
              "HIGH", "https://nclt.gov.in/x"),
            F("Bombay HC quashed spectrum charge on Bharti Airtel and Vodafone Idea.", "LOW",
              "https://indianexpress.com/x"),
        ],
        "statutory_compliance": [F("No GST cancellation found.", "INFO", "https://www.gst.gov.in/")],
        "adverse_media": [
            A("Airtel embeds AI at network layer to curb OTP-led banking fraud", "MEDIUM"),
            A("Bharti Airtel Q2 Results: Revenue Rises 25%", "LOW"),
            A("Bharti Airtel under scanner for money laundering - ET", "MEDIUM"),
            A("RBI Imposes Penalty On Airtel Payments Bank - NDTV Profit", "MEDIUM"),
        ],
        "fraud_aml": [],
        "connected_entities": [],
        "recommendation": "REJECT",
    })


def test_legacy_path_airtel_no_false_reject() -> None:
    """The shared validator+rubric must scrub the legacy-path false positives so
    AIRTEL (name only) does not auto-REJECT — the core regression."""
    from app.core.report_normalization import _ensure_calibrated_rubric
    from app.core.validator import validate_report_sync

    report = validate_report_sync(_airtel_legacy_report(), verify_urls=False)
    data = report.model_dump()
    _ensure_calibrated_rubric(data)
    es = data["executive_summary"]

    # The wilful-default explainer is gone → defaults no longer veto-class.
    assert all("wilful defaulter as per rbi" not in (f["point"] or "").lower() for f in data["defaults"])
    # Off-vendor NCLT/Wikipedia litigation dropped.
    joined_lit = " ".join(f["point"].lower() for f in data["litigations"])
    assert "wikipedia" not in joined_lit
    assert "section 7 ibc" not in joined_lit
    # AI-fraud-prevention and routine results dropped from adverse media.
    adverse = " ".join(a["summary"].lower() for a in data["adverse_media"])
    assert "curb otp-led banking fraud" not in adverse
    assert "revenue rises" not in adverse
    # Genuine adverse media survives.
    assert "money laundering" in adverse
    # Outcome: no veto, not REJECT, and an auditable rationale is present.
    assert es["veto_triggered"] is False
    assert data["recommendation"] != "REJECT"
    assert es["risk_rating"] in ("LOW", "MEDIUM")
    assert "Risk score" in es.get("risk_score_rationale", "")
