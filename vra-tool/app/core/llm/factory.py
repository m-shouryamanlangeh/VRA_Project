"""LLM provider factory."""

from __future__ import annotations

from typing import Any

from app.core.llm.anthropic import AnthropicProvider
from app.core.llm.base import LLMProvider
from app.core.llm.gemini import GeminiProvider
from app.core.llm.openai import OpenAIProvider
from app.core.llm.openrouter import OpenRouterProvider


def get_provider(name: str, **kwargs: Any) -> LLMProvider:
    """
    Return an LLM provider instance by name.

    Supported providers: gemini, openrouter, openai, anthropic.
    All require ``api_key=`` plus optional ``model``, ``temperature``,
    ``max_output_tokens``.
    """
    key = (name or "").strip().lower()

    if key == "gemini":
        api_key = kwargs.get("api_key")
        if not api_key:
            raise ValueError("get_provider('gemini') requires api_key=...")
        return GeminiProvider(
            api_key,
            model=kwargs.get("model", "gemini-2.0-flash"),
            temperature=float(kwargs.get("temperature", 0.2)),
            max_output_tokens=int(kwargs.get("max_output_tokens", 16384)),
        )

    if key == "openrouter":
        api_key = kwargs.get("api_key")
        if not api_key:
            raise ValueError("get_provider('openrouter') requires api_key=...")
        return OpenRouterProvider(
            api_key,
            model=kwargs.get("model", "google/gemini-2.0-flash-exp:free"),
            temperature=float(kwargs.get("temperature", 0.2)),
            max_output_tokens=int(kwargs.get("max_output_tokens", 16384)),
        )

    if key == "openai":
        return OpenAIProvider()
    if key in ("anthropic", "claude"):
        return AnthropicProvider()
    raise ValueError(f"Unknown LLM provider: {name!r}")
