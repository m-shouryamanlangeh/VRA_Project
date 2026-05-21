"""OpenAI provider stub — future."""

from __future__ import annotations

from typing import Any

from app.core.llm.base import LLMProvider, SchemaLike


class OpenAIProvider(LLMProvider):
    """Placeholder OpenAI integration."""

    async def generate(self, prompt: str, schema: SchemaLike) -> dict[str, Any]:
        raise NotImplementedError("OpenAI provider is not implemented yet")

    async def test_connection(self) -> bool:
        raise NotImplementedError("OpenAI provider is not implemented yet")
