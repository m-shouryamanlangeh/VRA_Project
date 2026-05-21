"""Coerce non-conforming LLM JSON into ``VRAReport``-compatible dicts (legacy / search path)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_VRA_LIST_KEYS = (
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
)

# Maps each report section to the 12-dimension rubric key. Some sections map 1:1;
# others (adverse_media, fraud_aml, management) are folded into the broader dimension.
_SECTION_TO_DIMENSION: dict[str, str] = {
    "company_profile":      "company_profile",
    "management":           "management_integrity",
    "credit_ratings":       "credit_ratings",
    "financial_soundness":  "financial_soundness",
    "borrowings":           "borrowings",
    "funds_raised":         "funds_raised",
    "mca_filings":          "mca_filings",
    "defaults":             "defaults",
    "litigations":          "litigations",
    "statutory_compliance": "statutory_compliance",
    "adverse_media":        "adverse_media",
    "fraud_aml":            "sanctions_aml_fraud",
}

# Weight (%) used in Step 2 of the rubric — must match the prompt instructions.
_DIMENSION_WEIGHTS: dict[str, int] = {
    "defaults":              15,
    "sanctions_aml_fraud":   15,
    "litigations":           10,
    "statutory_compliance":  10,
    "credit_ratings":         8,
    "adverse_media":         10,
    "borrowings":             7,
    "mca_filings":            5,
    "management_integrity":  10,
    "financial_soundness":    5,
    "funds_raised":           3,
    "company_profile":        2,
}
_ALL_DIMENSIONS: tuple[str, ...] = tuple(_DIMENSION_WEIGHTS.keys())

# Map severity tokens → 0/25/50/75/100 dimension score band.
_SEVERITY_TO_SCORE: dict[str, int] = {
    "HIGH":    75,
    "MEDIUM":  50,
    "LOW":     25,
    "INFO":     0,
    "NONE":     0,
    "":         0,
}


def _score_for_finding_text(text: str) -> int:
    """Bump score to 100 when the finding clearly cites a veto-class event."""
    t = (text or "").lower()
    veto_markers = (
        "wilful default", "willful default",
        "sanction", "ofac", "un consolidated",
        "ed chargesheet", "pmla", "sfio", "cbi chargesheet",
        "sebi debarment", "debarred",
        "cirp", "insolvency admitted", "liquidation order",
        "gst cancelled", "gstin cancelled", "fake invoic",
        "convicted", "conviction",
        "struck off", "struck-off", "disqualified director",
        "uapa", "fatf black",
    )
    return 100 if any(m in t for m in veto_markers) else 0


def _derive_dimension_score(findings: list[Any]) -> int:
    """Highest severity-band across findings in a section, with veto bumps."""
    if not findings:
        return 0
    best = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "").upper()
        base = _SEVERITY_TO_SCORE.get(sev, 25)
        text = f.get("point") or f.get("summary") or ""
        bumped = max(base, _score_for_finding_text(str(text)))
        best = max(best, bumped)
    return best


def _compute_risk_score(dimension_scores: dict[str, int]) -> int:
    """Weighted sum per Step 2 of the rubric — returns 0–100 integer."""
    total = 0.0
    for dim, weight in _DIMENSION_WEIGHTS.items():
        val = int(dimension_scores.get(dim, 0))
        val = max(0, min(100, val))
        total += (val * weight) / 100.0
    return max(0, min(100, int(round(total))))


def _score_to_rating(score: int, dim: dict[str, int], veto: bool) -> str:
    """Map score → HIGH/MEDIUM/LOW with veto + floor rules from Steps 3–4."""
    if veto:
        return "HIGH"
    if score >= 55:
        return "HIGH"
    # Floor rule: any of these ≥ 50 → cannot be LOW
    floor = (
        dim.get("litigations", 0) >= 50
        or dim.get("statutory_compliance", 0) >= 50
        or dim.get("adverse_media", 0) >= 50
    )
    if score >= 25 or floor:
        return "MEDIUM"
    return "LOW"


def _rating_to_recommendation(rating: str, confidence: str) -> str:
    """Step 6 mapping. LOW + LOW-confidence → CONDITIONAL (insufficient evidence)."""
    if rating == "HIGH":
        return "REJECT"
    if rating == "MEDIUM":
        return "CONDITIONAL"
    # rating == LOW
    if (confidence or "").upper() == "LOW":
        return "CONDITIONAL"
    return "PROCEED"


def _ensure_calibrated_rubric(data: dict[str, Any]) -> None:
    """Fill missing rubric fields (dimension_scores, risk_score, rating mapping)
    using findings from the report. Idempotent — keeps Gemini-supplied values
    when present and valid.
    """
    es = data.get("executive_summary")
    if not isinstance(es, dict):
        es = {}
        data["executive_summary"] = es

    # 1. dimension_scores — if Gemini didn't return them, derive from findings.
    dim_raw = es.get("dimension_scores")
    if not isinstance(dim_raw, dict):
        dim_raw = {}
    dim: dict[str, int] = {}
    for k in _ALL_DIMENSIONS:
        v = dim_raw.get(k)
        try:
            dim[k] = max(0, min(100, int(round(float(v))))) if v is not None else -1
        except (TypeError, ValueError):
            dim[k] = -1

    # For any missing dimension, derive from the corresponding section's findings.
    for section, dim_key in _SECTION_TO_DIMENSION.items():
        if dim.get(dim_key, -1) < 0:
            derived = _derive_dimension_score(data.get(section) or [])
            # Take the max if section already contributed (e.g. fraud_aml).
            existing = dim.get(dim_key, 0)
            dim[dim_key] = max(existing if existing > 0 else 0, derived)

    # Anything still negative → 0 (no signal).
    for k in _ALL_DIMENSIONS:
        if dim[k] < 0:
            dim[k] = 0

    es["dimension_scores"] = dim

    # 2. risk_score (compute if missing or out of range).
    score_raw = es.get("risk_score")
    try:
        score = int(round(float(score_raw))) if score_raw is not None else -1
    except (TypeError, ValueError):
        score = -1
    if score < 0 or score > 100:
        score = _compute_risk_score(dim)
    es["risk_score"] = score

    # 3. veto_triggered — true if any dimension hit 100.
    veto_existing = bool(es.get("veto_triggered"))
    veto_computed = any(v >= 100 for v in dim.values())
    veto = veto_existing or veto_computed
    es["veto_triggered"] = veto
    if veto and not es.get("veto_reason"):
        for k in ("defaults", "sanctions_aml_fraud", "management_integrity",
                  "litigations", "statutory_compliance", "mca_filings"):
            if dim.get(k, 0) >= 100:
                es["veto_reason"] = f"Auto-HIGH: severe finding in {k.replace('_', ' ')}."
                break

    # 4. confidence — default to MEDIUM if Gemini didn't set it.
    conf = str(es.get("confidence") or "").upper()
    if conf not in ("HIGH", "MEDIUM", "LOW"):
        conf = "MEDIUM"
    es["confidence"] = conf

    # 5. risk_rating — recompute if missing/invalid OR if Gemini's value contradicts
    #    the score + veto rules (e.g. score=70 but rating=LOW).
    rr = str(es.get("risk_rating") or es.get("risk_level") or "").upper()
    computed_rating = _score_to_rating(score, dim, veto)
    if rr not in ("HIGH", "MEDIUM", "LOW"):
        rr = computed_rating
    else:
        # Promote if the computed rating is stricter (never silently downgrade Gemini).
        order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
        if order[computed_rating] > order[rr]:
            rr = computed_rating
    es["risk_rating"] = rr

    # 6. recommendation — enforce Step 6 mapping mechanically.
    expected_rec = _rating_to_recommendation(rr, conf)
    given_rec = str(data.get("recommendation") or "").upper()
    if given_rec not in ("PROCEED", "CONDITIONAL", "REJECT"):
        data["recommendation"] = expected_rec
    else:
        # If Gemini said PROCEED but rating is MEDIUM/HIGH → override (safety bias).
        rec_order = {"PROCEED": 0, "CONDITIONAL": 1, "REJECT": 2}
        if rec_order[expected_rec] > rec_order[given_rec]:
            data["recommendation"] = expected_rec
        else:
            data["recommendation"] = given_rec


def normalize_legacy_vra_payload(
    raw: dict[str, Any],
    *,
    date_str: str,
    vendor_name: str,
    gst: str,
    org_type: str,
) -> dict[str, Any]:
    """
    Fix common alternate shapes (e.g. ``vendor_assessment`` root) before ``VRAReport.model_validate``.
    """
    data = dict(raw)
    if "vendor_assessment" in data and "vendor" not in data:
        va = data.pop("vendor_assessment")
        logger.info("Normalizing LLM payload: mapping vendor_assessment → vendor / executive_summary")
        if isinstance(va, dict):
            if isinstance(va.get("vendor"), dict):
                data["vendor"] = dict(va["vendor"])
            inner_es = va.get("executive_summary")
            if isinstance(inner_es, dict):
                data.setdefault("executive_summary", dict(inner_es))
            elif inner_es is not None:
                data.setdefault("executive_summary", {"summary": str(inner_es)})
            if "recommendation" not in data and va.get("recommendation"):
                r = str(va["recommendation"]).upper()
                if r in ("PROCEED", "CONDITIONAL", "REJECT"):
                    data["recommendation"] = r  # type: ignore[assignment]
            for list_key in _VRA_LIST_KEYS:
                if list_key not in data and list_key in va and isinstance(va[list_key], list):
                    data[list_key] = list(va[list_key])
        else:
            data.setdefault("executive_summary", {"summary": str(va)[:8000]})

    data.setdefault("vendor", {"name": vendor_name, "gst": gst, "org_type": org_type})
    data.setdefault("date_of_search", date_str)

    if "executive_summary" not in data or data["executive_summary"] in (None, {}):
        data["executive_summary"] = {"risk_level": "MEDIUM"}
    elif isinstance(data["executive_summary"], dict):
        es = data["executive_summary"]
        has_narrative = any(
            isinstance(es.get(k), str) and len((es.get(k) or "").strip()) > 40
            for k in ("summary", "text", "narrative", "overview", "description", "assessment")
        )
        if not has_narrative:
            logger.info("executive_summary has no narrative; leaving risk fields only for PDF fallback")

    if "recommendation" not in data or not data["recommendation"]:
        data["recommendation"] = "CONDITIONAL"

    rec = str(data["recommendation"]).upper()
    if rec not in ("PROCEED", "CONDITIONAL", "REJECT"):
        data["recommendation"] = "CONDITIONAL"
    else:
        data["recommendation"] = rec

    for k in _VRA_LIST_KEYS:
        if k not in data or data[k] is None:
            data[k] = []

    # Final pass: deterministically fill the calibrated rubric (dimension_scores,
    # risk_score, veto, confidence) and enforce Step-6 rating↔recommendation
    # mapping — even if Gemini ignored the new instructions in the prompt.
    _ensure_calibrated_rubric(data)

    return data
