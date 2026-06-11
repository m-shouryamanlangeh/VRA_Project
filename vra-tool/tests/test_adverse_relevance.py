"""Vendor relevance for adverse-media rows."""

from app.core.adverse_relevance import (
    adverse_text_matches_vendor,
    is_navigation_chrome,
    is_vendor_published_or_explainer,
)


def test_homonym_sharp_not_vendor() -> None:
    vendor = "SHARP PENCIL PRODUCTIONS"
    assert not adverse_text_matches_vendor(
        "",
        "Frederick L. Sharp charged by SEC in penny stock fraud scheme.",
        vendor_name=vendor,
        gst="27ADKFS8129B1ZY",
    )
    assert not adverse_text_matches_vendor(
        "",
        "Sharp Memorial Hospital settles Medicare whistleblower case.",
        vendor_name=vendor,
        gst="27ADKFS8129B1ZY",
    )


def test_on_topic_headline_matches() -> None:
    vendor = "SHARP PENCIL PRODUCTIONS"
    assert adverse_text_matches_vendor(
        "",
        "SHARP PENCIL PRODUCTIONS partnership active in Mumbai Suburban GST records.",
        vendor_name=vendor,
        gst="27ADKFS8129B1ZY",
    )


def test_gst_in_blob_matches() -> None:
    assert adverse_text_matches_vendor(
        "",
        "Some headline mentioning 27ADKFS8129B1ZY compliance review.",
        vendor_name="OTHER CO",
        gst="27ADKFS8129B1ZY",
    )


def test_single_token_vendor_matches_real_adverse_media() -> None:
    """One-word vendor names (KINGFISHER, PAYTM) must surface their adverse media.

    Regression: the single-token branch used length-sensitive token_sort_ratio,
    which scored a ~10-char name against a full headline at ~20 (< 78) and
    silently rejected EVERY real headline — Kingfisher's ED/CBI/wilful-default
    news produced zero findings and an all-LOW report.
    """
    for headline in (
        "Kingfisher Airlines fraud: ED attaches Vijay Mallya assets worth Rs 14,000 crore",
        "CBI files chargesheet against Kingfisher Airlines in IDBI loan default case",
        "Vijay Mallya declared wilful defaulter over Kingfisher loans",
    ):
        assert adverse_text_matches_vendor("", headline, vendor_name="KINGFISHER", gst="")
    assert adverse_text_matches_vendor(
        "", "Paytm Payments Bank fined by RBI for KYC violations",
        vendor_name="PAYTM", gst="",
    )


def test_single_token_whole_word_guard() -> None:
    """A short single token must match as a whole word, not as a substring."""
    # "MART" must not hit inside "WALMART".
    assert not adverse_text_matches_vendor(
        "", "Walmart opens new fulfilment centre in Texas", vendor_name="MART", gst="",
    )


def test_scandal_with_shared_token_not_same_company() -> None:
    """Single shared distinctive token (e.g. SARADHA) must not match without other tokens / GST."""
    vendor = "Saradha Constructions Company Pvt Ltd"
    assert not adverse_text_matches_vendor(
        "",
        "Saradha Group chit fund scam: ED attaches assets",
        vendor_name=vendor,
        gst="",
    )
    assert adverse_text_matches_vendor(
        "",
        "Saradha Constructions Company Pvt Ltd wins municipal tender in Kolkata",
        vendor_name=vendor,
        gst="",
    )


# ── Vendor-as-publisher / explainer guard ────────────────────────────────────

def test_vendor_published_explainer_dropped() -> None:
    """The vendor's own blog/explainer pages are not adverse media about it.

    Regression: an Airtel finance-blog explainer titled "Who is a Wilful
    Defaulter as per RBI? - Airtel" was keyword-scored HIGH on Defaults and
    triggered an auto-veto → bogus REJECT for a clean-on-that-axis vendor.
    """
    # 1. Branded explainer title (any host).
    assert is_vendor_published_or_explainer(
        "Who is a Wilful Defaulter as per RBI? - Airtel",
        "https://example.com/x",
        "AIRTEL",
    )
    assert is_vendor_published_or_explainer(
        "Paperless KYC Documents: Complete Guide to e-KYC Process in India - Airtel",
        "https://example.com/x",
        "AIRTEL",
    )
    assert is_vendor_published_or_explainer(
        "How to Block Scam Calls on Mobile: Secure Your Phone - Airtel",
        "https://www.airtel.in/blog/scam-calls",
        "AIRTEL",
    )
    # 2. Hosted on the vendor's own domain (no explainer phrasing needed).
    assert is_vendor_published_or_explainer(
        "Airtel Thanks rewards", "https://www.airtel.in/anything", "AIRTEL"
    )


def test_real_adverse_media_not_dropped_by_explainer_guard() -> None:
    """Genuine third-party reporting must survive the explainer / publisher guard."""
    # Real news on a news outlet — no explainer phrasing, not the vendor's domain.
    assert not is_vendor_published_or_explainer(
        "Bharti Airtel under scanner for money laundering - The Economic Times",
        "https://economictimes.indiatimes.com/x",
        "AIRTEL",
    )
    assert not is_vendor_published_or_explainer(
        "ED probing money laundering cases against Airtel: FinMin - The Hindu",
        "https://www.thehindu.com/x",
        "AIRTEL",
    )
    assert not is_vendor_published_or_explainer(
        "CBI files chargesheet against Kingfisher Airlines in loan default case",
        "https://www.thehindu.com/x",
        "KINGFISHER",
    )
    # Explainer phrasing but branded to the OUTLET, not the vendor → keep.
    assert not is_vendor_published_or_explainer(
        "What is the Paytm money-laundering probe? - Times of India",
        "https://timesofindia.indiatimes.com/x",
        "PAYTM",
    )
    # Vendor token is a SUBSTRING of a news domain's label ("express" ⊂
    # "indianexpress") — must NOT be treated as the vendor's own domain.
    assert not is_vendor_published_or_explainer(
        "Express Logistics under ED probe for fund diversion",
        "https://indianexpress.com/article/x",
        "EXPRESS LOGISTICS",
    )


def test_navigation_chrome_detection() -> None:
    """Scraped search-index / nav chrome must be recognised as junk, real text not."""
    assert is_navigation_chrome(
        "[Page content] https://indiankanoon.org/search/?formInput=AIRTEL — AIRTEL "
        "Skip to main content Indian Kanoon - Search engine for Indian Law"
    )
    assert is_navigation_chrome(
        "Skip to main content Indian Kanoon Filter Results by Document Types All Laws"
    )
    # An actual NCLT order excerpt is NOT chrome.
    assert not is_navigation_chrome(
        "The Corporate Debtor committed default in payment. This petition is filed "
        "by invoking Section 9 of the Insolvency and Bankruptcy Code, 2016."
    )
