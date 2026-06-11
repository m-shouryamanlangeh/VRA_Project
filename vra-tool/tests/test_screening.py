"""Unit tests for the adverse-media screening view (app.core.screening)."""

from __future__ import annotations

from app.core.screening import extract_negative_findings, screening_summary
from app.schemas import AdverseFinding, Finding, VRAReport


def _report(**lists) -> VRAReport:
    base = dict(
        vendor={"name": "ACME LTD"},
        date_of_search="2026-06-11",
        executive_summary={},
        recommendation="CONDITIONAL",
    )
    base.update(lists)
    return VRAReport(**base)


def test_not_assessed_and_info_are_excluded():
    rpt = _report(
        # "nothing retrieved" placeholders — must never appear as findings
        company_profile=[Finding(point="No GSTIN provided", source="", severity="INFO", fact_type="NOT_ASSESSED")],
        defaults=[Finding(point="No wilful-defaulter listing found", source="x", severity="INFO", fact_type="NOT_ASSESSED")],
        # a genuinely informational (clean) row, not adverse
        statutory_compliance=[Finding(point="GST active", source="https://gst.gov.in", severity="INFO", fact_type="VERIFIED_FACT")],
    )
    assert extract_negative_findings(rpt) == []
    sc = screening_summary(rpt)
    assert sc["overall"] == "NONE"
    assert sc["total"] == 0
    assert sc["counts"] == {"HIGH": 0, "MEDIUM": 0, "LOW": 0}


def test_real_findings_kept_and_sorted_by_severity():
    rpt = _report(
        litigations=[Finding(point="Fraud prosecution pending", source="https://sci.gov.in/x", severity="HIGH", fact_type="MEDIA_REFERENCE")],
        adverse_media=[
            AdverseFinding(entity="ACME", search_hyperlink="https://news.google.com/a", summary="Minor labour dispute", severity="LOW", fact_type="INFERENCE"),
            AdverseFinding(entity="ACME", search_hyperlink="https://news.google.com/b", summary="Regulator imposes penalty", severity="MEDIUM", fact_type="MEDIA_REFERENCE"),
            # INFO adverse row = "nothing material" → excluded
            AdverseFinding(entity="ACME", search_hyperlink="https://news.google.com/c", summary="no adverse media retained", severity="INFO", fact_type="NOT_ASSESSED"),
        ],
    )
    findings = extract_negative_findings(rpt)
    assert [f["severity"] for f in findings] == ["HIGH", "MEDIUM", "LOW"]  # sorted desc
    assert findings[0]["category"] == "Litigations"
    assert findings[1]["category"] == "Adverse Media"

    sc = screening_summary(rpt)
    assert sc["overall"] == "HIGH"
    assert sc["total"] == 3
    assert sc["counts"] == {"HIGH": 1, "MEDIUM": 1, "LOW": 1}


def test_source_label_falls_back_to_search_hyperlink_and_domain():
    rpt = _report(
        adverse_media=[AdverseFinding(entity="ACME", search_hyperlink="https://www.reuters.com/article/1", summary="probe", severity="LOW", fact_type="INFERENCE")],
    )
    f = extract_negative_findings(rpt)[0]
    assert f["source"] == "https://www.reuters.com/article/1"
    assert f["source_label"] == "reuters.com"  # www. stripped


def test_overall_is_highest_present_severity():
    rpt = _report(
        adverse_media=[
            AdverseFinding(entity="ACME", search_hyperlink="https://news.google.com/a", summary="x", severity="LOW", fact_type="INFERENCE"),
            AdverseFinding(entity="ACME", search_hyperlink="https://news.google.com/b", summary="y", severity="MEDIUM", fact_type="INFERENCE"),
        ],
    )
    assert screening_summary(rpt)["overall"] == "MEDIUM"
