"""LLM-free pipeline tests (USE_LLM=false architecture).

These verify that a full, calibrated VRAReport can be produced from collector
evidence ALONE — no language model — using:

    deterministic_synthesis()  →  build_vra_report()  →  _ensure_calibrated_rubric()
                                                        →  populate_narrative()

The narrative is templated from the calibrated ``why_rating`` block, so every
sentence is traceable to a number or a finding.
"""

from __future__ import annotations

from app.core.collectors.orchestrator import EvidencePack
from app.core.hybrid_report import build_vra_report, deterministic_synthesis
from app.core.narrative import populate_narrative
from app.core.report_normalization import _ensure_calibrated_rubric

_VALID_GST = "27ADKFS8129B1ZY"


def _evidence(*, name, gst="", org_type="Private Limited", gst_data=None,
              news=None, web=None) -> EvidencePack:
    return EvidencePack(
        vendor={"name": name, "gst": gst, "org_type": org_type},
        gst_data=gst_data,
        mca_data=None,
        news_headlines=list(news or []),
        web_search_results=dict(web or {}),
    )


def _run_no_llm(evidence: EvidencePack) -> dict:
    """Reproduce the USE_LLM=false report assembly (minus DB/PDF I/O)."""
    report = build_vra_report(evidence, deterministic_synthesis(), date_str="2026-06-11")
    data = report.model_dump()
    _ensure_calibrated_rubric(data)
    populate_narrative(data)
    return data


def test_deterministic_synthesis_is_neutral_and_llm_free() -> None:
    s = deterministic_synthesis()
    assert s.executive_summary == {}
    assert s.top_findings == [] and s.top_positives == []
    assert s.news_severity == []
    assert s.risk_rating == "LOW"


def test_clean_vendor_no_llm_is_low_and_approves() -> None:
    ev = _evidence(
        name="Steadfast Logistics Private Limited",
        gst=_VALID_GST,
        gst_data={"legal_name": "Steadfast Logistics Private Limited", "gst_status": "Active"},
    )
    data = _run_no_llm(ev)
    es = data["executive_summary"]

    assert es["risk_rating"] == "LOW"
    assert es["recommendation_tier"] in {"APPROVE", "APPROVE_WITH_MONITORING"}
    # Narrative is fully templated, present, and self-identifies as LLM-free.
    assert es["summary"]
    assert "deterministic rules engine" in es["summary"].lower()
    assert "Steadfast Logistics" in es["summary"]
    assert isinstance(es["key_risk_drivers"], list) and es["key_risk_drivers"]
    assert isinstance(es["key_mitigants"], list) and es["key_mitigants"]


def test_severe_verified_finding_no_llm_drives_high() -> None:
    ev = _evidence(
        name="Scamcorp Limited",
        gst=_VALID_GST,
        news=[{"title": "Scamcorp Limited: SFIO chargesheet, promoters convicted in multi-crore bank fraud",
               "link": "https://sfio.nic.in/order/x"}],
    )
    data = _run_no_llm(ev)
    es = data["executive_summary"]

    assert es["risk_rating"] == "HIGH"
    assert es["recommendation_tier"] in {"REJECT", "ENHANCED_DUE_DILIGENCE"}
    # The prose reflects the calibrated drivers (no invented content).
    assert es["summary"]
    assert es["why_rating"]["top_negative_factors"]


def test_narrative_is_deterministic_across_runs() -> None:
    ev = _evidence(
        name="NimbleAI Technologies Private Limited",
        gst=_VALID_GST,
        news=[{"title": "Startup in a minor service dispute with a vendor (pending)",
               "link": "https://www.business-standard.com/x"}],
    )
    out = {_run_no_llm(ev)["executive_summary"]["summary"] for _ in range(3)}
    assert len(out) == 1, f"narrative should be deterministic, got {len(out)} variants"


def test_name_only_vendor_has_no_phantom_adverse_driver() -> None:
    """A clean name-only vendor must NOT invent an adverse-media risk driver.

    The 'no adverse media retained' placeholder used to be severity LOW (→ 25),
    which became the entity's only 'top risk driver'. It must score 0 now.
    """
    ev = _evidence(name="Greenfield Trading", org_type="Unknown")
    data = _run_no_llm(ev)
    es = data["executive_summary"]

    assert es["dimension_scores"]["adverse_media"] == 0
    drivers = " ".join(es["key_risk_drivers"]).lower()
    assert "adverse media" not in drivers


def test_collector_placeholders_are_not_verified_facts() -> None:
    """Empty-collector placeholders must read NOT_ASSESSED, never VERIFIED FACT."""
    ev = _evidence(name="Greenfield Trading", org_type="Unknown")
    data = _run_no_llm(ev)

    for section in ("company_profile", "management", "financial_soundness",
                    "borrowings", "funds_raised", "mca_filings", "statutory_compliance"):
        for f in data.get(section) or []:
            assert f.get("fact_type") == "NOT_ASSESSED", (
                f"{section}: placeholder wrongly tagged {f.get('fact_type')} — {f.get('point')[:60]}"
            )

    # And nothing fabricated should be claimed as a Tier-1 verified fact in the
    # confidence drivers for a vendor with zero real evidence.
    why = data["executive_summary"]["why_rating"]
    assert "1 verified fact(s) on official record" not in " ".join(why["confidence_drivers"])


def test_populate_narrative_preserves_existing_summary_unless_forced() -> None:
    data = {
        "vendor": {"name": "Acme Ltd", "org_type": "Private Limited"},
        "executive_summary": {
            "risk_rating": "LOW",
            "summary": "Pre-existing LLM narrative.",
            "why_rating": {"rating": "LOW", "top_negative_factors": [], "top_positive_factors": []},
        },
    }
    populate_narrative(data)
    assert data["executive_summary"]["summary"] == "Pre-existing LLM narrative."

    populate_narrative(data, force=True)
    assert data["executive_summary"]["summary"] != "Pre-existing LLM narrative."
    assert "deterministic rules engine" in data["executive_summary"]["summary"].lower()
