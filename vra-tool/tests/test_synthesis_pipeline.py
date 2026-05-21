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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_hybrid_gemini_smoke_if_env() -> None:
    """Optional: set GEMINI_API_KEY + USE_HYBRID_MODE in environment for a live call."""
    import os

    if not os.getenv("GEMINI_API_KEY"):
        pytest.skip("GEMINI_API_KEY not set")
    pytest.skip("Live hybrid Gemini test not enabled in CI")
