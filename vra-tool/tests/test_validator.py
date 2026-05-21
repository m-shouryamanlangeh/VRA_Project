"""Validator helpers and layout sanity checks."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import BASE_DIR
from app.core.prompts import format_adverse_media_prompt
from app.core.validator import is_plausible_url
from app.schemas import VendorGenerateRequest


def test_base_dir_exists() -> None:
    assert (BASE_DIR / "app").is_dir()
    assert (BASE_DIR / "data").is_dir()


@pytest.mark.parametrize(
    "url,ok",
    [
        ("https://example.com/path", True),
        ("http://paytm.com", True),
        ("not-a-url", False),
        ("", False),
        ("ftp://x.com", False),
    ],
)
def test_url_plausibility(url: str, ok: bool) -> None:
    assert is_plausible_url(url) is ok


def test_gst_validation_accepts_sample() -> None:
    body = VendorGenerateRequest(
        vendor_name="SHARP PENCIL PRODUCTIONS",
        gst="27ADKFS8129B1ZY",
        org_type="Partnership",
    )
    assert body.gst == "27ADKFS8129B1ZY"


def test_org_type_defaults_to_unknown_when_blank() -> None:
    body = VendorGenerateRequest(
        vendor_name="ACME",
        gst="27ADKFS8129B1ZY",
        org_type="",
    )
    assert body.org_type == "Unknown"
    body2 = VendorGenerateRequest.model_validate(
        {"vendor_name": "ACME", "gst": "27ADKFS8129B1ZY", "org_type": "   "}
    )
    assert body2.org_type == "Unknown"


def test_adverse_prompt_keeps_literal_name_placeholder() -> None:
    """Stakeholder URL pattern uses ``{name}`` for the LLM — not Python format."""
    out = format_adverse_media_prompt("Co", "27AAAAA0000A1Z5", "LLP", "2026-01-01")
    assert "{name}" in out
    assert "Co" in out


def test_gst_validation_rejects_invalid() -> None:
    with pytest.raises(ValidationError):
        VendorGenerateRequest(
            vendor_name="X",
            gst="INVALIDGSTNUMBER",
            org_type="LLP",
        )


def test_gst_optional_empty_omitted() -> None:
    body = VendorGenerateRequest(vendor_name="ACME Corp", org_type="LLP")
    assert body.gst == ""
    body2 = VendorGenerateRequest.model_validate({"vendor_name": "ACME Corp"})
    assert body2.gst == ""
    assert body2.org_type == "Unknown"


def test_gst_for_prompt_shows_hint_when_blank() -> None:
    from app.core.prompts import gst_for_prompt

    assert gst_for_prompt("") == gst_for_prompt("   ")
    assert "not provided" in gst_for_prompt("").lower()
    assert gst_for_prompt("27ADKFS8129B1ZY") == "27ADKFS8129B1ZY"
