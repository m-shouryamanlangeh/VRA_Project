"""Google Gemini provider with Google Search grounding."""

from __future__ import annotations

import json
import logging
from json import JSONDecodeError, JSONDecoder
from typing import Any, Optional

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel

from app.core.llm.base import LLMProvider, SchemaLike

logger = logging.getLogger(__name__)


class _EmptyResponseError(RuntimeError):
    """Raised when Gemini returns a 200 OK with empty text — treated as retryable."""


# Gemini structured-output rejects several JSON Schema keywords Pydantic emits.
_GEMINI_SCHEMA_STRIP_KEYS = frozenset(
    {
        "additionalProperties",
        "unevaluatedProperties",
    }
)


def _gemini_json_schema_cleanup(obj: Any) -> Any:
    """Recursively drop schema keys the Gemini API does not accept."""
    if isinstance(obj, dict):
        return {
            k: _gemini_json_schema_cleanup(v)
            for k, v in obj.items()
            if k not in _GEMINI_SCHEMA_STRIP_KEYS
        }
    if isinstance(obj, list):
        return [_gemini_json_schema_cleanup(i) for i in obj]
    return obj


def _normalize_jsonish_quotes(text: str) -> str:
    """Replace curly apostrophes (safe inside JSON string values)."""
    return text.replace("\u2018", "'").replace("\u2019", "'")


def _first_balanced_json_object(s: str) -> str | None:
    """
    Slice the first top-level ``{ ... }`` from *s*, respecting string escapes.

    Helps when the model prefixes/suffixes prose or emits trailing commas/noise
    after a valid object (``raw_decode`` handles trailing junk only after the value).
    """
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
                return s[start : i + 1]
    return None


def _try_parse_json_dict(s: str) -> dict[str, Any] | None:
    """Best-effort parse of a single JSON object into a dict."""
    s = _normalize_jsonish_quotes(s.strip())
    if not s:
        return None
    try:
        out = json.loads(s)
        return out if isinstance(out, dict) else None
    except JSONDecodeError:
        pass
    try:
        obj, _end = JSONDecoder().raw_decode(s)
        return obj if isinstance(obj, dict) else None
    except JSONDecodeError:
        pass
    chunk = _first_balanced_json_object(s)
    if not chunk:
        return None
    try:
        out = json.loads(chunk)
        return out if isinstance(out, dict) else None
    except JSONDecodeError:
        pass
    try:
        obj, _end = JSONDecoder().raw_decode(chunk)
        return obj if isinstance(obj, dict) else None
    except JSONDecodeError:
        return None


def _parse_model_json(text: str) -> dict[str, Any]:
    """
    Parse a JSON object from model text.

    When Google Search is enabled, the API cannot use ``response_mime_type:
    application/json``; models may return fenced or slightly noisy text.
    """
    t = (text or "").strip()
    if not t:
        raise ValueError("empty model text")
    if "```" in t:
        for chunk in t.split("```"):
            chunk = chunk.strip()
            if chunk.lower().startswith("json"):
                chunk = chunk[4:].lstrip()
            if chunk.startswith("{"):
                got = _try_parse_json_dict(chunk)
                if got is not None:
                    return got
    got = _try_parse_json_dict(t)
    if got is not None:
        return got
    raise JSONDecodeError("Could not parse a JSON object from model text", t, 0)


def _schema_as_gemini_json_dict(schema: SchemaLike) -> dict[str, Any]:
    """Build a JSON Schema dict suitable for ``response_json_schema``."""
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        raw = schema.model_json_schema(ref_template="#/$defs/{model}")
    elif isinstance(schema, dict):
        raw = dict(schema)
    else:
        raise TypeError(f"Unsupported schema type: {type(schema)!r}")
    return _gemini_json_schema_cleanup(raw)


_SEARCH_GROUNDING_BLOCKLIST = frozenset(
    {"lite", "image", "tts", "audio", "embedding", "aqa"}
)


def _model_supports_search_grounding(model: str) -> bool:
    """Only full Gemini flash/pro models support Google Search grounding."""
    m = (model or "").lower()
    if not m.startswith("gemini"):
        return False
    return not any(tok in m for tok in _SEARCH_GROUNDING_BLOCKLIST)


def _model_supports_json_mode(model: str) -> bool:
    """Gemma and other non-Gemini models do not support response_mime_type=application/json."""
    return (model or "").lower().startswith("gemini")


