"""Keyword-tier severity classification for news headlines / web snippets."""

from __future__ import annotations

import pytest

from app.core.hybrid_report import _classify_snippet_severity


@pytest.mark.parametrize(
    "text,expected",
    [
        # 🔴 HIGH — veto-class
        ("ED attaches assets in money laundering case", "HIGH"),
        ("Promoter arrested in bank fraud, FIR registered", "HIGH"),
        ("Firm declared a wilful defaulter by lenders", "HIGH"),
        ("CBI probe into shell company transactions", "HIGH"),
        ("Director named in PMLA chargesheet", "HIGH"),
        # 🟠 MEDIUM — serious red flags
        ("SEBI order imposes penalty for disclosure lapse", "MEDIUM"),
        ("NCLT admits company into recovery proceedings", "MEDIUM"),
        ("Lender invokes SARFAESI over loan default", "MEDIUM"),
        ("Company gets show cause notice from RBI", "MEDIUM"),
        ("Rating downgrade on weak cash flows", "MEDIUM"),
        # 🟡 LOW — minor / civil
        ("Consumer complaint filed in district forum", "LOW"),
        ("PIL challenges land allotment", "LOW"),
        ("Vendor in a contractual dispute resolved amicably", "MEDIUM"),  # 'dispute' is MEDIUM
        ("Minor controversy over an advertisement", "LOW"),
        # ✅ INFO — no risk signal
        ("Company launches new product line, posts record growth", "INFO"),
        ("No adverse records found for this vendor", "INFO"),
    ],
)
def test_classify_snippet_severity(text: str, expected: str) -> None:
    assert _classify_snippet_severity(text) == expected


def test_word_boundary_does_not_misfire() -> None:
    """Short tokens must match as whole words, not inside unrelated words."""
    # 'fir' (FIR) must not fire inside 'fir tree'; 'fine' not inside 'defined';
    # 'raid' not inside 'afraid'; 'pil' not inside 'compile'.
    assert _classify_snippet_severity("A fir tree was planted in the office garden") == "INFO"
    assert _classify_snippet_severity("The vendor defined its refund policy clearly") == "INFO"
    assert _classify_snippet_severity("Staff were not afraid to compile the report") == "INFO"
