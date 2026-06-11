"""Litigation Intelligence — nature/outcome/role/recency classification."""

from __future__ import annotations

from app.core.risk.litigation import (
    LitigationNature,
    LitigationOutcome,
    classify_litigation,
)


def test_fraud_is_high() -> None:
    a = classify_litigation("Company booked by CBI in a bank loan fraud case, chargesheet filed")
    assert a.risk_band == "HIGH"
    assert a.nature == LitigationNature.FRAUD_CRIMINAL


def test_commercial_dispute_is_low() -> None:
    a = classify_litigation("Vendor in a breach of contract / payment recovery suit before the High Court")
    assert a.risk_band == "LOW"
    assert a.nature == LitigationNature.COMMERCIAL


def test_consumer_dispute_is_low() -> None:
    a = classify_litigation("Consumer complaint filed in district consumer forum over service deficiency")
    assert a.risk_band == "LOW"


def test_dismissed_case_drops_to_info() -> None:
    a = classify_litigation("High Court quashed the FIR; fraud case dismissed for want of evidence")
    assert a.risk_band == "INFO"
    assert a.outcome == LitigationOutcome.DISMISSED


def test_acquittal_drops_to_info() -> None:
    a = classify_litigation("Promoter acquitted of all charges; court gave a clean chit")
    assert a.risk_band == "INFO"


def test_conviction_is_high_even_if_base_lower() -> None:
    a = classify_litigation("Director convicted and sentenced in a service-tax matter")
    assert a.risk_band == "HIGH"
    assert a.outcome == LitigationOutcome.CONVICTED


def test_entity_as_complainant_not_adverse() -> None:
    a = classify_litigation("Company moves court against a supplier seeking damages for breach")
    assert a.risk_band == "INFO"
    assert a.entity_is_complainant is True


def test_regulatory_investigation_is_medium() -> None:
    a = classify_litigation("SEBI show-cause notice issued; adjudication proceedings under way")
    assert a.risk_band == "MEDIUM"


def test_insolvency_against_is_high() -> None:
    a = classify_litigation("NCLT admitted Section 9 insolvency petition; CIRP initiated against the company")
    assert a.risk_band == "HIGH"
    assert a.nature == LitigationNature.INSOLVENCY_AGAINST


def test_stale_unresolved_is_dampened() -> None:
    recent = classify_litigation("Pending tax dispute before the tribunal, hearing in 2025")
    stale = classify_litigation("Pending tax dispute before the tribunal since 2009")
    assert recent.risk_band == "MEDIUM"
    assert stale.risk_band == "LOW"
