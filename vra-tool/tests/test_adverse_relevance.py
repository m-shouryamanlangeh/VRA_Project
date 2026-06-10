"""Vendor relevance for adverse-media rows."""

from app.core.adverse_relevance import adverse_text_matches_vendor


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
