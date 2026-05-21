"""Abstract LLM provider — concrete implementations in provider modules."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel

# Structured output: either a JSON-schema-like dict or a Pydantic model class.
SchemaLike = dict[str, Any] | type[BaseModel]


class LLMProvider(ABC):
    """Contract for all LLM backends (Gemini, OpenAI, Anthropic)."""

    @abstractmethod
    async def generate(self, prompt: str, schema: SchemaLike) -> dict[str, Any]:
        """Run the model and return a JSON-compatible dict matching ``schema``."""

    @abstractmethod
    async def test_connection(self) -> bool:
        """Return True if credentials and API reachability are OK."""