def _is_interactions_api_only_error(exc: BaseException) -> bool:
    """Some listed models only work via Interactions API, not ``generateContent``."""
    msg = str(exc).lower()
    return "interactions api" in msg and "only supports" in msg


def _is_search_not_supported_error(exc: BaseException) -> bool:
    """Model doesn't support search as a tool (e.g. Gemma models)."""
    msg = str(exc).lower()
    return "search as tool is not enabled" in msg or (
        "invalid_argument" in msg and "tool" in msg and "search" in msg
    )


def _is_json_mode_not_supported_error(exc: BaseException) -> bool:
    """Model doesn't support JSON mode (e.g. Gemma models)."""
    msg = str(exc).lower()
    return "json mode is not enabled" in msg or (
        "invalid_argument" in msg and "json" in msg
    )


def _is_retryable_with_fallback(exc: BaseException) -> bool:
    """Whether to try the next API key or model candidate."""
    if isinstance(exc, genai_errors.ClientError):
        if exc.code in (401, 403, 404, 429):
            return True
        if exc.code == 400 and (
            _is_interactions_api_only_error(exc)
            or _is_search_not_supported_error(exc)
            or _is_json_mode_not_supported_error(exc)
        ):
            return True
    if isinstance(exc, genai_errors.ServerError):
        return exc.code in (500, 503, 502)
    msg = str(exc).lower()
    if "quota" in msg or "rate" in msg or "exhausted" in msg:
        return True
    if "api key" in msg or "permission" in msg or "unauthorized" in msg:
        return True
    if _is_interactions_api_only_error(exc) or _is_search_not_supported_error(exc) or _is_json_mode_not_supported_error(exc):
        return True
    if isinstance(exc, _EmptyResponseError):
        return True
    return False


def _skip_model_for_standard_generate_content(model_id: str) -> bool:
    """
    Exclude model IDs that advertise generateContent but error with Interactions-only,
    or that are realtime / live variants not meant for batch generateContent.
    """
    u = (model_id or "").strip().lower()
    if not u:
        return True
    if "gemini-live" in u or u.startswith("gemini-live"):
        return True
    # Gemini 3 preview IDs commonly require Interactions API for standard calls.
    if "gemini-3" in u and "preview" in u:
        return True
    if "gemini-3.1" in u and "preview" in u:
        return True
    return False


