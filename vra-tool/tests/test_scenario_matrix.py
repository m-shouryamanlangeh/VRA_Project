"""End-to-end scenario matrix across entity types (requirement #12).

Each scenario builds a representative report payload, runs the deterministic
calibration (the authoritative scoring layer), and asserts the expected risk
rating band, confidence band, and six-tier recommendation. The matrix is the
regression contract that the framework behaves CONSISTENTLY and generically
for listed companies, private companies, startups, LLPs, NGOs, defunct
companies, and entities with verified fraud / insolvency / sanctions exposure —
without any entity-specific logic.
"""

from __future__ import annotations

import copy

import pytest

from app.core.report_normalization import _ensure_calibrated_rubric

_LIST_SECTIONS = (
    "company_profile", "management", "credit_ratings", "financial_soundness",
    "borrowings", "funds_raised", "mca_filings", "defaults", "litigations",
    "statutory_compliance",
)
_VALID_GST = "27ADKFS8129B1ZY"


def _f(point, source, severity="INFO"):
    return {"point": point, "source": source, "severity": severity}


def _adv(summary, source, severity="LOW"):
    return {"entity": "X", "search_hyperlink": "https://www.google.com/search?q=x",
            "summary": summary, "severity": severity, "source": source}


def _build_report(*, name, gst="", org_type="Unknown", sections=None, adverse=None, fraud=None):
    data = {
        "vendor": {"name": name, "gst": gst, "org_type": org_type},
        "date_of_search": "2026-06-10",
        "executive_summary": {"risk_rating": "LOW", "summary": "Scenario assessment narrative."},
        "adverse_media": list(adverse or []),
        "fraud_aml": list(fraud or []),
        "connected_entities": [],
        "recommendation": "CONDITIONAL",
    }
    for s in _LIST_SECTIONS:
        data[s] = []
    for s, items in (sections or {}).items():
        data[s] = items
    return data


# ── Scenario definitions ─────────────────────────────────────────────────────
# (id, payload-kwargs, expected_rating_set, expected_confidence_set, expected_rec_set)
_SCENARIOS = [
    (
        "large_listed_clean",
        dict(
            name="Bluechip Industries Limited", gst=_VALID_GST, org_type="Public Limited",
            sections={
                "company_profile": [
                    _f("Bluechip Industries Limited is an NSE-listed manufacturer; status active.",
                       "https://www.mca.gov.in/"),
                    _f("Profile corroborated by financial press coverage.",
                       "https://economictimes.indiatimes.com/x"),
                ],
                "credit_ratings": [_f("Long-term rating AA/Stable affirmed.", "https://www.crisil.com/x")],
                "mca_filings": [_f("MCA status: Active; filings up to date.", "https://www.mca.gov.in/")],
            },
        ),
        {"LOW"}, {"HIGH", "MEDIUM"}, {"APPROVE", "APPROVE_WITH_MONITORING"},
    ),
    (
        "private_clean_verified",
        dict(
            name="Acme Components Private Limited", gst=_VALID_GST, org_type="Private Limited",
            sections={
                "company_profile": [_f("GST status: Active.", "https://www.gst.gov.in/")],
                "mca_filings": [_f("MCA CIN active; no strike-off.", "https://www.mca.gov.in/")],
            },
        ),
        {"LOW"}, {"HIGH", "MEDIUM"}, {"APPROVE", "APPROVE_WITH_MONITORING"},
    ),
    (
        "startup_minor_news",
        dict(
            name="NimbleAI Technologies Private Limited", gst=_VALID_GST, org_type="Private Limited",
            sections={
                "company_profile": [_f("Seed-stage startup; GST active.", "https://www.gst.gov.in/")],
                "funds_raised": [_f("Raised seed round; no irregularities.", "https://inc42.com/x")],
            },
            adverse=[_adv("Founder in a minor service dispute with a vendor (pending).",
                          "https://www.business-standard.com/x", "LOW")],
        ),
        {"LOW", "MEDIUM"}, {"HIGH", "MEDIUM", "LOW"},
        {"APPROVE_WITH_MONITORING", "CONDITIONAL_APPROVAL"},
    ),
    (
        "llp_sparse_no_gst",
        dict(
            name="Sundry Traders LLP", org_type="LLP",
            sections={"company_profile": [_f("No public footprint located.",
                                             "https://www.google.com/search?q=sundry")]},
        ),
        {"LOW"}, {"LOW"}, {"MANUAL_REVIEW_REQUIRED", "APPROVE_WITH_MONITORING"},
    ),
    (
        "ngo_trust_clean",
        dict(
            name="Helping Hands Foundation", org_type="Trust",
            sections={
                "company_profile": [_f("Registered charitable trust; FCRA listed.",
                                       "https://fcraonline.nic.in/")],
            },
        ),
        {"LOW"}, {"HIGH", "MEDIUM", "LOW"},
        {"APPROVE", "APPROVE_WITH_MONITORING", "MANUAL_REVIEW_REQUIRED"},
    ),
    (
        "defunct_struck_off_verified",
        dict(
            name="Ghost Trading Private Limited", gst=_VALID_GST, org_type="Private Limited",
            sections={
                "mca_filings": [_f("Company struck off the register of companies by the ROC.",
                                   "https://www.mca.gov.in/", "HIGH")],
            },
        ),
        {"HIGH"}, {"HIGH", "MEDIUM"}, {"REJECT", "ENHANCED_DUE_DILIGENCE"},
    ),
    (
        "confirmed_fraud_verified",
        dict(
            name="Scamcorp Limited", gst=_VALID_GST, org_type="Public Limited",
            fraud=[_adv("SFIO chargesheet: promoters convicted in a multi-crore bank fraud.",
                        "https://sfio.nic.in/x", "HIGH")],
        ),
        {"HIGH"}, {"HIGH", "MEDIUM"}, {"REJECT"},
    ),
    (
        "active_insolvency_verified",
        dict(
            name="Distressed Steel Limited", gst=_VALID_GST, org_type="Public Limited",
            sections={
                "litigations": [_f("NCLT admitted CIRP; insolvency proceedings initiated against the company.",
                                   "https://ibbi.gov.in/x", "HIGH")],
            },
        ),
        {"HIGH"}, {"HIGH", "MEDIUM"}, {"REJECT", "ENHANCED_DUE_DILIGENCE"},
    ),
    (
        "sanctions_exposure_verified",
        dict(
            name="Sanctioned Exports Limited", gst=_VALID_GST, org_type="Public Limited",
            fraud=[_adv("Entity appears on the OFAC SDN sanctions list.",
                        "https://sanctionssearch.ofac.treas.gov/x", "HIGH")],
        ),
        {"HIGH"}, {"HIGH", "MEDIUM"}, {"REJECT"},
    ),
    (
        "fraud_media_only_unverified",
        dict(
            name="Rumourmill Retail Limited", gst=_VALID_GST, org_type="Public Limited",
            adverse=[_adv("Blog alleges the company committed fraud (no official confirmation).",
                          "https://some-random-blog.xyz/x", "HIGH")],
        ),
        # Unverified severe allegation must NOT auto-REJECT — it routes to EDD.
        {"MEDIUM", "HIGH"}, {"HIGH", "MEDIUM", "LOW"}, {"ENHANCED_DUE_DILIGENCE"},
    ),
    (
        "clean_company_well_sourced",
        dict(
            name="Steadfast Logistics Private Limited", gst=_VALID_GST, org_type="Private Limited",
            sections={
                "company_profile": [
                    _f("GST active; no adverse records.", "https://www.gst.gov.in/"),
                    _f("No litigation surfaced on Indian Kanoon.", "https://indiankanoon.org/x"),
                ],
                "mca_filings": [_f("MCA active; filings current.", "https://www.mca.gov.in/")],
            },
        ),
        {"LOW"}, {"HIGH", "MEDIUM"}, {"APPROVE", "APPROVE_WITH_MONITORING"},
    ),
]


