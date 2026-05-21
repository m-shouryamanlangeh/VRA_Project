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
