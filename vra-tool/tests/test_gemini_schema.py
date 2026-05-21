"""Gemini JSON Schema must omit keywords the API rejects (e.g. additionalProperties)."""

from __future__ import annotations

from google.genai import errors as genai_errors

from app.core.llm.gemini import (
    _gemini_json_schema_cleanup,
    _parse_model_json,
    _schema_as_gemini_json_dict,
    _skip_model_for_standard_generate_content,
    _is_retryable_with_fallback,
)
from app.schemas import VRAReport

_BANNED = frozenset({"additionalProperties", "unevaluatedProperties"})


def _assert_no_banned_keys(obj: object) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k not in _BANNED, f"unexpected key {k!r}"
            _assert_no_banned_keys(v)
    elif isinstance(obj, list):
        for i in obj:
            _assert_no_banned_keys(i)


def test_pydantic_vra_schema_normally_has_additional_properties() -> None:
    raw = VRAReport.model_json_schema(ref_template="#/$defs/{model}")
    dumped = str(raw)
    assert "additionalProperties" in dumped


def test_gemini_schema_cleanup_strips_banned_keys() -> None:
    cleaned = _schema_as_gemini_json_dict(VRAReport)
    _assert_no_banned_keys(cleaned)


def test_parse_model_json_fenced() -> None:
    raw = 'Here is JSON:\n```json\n{"a": 1}\n```\n'
    assert _parse_model_json(raw) == {"a": 1}


def test_parse_model_json_prefix_and_trailing_junk() -> None:
    raw = 'Sure — here is the report:\n{"vendor": {"name": "X"}, "ok": true}\n\nHope this helps.'
    out = _parse_model_json(raw)
    assert out["vendor"]["name"] == "X"
    assert out["ok"] is True


def test_interactions_only_api_error_is_retryable() -> None:
    exc = genai_errors.ClientError(
        400,
        {
            "error": {
                "message": "This model only supports Interactions API.",
                "status": "INVALID_ARGUMENT",
            }
        },
        None,
    )
    assert _is_retryable_with_fallback(exc) is True


def test_skip_gemini3_preview_for_standard_generate() -> None:
    assert _skip_model_for_standard_generate_content("gemini-3-pro-preview") is True
    assert _skip_model_for_standard_generate_content("gemini-3-flash-preview") is True
    assert _skip_model_for_standard_generate_content("gemini-live-2.5-flash-preview") is True
    assert _skip_model_for_standard_generate_content("gemini-2.0-flash") is False


def test_gemini_schema_cleanup_dict_input() -> None:
    dirty = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"a": {"type": "string", "unevaluatedProperties": False}},
    }
    cleaned = _gemini_json_schema_cleanup(dirty)
    assert cleaned == {"type": "object", "properties": {"a": {"type": "string"}}}
