"""Classify each finding as VERIFIED FACT, MEDIA REFERENCE, or INFERENCE.

A defensible report must never blur the line between what is *proven on an
official record*, what is *reported by the press*, and what the model merely
*inferred*. This module assigns one of three provenance classes to a finding
based solely on the credibility of its citation and the wording of the finding
— never on the entity involved.

    VERIFIED_FACT   — substantiated by a Tier-1 official source (regulator,
                      court, registry, sanctions list, rating agency rationale).
    MEDIA_REFERENCE — reported by a Tier-2/3 news/industry source.
    INFERENCE       — model-derived, placeholder/search citation, unverified
                      (Tier-4) source, or explicitly flagged "verify manually".

Only VERIFIED_FACT evidence may, on its own, justify a veto-class conclusion
(see ``recommendation``). MEDIA_REFERENCE escalates to "investigate"; INFERENCE
can never raise risk on its own.
"""

from __future__ import annotations

from enum import Enum

from app.core.risk.source_credibility import SourceTier, classify_source


class FactType(str, Enum):
    VERIFIED_FACT = "VERIFIED_FACT"
    MEDIA_REFERENCE = "MEDIA_REFERENCE"
    INFERENCE = "INFERENCE"
    # A dimension the automated collectors could NOT retrieve a record for
    # (search returned nothing, registry API unavailable, out of scope). This is
    # an *absence of evidence*, never proof of a clean record — and crucially it
    # must NOT be stamped as a VERIFIED FACT just because a placeholder citation
    # (e.g. a generic registry URL) is attached to it.
    NOT_ASSESSED = "NOT_ASSESSED"


# Collector-boilerplate wording that means "we did not retrieve a record here".
# These phrases appear ONLY on the deterministic fallback placeholders (see
# app.core.hybrid_report / validator) — never on a real registry/media finding —
# so matching them is safe and keeps "nothing retrieved" out of VERIFIED FACT.
_NOT_ASSESSED_MARKERS = (
    "found via web search",
    "surfaced via web search",
    "no adverse media retained",
    "no adverse headlines returned",
    "out of scope for automated collectors",
    "scrape / api not available",
    "no mca master data retrieved",
    "no master data retrieved",
    "gst public api returned no usable fields",
    "profile is based on vendor name",
    "require mca21 or vendor disclosure",
    "director due-diligence is manual",
    "manual for this run",
)

# Wording that marks a finding as not independently verifiable, regardless of
# the (often placeholder) URL attached to it.
_INFERENCE_MARKERS = (
    "verify manually",
    "source not retrieved",
    "no public record",
    "no adverse record",
    "not verified from osint",
    "manual verification",
    "could not find",
    "based on training",
    "name overlap only",
    "possible name overlap",
)

# Search-pointer URLs are navigation aids, not evidence of anything.
_SEARCH_POINTERS = (
    "google.com/search",
    "news.google.com",
    "duckduckgo.com",
    "bing.com/search",
)


def _is_search_pointer(url: str) -> bool:
    u = (url or "").lower()
    return any(p in u for p in _SEARCH_POINTERS)


def classify_fact(
    text: str,
    source_url: str | None,
    *,
    dimension: str | None = None,
) -> FactType:
    """Return the provenance class for a finding.

    Resolution order (most conservative wins):
      1. Collector "nothing retrieved" boilerplate → NOT_ASSESSED (never a
         verified fact, regardless of any placeholder citation attached).
      2. Explicit "verify manually / no record / inference" wording → INFERENCE.
      3. A bare search-pointer URL → INFERENCE (it proves nothing on its own).
      4. Tier-1 official source → VERIFIED_FACT.
      5. Tier-2/3 source → MEDIA_REFERENCE.
      6. Anything else (Tier-4, missing, unknown) → INFERENCE.
    """
    t = (text or "").lower()
    if any(m in t for m in _NOT_ASSESSED_MARKERS):
        return FactType.NOT_ASSESSED
    if any(m in t for m in _INFERENCE_MARKERS):
        return FactType.INFERENCE

    url = (source_url or "").strip()
    if not url or _is_search_pointer(url):
        return FactType.INFERENCE

    cred = classify_source(url, dimension=dimension)
    if cred.tier == SourceTier.GOVERNMENT:
        return FactType.VERIFIED_FACT
    if cred.tier in (SourceTier.ESTABLISHED_MEDIA, SourceTier.INDUSTRY_COMMENTARY):
        return FactType.MEDIA_REFERENCE
    return FactType.INFERENCE


def is_verifiable(fact_type: FactType | str) -> bool:
    """True only for VERIFIED_FACT — the gate for veto-class escalation."""
    if isinstance(fact_type, FactType):
        return fact_type == FactType.VERIFIED_FACT
    return str(fact_type).upper() == FactType.VERIFIED_FACT.value
