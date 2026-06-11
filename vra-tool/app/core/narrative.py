"""Deterministic narrative generation (templated NLG) — LLM-free analyst prose.

When the platform runs with ``USE_LLM=false`` no model writes the executive
summary. This module composes the ``executive_summary`` narrative purely from the
calibrated, auditable outputs already produced by
``app.core.report_normalization._apply_production_framework``: the dimension
scores, the risk band + score, the independent confidence band, the six-tier
recommendation, and the ``why_rating`` explainability block.

Every sentence is traceable to a number or a cited finding — nothing is inferred,
guessed, or invented. The output is intentionally plain and consistent so a
compliance reviewer can reconcile each clause against the scorecard.
"""

from __future__ import annotations

from typing import Any

# Human-readable disposition phrasing for the six recommendation tiers.
_RECOMMENDATION_PHRASE: dict[str, str] = {
    "APPROVE": "approval with no material concerns identified",
    "APPROVE_WITH_MONITORING": "approval with ongoing monitoring of the minor findings noted",
    "CONDITIONAL_APPROVAL": "conditional approval, subject to the additional documents listed below",
    "ENHANCED_DUE_DILIGENCE": "enhanced due diligence before any onboarding decision",
    "MANUAL_REVIEW_REQUIRED": "manual review, because the evidence gathered is insufficient for an automated decision",
    "REJECT": "rejection, on the basis of the verified severe findings detailed below",
}


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    return []


def _join_clause(items: list[str], *, limit: int = 3) -> str:
    """Join a few short phrases into a readable, comma-separated clause."""
    picked = [i.rstrip(".") for i in items[:limit]]
    if not picked:
        return ""
    if len(picked) == 1:
        return picked[0]
    if len(picked) == 2:
        return f"{picked[0]} and {picked[1]}"
    return f"{', '.join(picked[:-1])}, and {picked[-1]}"


def populate_narrative(data: dict[str, Any], *, force: bool = False) -> None:
    """Fill ``executive_summary`` narrative fields from calibrated data, in place.

    Sets ``summary``, ``key_risk_drivers`` and ``key_mitigants`` from the
    deterministic ``why_rating`` block. Existing values are preserved unless
    ``force=True`` (so an LLM-authored summary is never clobbered by accident —
    this is only invoked on the LLM-free path).
    """
    es = data.get("executive_summary")
    if not isinstance(es, dict):
        es = {}
        data["executive_summary"] = es

    why = es.get("why_rating") if isinstance(es.get("why_rating"), dict) else {}

    rating = str(es.get("risk_rating") or why.get("rating") or "LOW").upper()
    score = es.get("risk_score", why.get("risk_score"))
    confidence = str(es.get("confidence") or why.get("confidence") or "LOW").upper()
    tier = str(es.get("recommendation_tier") or why.get("recommendation") or "").upper()

    negatives = _as_list(why.get("top_negative_factors"))
    positives = _as_list(why.get("top_positive_factors"))
    missing = _as_list(why.get("missing_information"))

    # ── key_risk_drivers / key_mitigants ────────────────────────────────────
    if force or not es.get("key_risk_drivers"):
        es["key_risk_drivers"] = negatives or ["No adverse dimension scored above zero."]
    if force or not es.get("key_mitigants"):
        es["key_mitigants"] = positives or ["No positive signal could be evidenced."]
    # Keep legacy aliases populated for any consumer that reads them.
    es.setdefault("top_findings", list(es["key_risk_drivers"]))
    es.setdefault("top_positives", list(es["key_mitigants"]))

    # ── Prose summary ────────────────────────────────────────────────────────
    if not (force or not es.get("summary")):
        return

    vendor = data.get("vendor") or {}
    name = str(vendor.get("name") or "The entity").strip() or "The entity"
    org_type = str(vendor.get("org_type") or "").strip()
    subject = f"{name} ({org_type})" if org_type and org_type.lower() != "unknown" else name

    er = es.get("entity_resolution") if isinstance(es.get("entity_resolution"), dict) else {}
    resolved = str(er.get("resolved_name") or er.get("resolved_entity") or "").strip()
    er_conf = str(er.get("confidence") or "").upper()

    score_txt = f" (risk score {int(score)}/100)" if isinstance(score, (int, float)) else ""
    disposition = _RECOMMENDATION_PHRASE.get(tier, "")

    sentences: list[str] = []
    sentences.append(
        f"This assessment of {subject} returns an overall risk rating of {rating}{score_txt}, "
        f"held with {confidence} confidence in the underlying evidence."
    )
    if disposition:
        sentences.append(f"The recommended disposition is {disposition}.")

    if resolved and resolved.lower() != name.lower():
        conf_clause = f" ({er_conf.lower()} confidence)" if er_conf else ""
        sentences.append(
            f"The searched name was resolved to the legal entity {resolved}{conf_clause}; "
            "all findings below are scoped to that entity."
        )
    elif er.get("ambiguous"):
        sentences.append(
            "The searched name could not be resolved to a single legal entity "
            "(multiple plausible matches), which constrains the confidence of this report."
        )

    neg_clause = _join_clause(negatives)
    if neg_clause and negatives != ["No adverse dimension scored above zero."]:
        sentences.append(f"The rating is driven primarily by {neg_clause}.")
    else:
        sentences.append("No adverse dimension scored above zero on the evidence gathered.")

    pos_clause = _join_clause(positives)
    if pos_clause and positives != ["No positive signal could be evidenced."]:
        sentences.append(f"Mitigating factors include {pos_clause}.")

    miss_clause = _join_clause(missing)
    if miss_clause and missing != ["None material — assessment is well evidenced."]:
        sentences.append(f"Key evidence gaps remain: {miss_clause}.")

    sentences.append(
        "This report was produced by the deterministic rules engine (no language model). "
        "Every score is computed from the cited evidence — see the dimension scorecard and "
        "the \u201cWhy This Rating Was Assigned\u201d section for the full calculation."
    )

    es["summary"] = " ".join(sentences)
