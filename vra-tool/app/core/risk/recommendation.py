"""Recommendation Engine — six evidence-gated outcomes.

The legacy engine collapsed everything into PROCEED / CONDITIONAL / REJECT and
auto-REJECTed on any HIGH rating, so an unproven media allegation could sink a
vendor. This engine emits the six required outcomes and, critically, makes
REJECT reachable ONLY on a verified, severe finding:

    APPROVE                 — no material concerns identified.
    APPROVE_WITH_MONITORING — minor findings only.
    CONDITIONAL_APPROVAL    — additional documents required.
    ENHANCED_DUE_DILIGENCE  — material concerns require investigation.
    MANUAL_REVIEW_REQUIRED  — insufficient / ambiguous evidence.
    REJECT                  — verified severe findings (sanctions, terror
                              financing, confirmed fraud, wilful-defaulter
                              record, active insolvency, serious criminal
                              proceedings) on an official record.

A HIGH risk rating that rests on *unverified* media routes to
ENHANCED_DUE_DILIGENCE, never an automatic REJECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Recommendation(str, Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_MONITORING = "APPROVE_WITH_MONITORING"
    CONDITIONAL_APPROVAL = "CONDITIONAL_APPROVAL"
    ENHANCED_DUE_DILIGENCE = "ENHANCED_DUE_DILIGENCE"
    MANUAL_REVIEW_REQUIRED = "MANUAL_REVIEW_REQUIRED"
    REJECT = "REJECT"


# Human-readable one-liners for the report.
RECOMMENDATION_LABEL: dict[str, str] = {
    Recommendation.APPROVE.value: "Approve — no material concerns identified.",
    Recommendation.APPROVE_WITH_MONITORING.value: "Approve with monitoring — minor findings only.",
    Recommendation.CONDITIONAL_APPROVAL.value: "Conditional approval — additional documents required.",
    Recommendation.ENHANCED_DUE_DILIGENCE.value: "Enhanced due diligence — material concerns require investigation.",
    Recommendation.MANUAL_REVIEW_REQUIRED.value: "Manual review required — insufficient or ambiguous evidence.",
    Recommendation.REJECT.value: "Reject — verified severe findings.",
}

# Back-compat mapping to the legacy 3-value field still used by older callers.
_LEGACY_MAP: dict[str, str] = {
    Recommendation.APPROVE.value: "PROCEED",
    Recommendation.APPROVE_WITH_MONITORING.value: "PROCEED",
    Recommendation.CONDITIONAL_APPROVAL.value: "CONDITIONAL",
    Recommendation.ENHANCED_DUE_DILIGENCE.value: "CONDITIONAL",
    Recommendation.MANUAL_REVIEW_REQUIRED.value: "CONDITIONAL",
    Recommendation.REJECT.value: "REJECT",
}


@dataclass
class RecommendationResult:
    recommendation: str            # one of Recommendation
    legacy: str                    # PROCEED | CONDITIONAL | REJECT
    rationale: str
    next_actions: list[str]


def _next_actions(rec: str) -> list[str]:
    return {
        Recommendation.APPROVE.value: [
            "Proceed under standard onboarding controls.",
            "Retain the OSINT evidence pack for the audit trail.",
            "Re-screen periodically and on any material change.",
        ],
        Recommendation.APPROVE_WITH_MONITORING.value: [
            "Onboard with periodic re-screening (recommended every 6 months).",
            "Document the minor findings and the basis for acceptance.",
        ],
        Recommendation.CONDITIONAL_APPROVAL.value: [
            "Obtain supporting documents (latest financials, GST returns, registration proof).",
            "Verify the flagged items with the vendor before contracting.",
            "Record a named approver's risk acceptance.",
        ],
        Recommendation.ENHANCED_DUE_DILIGENCE.value: [
            "Escalate to enhanced due diligence on the material concern(s).",
            "Obtain primary-source confirmation (court order, regulator filing).",
            "Run AML / sanctions screening on directors, partners, and UBOs.",
        ],
        Recommendation.MANUAL_REVIEW_REQUIRED.value: [
            "Collect KYC, GSTIN/CIN, and incorporation proof to establish identity.",
            "Disambiguate the legal entity before any risk decision.",
            "Re-run the assessment once identifiers are confirmed.",
        ],
        Recommendation.REJECT.value: [
            "Do not onboard. Escalate to the risk committee with the verified findings.",
            "Preserve the verified-fact evidence (official source URLs).",
            "Require legal review before any future reconsideration.",
        ],
    }.get(rec, [])


def recommend(
    *,
    risk_band: str,
    confidence_band: str,
    verified_severe: bool,
    serious_unverified: bool,
    minor_findings: bool,
    evidence_sufficient: bool,
    entity_ambiguous: bool,
) -> RecommendationResult:
    """Map the assessment state to one of the six recommendations.

    Decision order (first match wins) — each branch is mutually exclusive and
    documented so the choice is auditable:
    """
    risk_band = (risk_band or "LOW").upper()
    conf = (confidence_band or "LOW").upper()

    if verified_severe:
        rec, why = (
            Recommendation.REJECT.value,
            "A severe finding (e.g. sanctions, confirmed fraud, wilful default, "
            "active insolvency, or serious criminal proceeding) is substantiated "
            "by a Tier-1 official source.",
        )
    elif entity_ambiguous:
        rec, why = (
            Recommendation.MANUAL_REVIEW_REQUIRED.value,
            "The exact legal entity could not be resolved (multiple distinct "
            "candidates share the name); identity must be confirmed before a "
            "risk decision.",
        )
    elif serious_unverified or risk_band == "HIGH":
        # A material red flag drives INVESTIGATION even when it is poorly
        # sourced — that is precisely what enhanced due diligence is for.
        rec, why = (
            Recommendation.ENHANCED_DUE_DILIGENCE.value,
            "Material adverse signals were found but are not yet verified on an "
            "official record; investigation is required before a decision.",
        )
    elif not evidence_sufficient:
        rec, why = (
            Recommendation.MANUAL_REVIEW_REQUIRED.value,
            "Insufficient public-domain evidence to either clear or condemn the "
            "vendor; absence of adverse signal is not proof of a clean record.",
        )
    elif risk_band == "MEDIUM":
        rec, why = (
            Recommendation.CONDITIONAL_APPROVAL.value,
            "Moderate, documentable concerns were identified; approval is "
            "conditional on supporting documents and verification.",
        )
    else:  # LOW risk
        if minor_findings or conf == "LOW":
            rec, why = (
                Recommendation.APPROVE_WITH_MONITORING.value,
                "No material concerns; only minor or sparsely-evidenced signals "
                "were found, warranting periodic monitoring.",
            )
        else:
            rec, why = (
                Recommendation.APPROVE.value,
                "No material concerns identified and the assessment is "
                "adequately sourced.",
            )

    return RecommendationResult(
        recommendation=rec,
        legacy=_LEGACY_MAP[rec],
        rationale=why,
        next_actions=_next_actions(rec),
    )


def to_legacy(recommendation: str) -> str:
    return _LEGACY_MAP.get((recommendation or "").upper(), "CONDITIONAL")
