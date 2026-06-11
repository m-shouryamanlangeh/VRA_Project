"""Source credibility tiering + fact-provenance classification."""

from __future__ import annotations

import pytest

from app.core.risk.fact_classification import FactType, classify_fact
from app.core.risk.source_credibility import SourceTier, classify_source


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.rbi.org.in/scripts/x.aspx", SourceTier.GOVERNMENT),
        ("https://sebi.gov.in/order", SourceTier.GOVERNMENT),
        ("https://press.mca.gov.in/release", SourceTier.GOVERNMENT),       # subdomain
        ("https://sfio.nic.in/release", SourceTier.GOVERNMENT),
        ("https://indiankanoon.org/doc/123/", SourceTier.GOVERNMENT),
        ("https://sanctionssearch.ofac.treas.gov/", SourceTier.GOVERNMENT),
        ("https://economictimes.indiatimes.com/x", SourceTier.ESTABLISHED_MEDIA),
        ("https://www.reuters.com/x", SourceTier.ESTABLISHED_MEDIA),
        ("https://tofler.in/company", SourceTier.INDUSTRY_COMMENTARY),
        ("https://the420.in/x", SourceTier.INDUSTRY_COMMENTARY),
        ("https://www.consumercomplaints.in/x", SourceTier.UNVERIFIED),
        ("https://reddit.com/r/x", SourceTier.UNVERIFIED),
        ("https://some-random-blog.xyz/post", SourceTier.UNVERIFIED),
        ("", SourceTier.UNVERIFIED),
        (None, SourceTier.UNVERIFIED),
    ],
)
def test_classify_source_tier(url, expected) -> None:
    assert classify_source(url).tier == expected


def test_rating_agency_promoted_only_for_credit_dimension() -> None:
    assert classify_source("https://crisil.com/x", dimension="credit_ratings").tier == SourceTier.GOVERNMENT
    assert classify_source("https://crisil.com/x").tier == SourceTier.ESTABLISHED_MEDIA


def test_tier_weight_monotonic() -> None:
    g = classify_source("https://rbi.org.in").weight
    m = classify_source("https://reuters.com").weight
    i = classify_source("https://tofler.in").weight
    u = classify_source("https://reddit.com").weight
    assert g > m > i > u


def test_fact_classification() -> None:
    assert classify_fact("RBI cancelled the licence", "https://rbi.org.in/x") == FactType.VERIFIED_FACT
    assert classify_fact("Firm probed for fraud", "https://reuters.com/x") == FactType.MEDIA_REFERENCE
    assert classify_fact("Allegation discussed", "https://reddit.com/x") == FactType.INFERENCE
    # Search-pointer URL proves nothing on its own.
    assert classify_fact("Some claim", "https://www.google.com/search?q=x") == FactType.INFERENCE
    # "Verify manually" wording forces INFERENCE even with an official-looking URL.
    assert classify_fact(
        "Possible default [Verify manually: source not retrieved by collectors.]",
        "https://www.mca.gov.in/",
    ) == FactType.INFERENCE
    assert classify_fact("No adverse record found", "https://rbi.org.in/x") == FactType.INFERENCE