@pytest.mark.parametrize(
    "scenario_id,kwargs,exp_rating,exp_conf,exp_rec",
    _SCENARIOS,
    ids=[s[0] for s in _SCENARIOS],
)
def test_scenario_matrix(scenario_id, kwargs, exp_rating, exp_conf, exp_rec) -> None:
    data = _build_report(**kwargs)
    _ensure_calibrated_rubric(data)
    es = data["executive_summary"]
    assert es["risk_rating"] in exp_rating, (
        f"{scenario_id}: rating {es['risk_rating']} not in {exp_rating} "
        f"(score={es['risk_score']}, veto={es['veto_triggered']})"
    )
    assert es["confidence"] in exp_conf, f"{scenario_id}: confidence {es['confidence']} not in {exp_conf}"
    assert es["recommendation_tier"] in exp_rec, (
        f"{scenario_id}: recommendation {es['recommendation_tier']} not in {exp_rec}"
    )


@pytest.mark.parametrize("scenario_id,kwargs,exp_rating,exp_conf,exp_rec", _SCENARIOS,
                         ids=[s[0] for s in _SCENARIOS])
def test_scenarios_are_deterministic(scenario_id, kwargs, exp_rating, exp_conf, exp_rec) -> None:
    """Same input ⇒ identical rating / score / confidence / recommendation across runs."""
    out = []
    for _ in range(3):
        data = _build_report(**copy.deepcopy(kwargs))
        _ensure_calibrated_rubric(data)
        es = data["executive_summary"]
        out.append((es["risk_rating"], es["risk_score"], es["confidence"], es["recommendation_tier"]))
    assert len(set(out)) == 1, f"{scenario_id}: non-deterministic outputs {out}"


def test_reject_requires_verified_source() -> None:
    """The same severe wording REJECTs on a Tier-1 source but only EDDs on a blog."""
    verified = _build_report(
        name="X Limited", gst=_VALID_GST,
        fraud=[_adv("Promoters convicted of fraud.", "https://cbi.gov.in/x", "HIGH")],
    )
    _ensure_calibrated_rubric(verified)
    assert verified["executive_summary"]["recommendation_tier"] == "REJECT"

    unverified = _build_report(
        name="X Limited", gst=_VALID_GST,
        fraud=[_adv("Promoters convicted of fraud.", "https://some-blog.example/x", "HIGH")],
    )
    _ensure_calibrated_rubric(unverified)
    assert unverified["executive_summary"]["recommendation_tier"] != "REJECT"
