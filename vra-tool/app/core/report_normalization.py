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
    """Highest severity-band across findings in a section, with veto bumps.

    Veto-text bump only fires when the LLM already scored the finding HIGH
    (base ≥ 75).  Applying it to LOW/INFO findings causes false positives
    because sentences like "Not found on sanctions lists" or "No wilful
    default records found" contain veto keywords in a NEGATIVE context.
    """
    if not findings:
        return 0
    best = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "").upper()
        base = _SEVERITY_TO_SCORE.get(sev, 25)
        # Only apply text-based veto bump when the LLM already flagged HIGH.
        # LOW/INFO findings that mention "sanction" / "default" etc. are almost
        # always negative-context ("Not found on…") and must NOT be bumped.
        if base >= 75:
            text = f.get("point") or f.get("summary") or ""
            bumped = max(base, _score_for_finding_text(str(text)))
        else:
            bumped = base
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

    # Findings are the ONLY ground truth for dimension scores.
    # Gemini's dimension_scores JSON field is systematically unreliable —
    # it hallucinates 100 (veto-class) even when per-section findings are
    # all INFO or LOW (e.g. "No records found").  The previous partial fix
    # (cap only when derived==0) still let hallucinated 100s through whenever
    # findings were LOW (derived=25).  The correct rule: always use derived.
    #
    # If Gemini genuinely found something severe it MUST have written a
    # HIGH-severity finding in the relevant section — the derived score will
    # then be ≥ 75 automatically.  There is no legitimate case where
    # Gemini's summary score should exceed what the per-section findings show.
    for section, dim_key in _SECTION_TO_DIMENSION.items():
        derived = _derive_dimension_score(data.get(section) or [])
        dim[dim_key] = derived

    # Anything still negative → 0 (no signal).
    for k in _ALL_DIMENSIONS:
        if dim.get(k, -1) < 0:
            dim[k] = 0

    es["dimension_scores"] = dim

    # 2. risk_score — always recompute from derived dimension scores.
    # Gemini's own risk_score is NOT trusted because it was computed against
    # its hallucinated dimension_scores, not the corrected derived ones.
    score = _compute_risk_score(dim)
    es["risk_score"] = score

    # 3. veto_triggered — derived ONLY from dimension scores computed above.
    # Gemini's veto_triggered flag is NOT trusted — it is systematically set to
    # true even when no per-section finding justifies it (same hallucination
    # pattern as dimension_scores).  The single source of truth is whether any
    # derived dimension hit 100 (i.e. a HIGH-severity finding in that section).
    veto = any(v >= 100 for v in dim.values())
    es["veto_triggered"] = veto
    if veto:
        # Set or overwrite veto_reason to match the actual triggering dimension.
        for k in ("defaults", "sanctions_aml_fraud", "management_integrity",
                  "litigations", "statutory_compliance", "mca_filings"):
            if dim.get(k, 0) >= 100:
                es["veto_reason"] = f"Auto-HIGH: severe finding in {k.replace('_', ' ')}."
                break
    else:
        # Clear any hallucinated veto_reason from Gemini.
        es.pop("veto_reason", None)

    # 4. confidence — default to MEDIUM if LLM didn't set it.
    conf = str(es.get("confidence") or "").upper()
    if conf not in ("HIGH", "MEDIUM", "LOW"):
        conf = "MEDIUM"
    # Safety: if score=0 and ALL dimension scores are 0, the LLM has no adverse
    # signals — could be genuinely clean OR unknown small company.
    # Downgrade to LOW confidence (MEDIUM or HIGH) unless the LLM had real positive
    # evidence: HIGH confidence is only valid when GSTIN/CIN confirmed in ≥2 sources.
    # LOW confidence → CONDITIONAL recommendation (Step 6) prevents false PROCEED.
    if score == 0 and all(v == 0 for v in dim.values()) and conf in ("MEDIUM", "HIGH"):
        conf = "LOW"
        logger.info(
            "_ensure_calibrated_rubric: all dimensions=0, score=0 — downgrading "
            "confidence %s→LOW (no adverse signals found; forcing CONDITIONAL)", conf
        )
    es["confidence"] = conf

    # 5. risk_rating — always use the mechanically computed rating.
    # Gemini's risk_rating is NOT trusted: it hallucinates HIGH even when all
    # per-section findings are INFO/LOW and veto=False.  The "never downgrade"
    # rule was a safeguard for human reviewers; it does not apply to an LLM
    # whose structured fields are systematically wrong.  The computed_rating
    # from our derived dimension scores + veto flag is the single source of truth.
    rr = _score_to_rating(score, dim, veto)
    es["risk_rating"] = rr

    # 6. recommendation — always derive mechanically from computed rating.
    # Gemini's recommendation is NOT trusted (it follows its hallucinated rating).
    # The computed recommendation from our derived rating + confidence is the
    # single source of truth.  Safety-bias: never let PROCEED through when rating
    # is MEDIUM or HIGH — the expected_rec from _rating_to_recommendation already
    # enforces this.
    data["recommendation"] = _rating_to_recommendation(rr, conf)


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
