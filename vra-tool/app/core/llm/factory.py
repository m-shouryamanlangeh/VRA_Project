"""LLM provider factory."""

from __future__ import annotations

from typing import Any

from app.core.llm.anthropic import AnthropicProvider
from app.core.llm.base import LLMProvider
from app.core.llm.gemini import GeminiProvider
from app.core.llm.openai import OpenAIProvider


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """
    Return an LLM provider instance by name.

    For ``gemini``, pass ``api_key=`` and optional ``model``, ``temperature``,
    ``max_output_tokens``.

    Raises:
        ValueError: Unknown provider name or missing required arguments.
    """
    key = (name or "").strip().lower()
    if key == "gemini":
        api_key = kwargs.get("api_key")
        if not api_key:
            raise ValueError("get_provider('gemini') requires api_key=...")
        return GeminiProvider(
            api_key,
            model=kwargs.get("model", "gemini-2.0-flash-001"),
            temperature=float(kwargs.get("temperature", 0.2)),
            max_output_tokens=int(kwargs.get("max_output_tokens", 16384)),
        )
    if key == "openai":
        return OpenAIProvider()
    if key in ("anthropic", "claude"):
        return AnthropicProvider()
    raise ValueError(f"Unknown LLM provider: {name!r}")
