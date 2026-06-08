"""OpenRouter provider — OpenAI-compatible API with 100+ models + web search plugin."""

from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from typing import Any

import httpx
from pydantic import BaseModel

from app.core.llm.base import LLMProvider, SchemaLike

logger = logging.getLogger(__name__)

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# Free models currently available on OpenRouter (updated 2025-05).
FREE_MODELS_WITH_SEARCH = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "openai/gpt-oss-120b:free",
]

# Default free model to use when none is configured.
DEFAULT_FREE_MODEL = "openai/gpt-oss-20b:free"

# Models known to support response_format=json_object.
# Others will rely on prompt-based JSON extraction.
_JSON_MODE_MODELS = {
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1",
    "openai/gpt-4-turbo",
}


def _supports_json_mode(model: str) -> bool:
    base = model.split(":")[0]
    return base in _JSON_MODE_MODELS


# Models that natively support web search on OpenRouter via plugins.
_SEARCH_SUPPORTED_MODELS = {
    "openai/gpt-4o",
    "openai/gpt-4o-mini",
    "openai/gpt-4.1",
    "anthropic/claude-sonnet-4-5",
    "anthropic/claude-opus-4",
    "perplexity/sonar-pro",
    "perplexity/sonar",
}


def _supports_web_search(model: str) -> bool:
    base = model.split(":")[0]  # strip :free / :online suffix
    return base in _SEARCH_SUPPORTED_MODELS or ":online" in model


def _normalize_jsonish_quotes(text: str) -> str:
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _first_balanced_json_object(s: str) -> str | None:
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(s)):
        c = s[i]
        if escape:
            escape = False
            continue
        if in_string:
            if c == "\\":
                escape = True
            elif c == '"':
                in_string = False
            continue
        if c == '"':
            in_string = True
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start: i + 1]
    return None


def _parse_json(text: str) -> dict[str, Any] | None:
    text = _normalize_jsonish_quotes((text or "").strip())
    # Strip markdown fences
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
    try:
        out = json.loads(text)
        return out if isinstance(out, dict) else None
    except JSONDecodeError:
        pass
    chunk = _first_balanced_json_object(text)
    if chunk:
        try:
            out = json.loads(chunk)
            return out if isinstance(out, dict) else None
        except JSONDecodeError:
            pass
    return None