class GeminiProvider(LLMProvider):
    """Gemini 2.x with Google Search tool and structured JSON output."""

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gemini-2.0-flash",
        temperature: float = 0.2,
        max_output_tokens: int = 16384,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ValueError("Gemini API key is empty")
        self._api_key = api_key.strip()
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._client = genai.Client(api_key=self._api_key)
        self.last_total_token_count: int | None = None

    def _build_config(
        self,
        *,
        schema: SchemaLike,
        use_search: bool = True,
    ) -> types.GenerateContentConfig:
        kwargs: dict[str, Any] = {
            "temperature": self._temperature,
            "max_output_tokens": self._max_output_tokens,
        }
        search_active = use_search and _model_supports_search_grounding(self._model)
        if search_active:
            # API error: "Tool use with a response mime type: 'application/json' is unsupported"
            kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
        elif _model_supports_json_mode(self._model):
            kwargs["response_mime_type"] = "application/json"
            # Cleaned dict — Pydantic class would emit ``additionalProperties``, which Gemini rejects.
            kwargs["response_json_schema"] = _schema_as_gemini_json_dict(schema)
        # else: non-Gemini model (e.g. Gemma) — no JSON mode; parse from text output
        return types.GenerateContentConfig(**kwargs)

    async def generate(self, prompt: str, schema: SchemaLike, *, use_search: bool = True) -> dict[str, Any]:
        """
        Run Gemini with optional Google Search and structured output.

        Args:
            prompt: User + instruction text.
            schema: Pydantic model class or JSON Schema dict.
            use_search: When False, skip the Google Search tool (faster smoke tests).
        """
        config = self._build_config(schema=schema, use_search=use_search)
        search_active = use_search and _model_supports_search_grounding(self._model)
        contents = prompt
        if search_active:
            contents = (
                prompt
                + "\n\nReturn a single JSON object only, matching the schema implied by the instructions "
                "above. No markdown code fences, no commentary before or after the JSON."
            )
        self.last_total_token_count = None
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            )
        except Exception as exc:
            if _is_retryable_with_fallback(exc):
                logger.warning("Gemini call failed (may retry with fallback key): %s", exc)
            raise

        um = getattr(response, "usage_metadata", None)
        if um is not None and getattr(um, "total_token_count", None) is not None:
            self.last_total_token_count = int(um.total_token_count)

        parsed = getattr(response, "parsed", None)
        if parsed is not None:
            if isinstance(parsed, BaseModel):
                return parsed.model_dump(mode="json")
            if isinstance(parsed, dict):
                return parsed

        text = getattr(response, "text", None) or ""
        if not text.strip():
            cand = None
            if getattr(response, "candidates", None):
                cand = response.candidates[0] if response.candidates else None
            finish = getattr(cand, "finish_reason", None) if cand else None
            safety = getattr(cand, "safety_ratings", None) if cand else None
            logger.warning(
                "Gemini returned empty text (model=%s, finish=%s, safety=%s)",
                self._model, finish, safety,
            )
            raise _EmptyResponseError(f"Gemini returned empty response (model={self._model}, finish={finish})")

        cand = None
        if getattr(response, "candidates", None):
            cand = response.candidates[0] if response.candidates else None
        finish = getattr(cand, "finish_reason", None) if cand else None
        if finish == types.FinishReason.MAX_TOKENS:
            logger.warning(
                "Gemini finish_reason=MAX_TOKENS (output ~%s chars); response may be truncated — "
                "raise Settings → max output tokens (legacy VRA needs ~16k+ for large JSON).",
                len(text),
            )

        try:
            return _parse_model_json(text)
        except JSONDecodeError as exc:
            around = ""
            pos = getattr(exc, "pos", None)
            if isinstance(pos, int) and pos > 0:
                lo = max(0, pos - 120)
                hi = min(len(text), pos + 120)
                around = f" | context[{lo}:{hi}]={text[lo:hi]!r}"
            logger.warning(
                "JSON parse failed (search=%s, len=%s, finish=%s)%s",
                use_search,
                len(text),
                finish,
                around,
            )
            hint = ""
            if finish == types.FinishReason.MAX_TOKENS:
                hint = (
                    " Output was cut off (MAX_TOKENS). In Settings, increase max output tokens "
                    "(try 24576 or 32768) or enable USE_HYBRID_MODE for shorter structured synthesis."
                )
            raise RuntimeError(f"Model returned non-JSON text: {exc}.{hint}") from exc

    async def test_connection(self) -> bool:
        """Minimal non-search call to verify the API key."""
        ok, _detail = await self.test_connection_detail()
        return ok

    async def test_connection_detail(self) -> tuple[bool, str]:
        """
        Verify API key and return a human-readable detail string.

        Tries several model IDs because the AI Studio API often expects versioned
        ids (e.g. ``gemini-2.0-flash-001``) while older aliases may 404.
        """
        cfg = types.GenerateContentConfig(max_output_tokens=64, temperature=0.0)
        prompt = 'Reply with exactly the word "ok" in lowercase, nothing else.'
        last_err = "Could not reach Gemini (check key, model name, and network)."
        for m in await resolve_model_candidates(self._model, self._api_key):
            try:
                response = await self._client.aio.models.generate_content(
                    model=m,
                    contents=prompt,
                    config=cfg,
                )
            except Exception as exc:
                logger.info("Gemini probe failed for model %s: %s", m, exc)
                last_err = str(exc)
                continue
            text = (getattr(response, "text", None) or "").strip().lower()
            um = getattr(response, "usage_metadata", None)
            tokens = getattr(um, "total_token_count", None) if um else None
            if "ok" in text or (text and len(text) < 120) or (tokens and int(tokens) > 0):
                return True, f"OK (model: {m})"
            blocked = getattr(response, "prompt_feedback", None)
            if blocked is not None:
                last_err = f"Blocked or empty response (model: {m}); check API key / region."
                continue
            last_err = f"Unexpected empty response (model: {m})."
        return False, last_err


# IDs that often 404 on AI Studio v1beta (retired previews, sunset aliases, etc.)
_UNSUPPORTED_MODEL_IDS = frozenset(
    {
        "gemini-2.5-flash-preview-05-20",
        # Versioned 2.5 Flash id is not offered for many AI Studio keys (404).
        "gemini-2.5-flash-001",
        # Unversioned 1.5 Flash is commonly removed from generateContent; use 2.x only.
        "gemini-1.5-flash",
        "gemini-1.5-flash-latest",
        # Standard generateContent returns 400 Interactions API only — use Interactions or pick 2.x.
        "gemini-3-pro-preview",
        "gemini-3-flash-preview",
        "gemini-3.1-pro-preview",
    }
)


