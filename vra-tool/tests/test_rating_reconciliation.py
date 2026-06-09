"""Regression tests for the LLM-rating floor in ``_ensure_calibrated_rubric``.

Guards against the silent HIGH→LOW/PROCEED downgrade: a vendor the LLM rated
risky must never be reported as low risk just because the per-section dimension
scores (sparse web evidence) didn't independently reach that band. The flip
side is equally important — the LLM's rating must not be trusted to *lower* a
structurally-supported HIGH, nor to auto-REJECT a clean vendor.
"""

from __future__ import annotations

from app.core.report_normalization import _ensure_calibrated_rubric

_LIST_SECTIONS = (
    "company_profile", "management", "credit_ratings", "financial_soundness",
    "borrowings", "funds_raised", "mca_filings", "defaults", "litigations",
    "statutory_compliance", "adverse_media", "fraud_aml",
)


def _build(es: dict, sections: dict | None = None) -> dict:
    data: dict = {"executive_summary": dict(es)}
    for k in _LIST_SECTIONS:
        data[k] = []
    if sections:
        data.update(sections)
    _ensure_calibrated_rubric(data)
    return data


def test_llm_high_with_sparse_evidence_is_not_downgraded_to_proceed() -> None:
    """The reported bug: LLM=HIGH, one narrative finding routed at MEDIUM whose
    wording matches no veto phrase / _EVENT_FLOOR. Must not read LOW/PROCEED."""
    data = _build(
        {"risk_rating": "HIGH", "confidence": "MEDIUM",
         "top_findings": ["defaults: severe undisclosed promoter related-party leakage"]},
        {"defaults": [{"point": "severe undisclosed promoter related-party leakage",
                       "severity": "MEDIUM"}]},
    )
    es = data["executive_summary"]
    assert es["risk_rating"] in ("MEDIUM", "HIGH")
    assert data["recommendation"] != "PROCEED"
    assert es["confidence"] == "LOW"


def test_llm_high_with_no_evidence_floors_to_conditional() -> None:
    data = _build({"risk_rating": "HIGH", "confidence": "MEDIUM", "top_findings": []})
    assert data["executive_summary"]["risk_rating"] == "MEDIUM"
    assert data["recommendation"] == "CONDITIONAL"


def test_llm_medium_floors_to_conditional() -> None:
    data = _build({"risk_rating": "MEDIUM", "confidence": "MEDIUM", "top_findings": []})
    assert data["executive_summary"]["risk_rating"] == "MEDIUM"
    assert data["recommendation"] == "CONDITIONAL"


def test_clean_vendor_is_not_spuriously_promoted() -> None:
    """LLM itself said LOW and nothing was found — rating must stay LOW, never
    auto-escalated. (Recommendation is CONDITIONAL only via the empty-signal
    guard, which is the intended belt-and-suspenders behaviour.)"""
    data = _build({"risk_rating": "LOW", "confidence": "HIGH"})
    assert data["executive_summary"]["risk_rating"] == "LOW"


def test_structurally_supported_high_stays_reject() -> None:
    data = _build(
        {"risk_rating": "HIGH", "confidence": "HIGH",
         "top_findings": ["defaults: borrower declared a wilful defaulter by the bank"]},
        {"defaults": [{"point": "borrower declared a wilful defaulter by the bank",
                       "severity": "HIGH"}]},
    )
    assert data["executive_summary"]["risk_rating"] == "HIGH"
    assert data["recommendation"] == "REJECT"


def test_llm_rating_never_lowers_a_computed_high() -> None:
    """A veto finding present, but the LLM's structured field says LOW. The LLM
    must not be allowed to pull a genuine HIGH down."""
    data = _build(
        {"risk_rating": "LOW", "confidence": "HIGH",
         "top_findings": ["defaults: borrower declared a wilful defaulter"]},
        {"defaults": [{"point": "borrower declared a wilful defaulter",
                       "severity": "HIGH"}]},
    )
    assert data["executive_summary"]["risk_rating"] == "HIGH"
