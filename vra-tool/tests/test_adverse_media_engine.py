"""Adverse Media Engine — five-dimension article scoring."""

from __future__ import annotations

from app.core.risk.adverse_media import score_article


def test_positive_product_news_is_zero() -> None:
    s = score_article(
        title="Company launches AI fraud prevention to protect customers",
        url="https://economictimes.indiatimes.com/x",
        vendor_name="Company",
    )
    assert s.is_adverse is False
    assert s.composite == 0


def test_conviction_tier1_is_high() -> None:
    s = score_article(
        title="ACME Ltd convicted in fraud case; directors sentenced",
        url="https://indiankanoon.org/doc/1",
        vendor_name="ACME",
        base_band="HIGH",
    )
    assert s.band == "HIGH"
    assert s.composite >= 55


def test_low_tier_source_cannot_drive_high() -> None:
    """Same severe wording, but a forum/UGC source must not produce HIGH."""
    s = score_article(
        title="ACME Ltd convicted in fraud case; directors sentenced",
        url="https://reddit.com/r/x",
        vendor_name="ACME",
        base_band="HIGH",
    )
    assert s.band != "HIGH"


def test_regulator_fine_is_modest() -> None:
    s = score_article(
        title="ACME fined Rs 50 lakh by regulator for disclosure lapse",
        url="https://www.business-standard.com/x",
        vendor_name="ACME",
        base_band="MEDIUM",
    )
    assert s.band in ("LOW", "MEDIUM")
    assert s.composite < 55


def test_cleared_matter_collapses() -> None:
    s = score_article(
        title="ACME given clean chit; fraud case dismissed by High Court",
        url="https://www.thehindu.com/x",
        vendor_name="ACME",
        base_band="HIGH",
    )
    # Resolved favourably ⇒ near-zero impact.
    assert s.composite <= 15


def test_off_entity_article_dropped() -> None:
    s = score_article(
        title="Unrelated Hospital Trust investigated for fraud",
        url="https://www.thehindu.com/x",
        vendor_name="ACME Payments Limited",
    )
    assert s.is_adverse is False
    assert s.drop_reason == "not about this entity"


def test_historical_event_dampened() -> None:
    recent = score_article(
        title="ACME under ED probe for money laundering",
        url="https://reuters.com/x", vendor_name="ACME", base_band="HIGH",
        published="2025-01-01",
    )
    historical = score_article(
        title="ACME under ED probe for money laundering",
        url="https://reuters.com/x", vendor_name="ACME", base_band="HIGH",
        published="2009-01-01",
    )
    assert recent.composite > historical.composite
