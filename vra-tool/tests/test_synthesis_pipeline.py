"""Hybrid assembly tests (no live Gemini by default)."""

from __future__ import annotations

import pytest

from app.core.collectors.orchestrator import EvidencePack
from app.core.hybrid_report import build_vra_report
from app.schemas import SynthesisResult


def test_build_vra_report_merges_sections() -> None:
    ev = EvidencePack(
        vendor={"name": "ACME", "gst": "27ADKFS8129B1ZY", "org_type": "Partnership"},
        gst_data={"legal_name": "ACME LLP", "gst_status": "Active"},
        mca_data=None,
        news_headlines=[{"title": "ACME investigation", "link": "https://news.example.com/1"}],
        news_meta={"entity_google_search_hyperlink": "https://www.google.com/search?q=acme"},
        collector_status={"gst": "ok"},
        collector_errors={},
    )
    syn = SynthesisResult(
        executive_summary={"text": "Summary."},
        top_findings=["f1", "f2", "f3"],
        top_positives=["p1"],
        risk_rating="MEDIUM",
        recommendation="CONDITIONAL",
        news_severity=[{"title": "ACME investigation", "severity": "HIGH"}],
    )
    report = build_vra_report(ev, syn, date_str="2026-01-01")
    assert report.recommendation == "CONDITIONAL"
    assert report.company_profile
    assert report.adverse_media


def _airtel_false_positive_evidence() -> EvidencePack:
    """Web/news evidence that previously produced a bogus HIGH/REJECT for AIRTEL."""
    return EvidencePack(
        vendor={"name": "AIRTEL", "gst": "", "org_type": "Unknown"},
        gst_data=None,
        mca_data=None,
        news_headlines=[
            {"title": "Bharti Airtel under scanner for money laundering - The Economic Times",
             "link": "https://economictimes.indiatimes.com/x"},
            {"title": "How to Block Scam Calls on Mobile: Secure Your Phone - Airtel",
             "link": "https://www.airtel.in/blog/scam-calls"},
        ],
        web_search_results={
            # Vendor's own explainer blog — keyword-scored HIGH, drove the veto.
            "defaults": [{
                "title": "Who is a Wilful Defaulter as per RBI? - Airtel",
                "snippet": "A wilful defaulter as per RBI is someone who deliberately misuses funds.",
                "url": "https://www.airtel.in/blog/wilful-defaulter",
            }],
            "litigations": [
                # Generic portal description — never names the vendor.
                {"title": "NCLT and NCLAT Case Status on eCourtsIndia: Search Every ...",
                 "snippet": "Search NCLT and NCLAT case status across all 15 insolvency benches.",
                 "url": "https://ecourtsindia.com/nclt"},
                # Generic NCLT order — never names the vendor.
                {"title": "IN THE NATIONAL COMPANY LAW TRIBUNAL MUMBAI",
                 "snippet": "The Corporate Debtor committed default. Petition under Section 9 "
                            "Insolvency and Bankruptcy Code.",
                 "url": "https://ibbi.gov.in/x"},
                # Scraped IndianKanoon search-index page (nav chrome).
                {"title": "[Page content] https://indiankanoon.org/search/?formInput=AIRTEL",
                 "snippet": "AIRTEL Skip to main content Indian Kanoon - Search engine for Indian Law",
                 "url": "https://indiankanoon.org/search/?formInput=AIRTEL"},
            ],
        },
        news_meta={"entity_google_search_hyperlink": "https://www.google.com/search?q=AIRTEL"},
        collector_status={},
        collector_errors={},
    )


def test_web_findings_drop_false_positives_no_veto() -> None:
    """Explainer / off-vendor / chrome web snippets must NOT score a dimension.

    Without the gates, an Airtel-published wilful-default explainer scored
    Defaults=HIGH(100) and auto-vetoed the whole report to REJECT.
    """
    ev = _airtel_false_positive_evidence()
    syn = SynthesisResult(
        executive_summary={"summary": "Airtel assessment."},
        top_findings=["Bharti Airtel under scanner for money laundering"],
        top_positives=[],
        risk_rating="HIGH",
        recommendation="REJECT",
        news_severity=[],
    )
    report = build_vra_report(ev, syn, date_str="2026-06-10")

    # Defaults / litigations collapse to the INFO placeholder (every snippet filtered).
    assert all(f.severity == "INFO" for f in report.defaults)
    assert all(f.severity == "INFO" for f in report.litigations)
    # No HIGH finding anywhere in the scored sections → no veto-class score.
    assert all(f.severity != "HIGH" for f in report.defaults + report.litigations)
    # The vendor-published "How to Block Scam Calls - Airtel" item is not adverse media.
    summaries = " ".join(a.summary for a in report.adverse_media).lower()
    assert "block scam calls" not in summaries
    # The genuine money-laundering report is still surfaced.
    assert "money laundering" in summaries


def test_verified_gstin_high_web_finding_still_vetoes() -> None:
    """The no-GSTIN HIGH→MEDIUM cap must NOT neuter a verified-identity veto."""
    from app.core.report_normalization import _ensure_calibrated_rubric

    ev = EvidencePack(
        vendor={"name": "ACME CORP", "gst": "27ADKFS8129B1ZY", "org_type": "Private"},
        gst_data={"legal_name": "ACME CORP", "gst_status": "Active"},
        mca_data=None,
        news_headlines=[],
        web_search_results={
            "defaults": [{
                "title": "ACME CORP declared wilful defaulter by consortium of banks",
                "snippet": "Lenders classified ACME CORP as a wilful defaulter over unpaid dues.",
                "url": "https://www.watchoutinvestors.com/x",
            }],
        },
        news_meta={},
        collector_status={},
        collector_errors={},
    )
    syn = SynthesisResult(
        executive_summary={"summary": "ACME assessment."},
        top_findings=[],
        top_positives=[],
        risk_rating="HIGH",
        recommendation="REJECT",
        news_severity=[],
    )
    report = build_vra_report(ev, syn, date_str="2026-06-10")
    # With a verified GSTIN, the genuine wilful-default finding stays HIGH.
    assert any(f.severity == "HIGH" for f in report.defaults)
    data = report.model_dump()
    _ensure_calibrated_rubric(data)
    assert data["executive_summary"]["veto_triggered"] is True
    assert data["recommendation"] == "REJECT"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_hybrid_gemini_smoke_if_env() -> None:
    """Optional: set GEMINI_API_KEY + USE_HYBRID_MODE in environment for a live call."""
    import os

    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    pytest.skip("Live hybrid Gemini test not enabled in CI")
