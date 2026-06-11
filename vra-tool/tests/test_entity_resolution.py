"""Entity Resolution — candidate extraction, confidence, ambiguity."""

from __future__ import annotations

from app.core.risk.entity_resolution import is_linked_to_entity, resolve_entity


def test_verified_gstin_and_legal_name_is_high() -> None:
    r = resolve_entity(
        input_name="ACME",
        gst="27ADKFS8129B1ZY",
        gst_legal_name="ACME Payments Limited",
        evidence_texts=["ACME Payments Limited is a fintech."],
    )
    assert r.confidence == "HIGH"
    assert r.resolved_name == "ACME Payments Limited"
    assert r.identifiers_verified is True


def test_name_only_dominant_candidate_is_medium() -> None:
    r = resolve_entity(
        input_name="Globex",
        evidence_texts=[
            "Globex Industries Limited announced results.",
            "Globex Industries Limited expanded operations.",
            "Globex Industries Limited hired a new CFO.",
        ],
    )
    assert r.confidence == "MEDIUM"
    assert "Globex Industries Limited" in r.resolved_name


def test_ambiguous_homonyms_are_low_confidence() -> None:
    r = resolve_entity(
        input_name="Airtel",
        evidence_texts=[
            "Bharti Airtel Limited reported profit.",
            "Bharti Airtel Limited raised tariffs.",
            "Airtel Payments Bank Limited launched a product.",
            "Airtel Payments Bank Limited fined by RBI.",
        ],
    )
    assert r.ambiguous is True
    assert r.confidence == "LOW"


def test_identifier_disambiguates_homonyms() -> None:
    r = resolve_entity(
        input_name="Airtel",
        gst="07AAACB2894G1ZX",
        gst_legal_name="Bharti Airtel Limited",
        evidence_texts=[
            "Bharti Airtel Limited reported profit.",
            "Airtel Payments Bank Limited fined by RBI.",
        ],
    )
    assert r.confidence == "HIGH"
    assert r.resolved_name == "Bharti Airtel Limited"


def test_no_evidence_is_low() -> None:
    r = resolve_entity(input_name="Tiny Unknown Vendor")
    assert r.confidence == "LOW"
    assert r.resolved_name == "Tiny Unknown Vendor"


def test_is_linked_rejects_off_entity_text() -> None:
    r = resolve_entity(
        input_name="ACME Payments",
        gst="27ADKFS8129B1ZY",
        gst_legal_name="ACME Payments Limited",
    )
    assert is_linked_to_entity("ACME Payments Limited fined by RBI", r) is True
    assert is_linked_to_entity("Unrelated Hospital Trust probed for fraud", r) is False