def _short_model_id(full_name: Optional[str]) -> str:
    if not full_name:
        return ""
    return full_name.rsplit("/", 1)[-1]


def _model_preference_key(model_id: str) -> tuple[int, str]:
    """Lower tuple sorts earlier = try sooner."""
    u = model_id.lower()
    if any(x in u for x in ("embed", "imagen", "tts", "bison", "robotics")):
        return (80, model_id)
    if "2.0-flash" in u and "lite" not in u:
        return (0, model_id)
    if "2.5-flash" in u:
        return (2, model_id)
    if "2.0-flash-lite" in u:
        return (4, model_id)
    if "2.5-pro" in u or "2.0-pro" in u:
        return (10, model_id)
    if "1.5" in u:
        return (20, model_id)
    return (40, model_id)


async def fetch_live_generate_content_model_ids(api_key: str) -> list[str]:
    """
    Ask the API which base models support ``generateContent`` for this key.

    Avoids hard-coding model ids that 404 for some accounts or API versions.
    """
    client = genai.Client(api_key=api_key.strip())
    found: list[str] = []
    try:
        pager = await client.aio.models.list(config=types.ListModelsConfig(page_size=100))
        async for m in pager:
            acts = m.supported_actions or []
            norm = {str(a) for a in acts}
            norm_lc = {x.lower() for x in norm}
            if norm_lc and "generatecontent" not in norm_lc:
                continue
            sid = _short_model_id(m.name)
            if not sid or sid in found:
                continue
            if _skip_model_for_standard_generate_content(sid):
                logger.info("Skipping model %r for standard generateContent routing", sid)
                continue
            if sid in _UNSUPPORTED_MODEL_IDS:
                continue
            found.append(sid)
    except Exception as exc:
        logger.warning("Gemini ListModels failed: %s", exc)
        return []
    logger.info("ListModels: %d generateContent-capable base models for this key", len(found))
    return found


def _static_model_candidates(preferred: str) -> list[str]:
    """Fallback when ListModels fails or returns empty (offline, older SDK, etc.)."""
    out: list[str] = []
    # Unversioned 2.0 Flash is the most portable alias across API versions.
    for m in (
        preferred.strip(),
        "gemini-2.0-flash",
        "gemini-2.0-flash-001",
        "gemini-2.5-flash",
    ):
        if not m or m in _UNSUPPORTED_MODEL_IDS:
            continue
        if _skip_model_for_standard_generate_content(m):
            continue
        if m not in out:
            out.append(m)
    return out


async def resolve_model_candidates(preferred: str, api_key: str) -> list[str]:
    """
    Build an ordered model list: user preference (if the API lists it), then other
    ``generateContent`` models sorted by a Flash-friendly heuristic.
    """
    pref = (preferred or "").strip()
    live = await fetch_live_generate_content_model_ids(api_key)
    if not live:
        logger.warning("Gemini ListModels returned no models; using static fallback list.")
        return _static_model_candidates(pref)

    live_set = set(live)
    ordered = sorted(live, key=_model_preference_key)
    out: list[str] = []
    seen: set[str] = set()
    if (
        pref
        and pref not in _UNSUPPORTED_MODEL_IDS
        and not _skip_model_for_standard_generate_content(pref)
        and pref in live_set
    ):
        out.append(pref)
        seen.add(pref)
    elif pref and _skip_model_for_standard_generate_content(pref):
        logger.info(
            "Model %r is Interactions-only / incompatible with standard generateContent; "
            "using fallback model list.",
            pref,
        )
    elif pref and pref not in _UNSUPPORTED_MODEL_IDS and pref not in live_set:
        logger.info(
            "Model %r not returned by ListModels for this key; using API-listed models only.",
            pref,
        )
    for m in ordered:
        if m in _UNSUPPORTED_MODEL_IDS:
            continue
        if _skip_model_for_standard_generate_content(m):
            continue
        if m not in seen:
            out.append(m)
            seen.add(m)
    return out


# re-export for vendor route fallback detection
is_retryable_with_fallback = _is_retryable_with_fallback