class OpenRouterProvider(LLMProvider):
    """
    Calls OpenRouter (openrouter.ai) — OpenAI-compatible, 100+ models.

    Supports web search via the OpenRouter web plugin (injected automatically
    for models in _SEARCH_SUPPORTED_MODELS or with :online suffix).
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_FREE_MODEL,
        temperature: float = 0.2,
        max_output_tokens: int = 16384,
        use_search: bool = True,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("OpenRouter API key is empty")
        self._api_key = api_key.strip()
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._use_search = use_search
        self.last_total_token_count: int | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://vra-backgroundverification.netlify.app",
            "X-Title": "Paytm VRA Tool",
        }

    def _schema_instruction(self, schema: SchemaLike) -> str:
        """
        Produce a concise output reminder for free/small models.

        Intentionally avoids dumping the raw Pydantic JSON schema because
        small free models (e.g. gpt-oss-20b) confuse the schema definition
        itself with the expected output and return {"description": ..., "type": ...}
        instead of a filled object.

        Instead we include a minimal concrete skeleton that the model can copy
        and fill in.
        """
        from app.schemas import SynthesisResult, AdversePassResult, VRAReport  # local to avoid circular

        if not isinstance(schema, type):
            return ""

        if issubclass(schema, SynthesisResult):
            return (
                "\n\n"
                "=== REQUIRED OUTPUT FORMAT ===\n"
                "Return ONLY a JSON object (no markdown, no prose) with EXACTLY these fields filled in:\n"
                "{\n"
                '  "risk_rating": "LOW",          // REQUIRED: LOW | MEDIUM | HIGH\n'
                '  "recommendation": "PROCEED",   // REQUIRED: PROCEED | CONDITIONAL | REJECT\n'
                '  "executive_summary": {\n'
                '    "risk_rating": "LOW",\n'
                '    "risk_score": 12,\n'
                '    "confidence": "HIGH",\n'
                '    "veto_triggered": false,\n'
                '    "veto_reason": null,\n'
                '    "summary": "3-5 sentence analyst narrative citing top scoring dimensions",\n'
                '    "key_risk_drivers": ["finding 1", "finding 2", "finding 3"],\n'
                '    "key_mitigants": ["positive 1", "positive 2"],\n'
                '    "dimension_scores": {\n'
                '      "defaults": 0, "sanctions_aml_fraud": 0, "litigations": 0,\n'
                '      "statutory_compliance": 0, "credit_ratings": 0, "adverse_media": 0,\n'
                '      "borrowings": 0, "mca_filings": 0, "management_integrity": 0,\n'
                '      "financial_soundness": 0, "funds_raised": 0, "company_profile": 0\n'
                "    }\n"
                "  },\n"
                '  "top_findings": ["specific finding 1", "specific finding 2", "specific finding 3"],\n'
                '  "top_positives": ["positive 1", "positive 2"],\n'
                '  "news_severity": [{"title": "headline text", "severity": "LOW"}]\n'
                "}\n"
                "Replace ALL placeholder values with real analysis. "
                "risk_rating and recommendation are MANDATORY — the response is invalid without them."
            )

        # For other schemas (VRAReport, AdversePassResult, etc.) fall back to a
        # minimal reminder — their prompts already contain full format instructions.
        return (
            "\n\nReturn ONLY a valid JSON object — no markdown fences, no prose before or after."
        )

    async def generate(
        self,
        prompt: str,
        schema: SchemaLike,
        *,
        use_search: bool = True,
    ) -> dict[str, Any]:
        full_prompt = prompt + self._schema_instruction(schema)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": "user", "content": full_prompt}],
            "temperature": self._temperature,
            "max_tokens": self._max_output_tokens,
        }
        # Only enable JSON mode for models that support it; others use prompt-based extraction.
        if _supports_json_mode(self._model):
            body["response_format"] = {"type": "json_object"}

        # Enable web search plugin when model supports it
        if use_search and self._use_search and _supports_web_search(self._model):
            body["plugins"] = [{"id": "web"}]
            logger.debug("OpenRouter web search plugin enabled for model %s", self._model)

        async with httpx.AsyncClient(timeout=180.0, verify=False) as client:
            resp = await client.post(
                f"{OPENROUTER_BASE}/chat/completions",
                headers=self._headers(),
                json=body,
            )
            if resp.status_code == 429:
                raise RuntimeError(
                    f"OpenRouter rate limit / quota exceeded (model={self._model}). "
                    "Add a fallback key or switch model."
                )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"OpenRouter API error {resp.status_code}: {resp.text[:500]}"
                )

            data = resp.json()

        # Token tracking
        usage = data.get("usage") or {}
        total = usage.get("total_tokens")
        if total is not None:
            self.last_total_token_count = int(total)

        # Extract content
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenRouter returned no choices in response")

        content = (choices[0].get("message") or {}).get("content") or ""
        if not content.strip():
            finish = (choices[0].get("finish_reason") or "")
            raise RuntimeError(
                f"OpenRouter returned empty content (model={self._model}, finish={finish})"
            )

        parsed = _parse_json(content)
        if parsed is None:
            raise RuntimeError(
                f"OpenRouter model returned non-JSON text: {content[:300]}"
            )
        return parsed

    async def test_connection(self) -> bool:
        ok, _ = await self.test_connection_detail()
        return ok

    async def test_connection_detail(self) -> tuple[bool, str]:
        try:
            body: dict[str, Any] = {
                "model": self._model,
                "messages": [{"role": "user", "content": 'Reply with {"ok": true}'}],
                "max_tokens": 32,
            }
            if _supports_json_mode(self._model):
                body["response_format"] = {"type": "json_object"}
            async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
                resp = await client.post(
                    f"{OPENROUTER_BASE}/chat/completions",
                    headers=self._headers(),
                    json=body,
                )
            if resp.status_code >= 400:
                return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
            return True, f"OK (model: {self._model})"
        except Exception as exc:
            return False, str(exc)
