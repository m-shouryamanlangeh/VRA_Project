"""Confidence (evidence quality) + six-tier recommendation engine."""

from __future__ import annotations

from app.core.risk.confidence import SourceFact, compute_confidence
from app.core.risk.fact_classification import FactType
from app.core.risk.recommendation import Recommendation, recommend
from app.core.risk.source_credibility import SourceTier


def _fact(tier, ftype, recency=0.8):
    return SourceFact(tier=tier, fact_type=ftype, recency_factor=recency)


def test_high_confidence_with_official_recent_sources() -> None:
    facts = [
        _fact(SourceTier.GOVERNMENT, FactType.VERIFIED_FACT),
        _fact(SourceTier.GOVERNMENT, FactType.VERIFIED_FACT),
        _fact(SourceTier.ESTABLISHED_MEDIA, FactType.MEDIA_REFERENCE),
        _fact(SourceTier.ESTABLISHED_MEDIA, FactType.MEDIA_REFERENCE),
        _fact(SourceTier.ESTABLISHED_MEDIA, FactType.MEDIA_REFERENCE),
    ]
    r = compute_confidence(facts, entity_confidence="HIGH")
    assert r.band == "HIGH"


def test_low_confidence_when_empty() -> None:
    r = compute_confidence([], entity_confidence="LOW")
    assert r.band == "LOW"
    assert r.score < 34


def test_confidence_independent_of_risk() -> None:
    """A single unverified forum source ⇒ LOW confidence regardless of how
    severe the (alleged) content is."""
    facts = [_fact(SourceTier.UNVERIFIED, FactType.INFERENCE, recency=0.4)]
    r = compute_confidence(facts, entity_confidence="LOW")
    assert r.band == "LOW"


def test_reject_only_on_verified_severe() -> None:
    r = recommend(
        risk_band="HIGH", confidence_band="HIGH",
        verified_severe=True, serious_unverified=True,
        minor_findings=True, evidence_sufficient=True, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.REJECT.value
    assert r.legacy == "REJECT"


def test_high_risk_unverified_is_edd_not_reject() -> None:
    r = recommend(
        risk_band="HIGH", confidence_band="MEDIUM",
        verified_severe=False, serious_unverified=True,
        minor_findings=True, evidence_sufficient=True, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.ENHANCED_DUE_DILIGENCE.value
    assert r.legacy == "CONDITIONAL"


def test_ambiguous_entity_is_manual_review() -> None:
    r = recommend(
        risk_band="MEDIUM", confidence_band="LOW",
        verified_severe=False, serious_unverified=False,
        minor_findings=False, evidence_sufficient=True, entity_ambiguous=True,
    )
    assert r.recommendation == Recommendation.MANUAL_REVIEW_REQUIRED.value


def test_insufficient_evidence_is_manual_review() -> None:
    r = recommend(
        risk_band="LOW", confidence_band="LOW",
        verified_severe=False, serious_unverified=False,
        minor_findings=False, evidence_sufficient=False, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.MANUAL_REVIEW_REQUIRED.value


def test_clean_well_sourced_is_approve() -> None:
    r = recommend(
        risk_band="LOW", confidence_band="HIGH",
        verified_severe=False, serious_unverified=False,
        minor_findings=False, evidence_sufficient=True, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.APPROVE.value
    assert r.legacy == "PROCEED"


def test_minor_findings_is_approve_with_monitoring() -> None:
    r = recommend(
        risk_band="LOW", confidence_band="MEDIUM",
        verified_severe=False, serious_unverified=False,
        minor_findings=True, evidence_sufficient=True, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.APPROVE_WITH_MONITORING.value


def test_medium_risk_is_conditional() -> None:
    r = recommend(
        risk_band="MEDIUM", confidence_band="MEDIUM",
        verified_severe=False, serious_unverified=False,
        minor_findings=True, evidence_sufficient=True, entity_ambiguous=False,
    )
    assert r.recommendation == Recommendation.CONDITIONAL_APPROVAL.value
