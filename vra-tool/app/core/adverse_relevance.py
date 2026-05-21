"""Filter adverse-media rows that are not about the assessed vendor (noisy news / homonyms)."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

# Generic org suffixes — not distinctive for matching
_STOP = frozenset(
    {
        "PRIVATE",
        "LIMITED",
        "PARTNERSHIP",
        "LLP",
        "LTD",
        "INDIA",
        "COMPANY",
        "THE",
        "AND",
        "ENTERPRISES",
        "SERVICES",
        "GROUP",
        "HOLDINGS",
        "GLOBAL",
        "SOLUTIONS",
        "TECHNOLOGIES",
        "INDUSTRIES",
        "INFRA",
        "INFRASTRUCTURE",
        "DEVELOPERS",
        "REALTY",
        "ESTATES",
        "MEDIA",
        "POWER",
        "ENERGY",
        "CORP",
        "CORPORATION",
    }
)


def _significant_tokens(name: str) -> list[str]:
    parts: list[str] = []
    for w in re.split(r"[^\w]+", (name or "").upper()):
        if len(w) >= 4 and w not in _STOP:
            parts.append(w)
    return parts


def _pan_from_gstin(gst: str) -> str:
    """Embedded PAN inside a 15-char GSTIN (positions 3–12)."""
    g = (gst or "").strip().upper()
    if len(g) != 15:
        return ""
    return g[2:12]


def adverse_text_matches_vendor(
    entity: str,
    summary: str,
    *,
    vendor_name: str,
    gst: str = "",
) -> bool:
    """
    Return True if entity + summary plausibly refer to ``vendor_name``.

    Stricter than a plain substring search:
    - Prefer **full GSTIN** or embedded **PAN** in the text (avoids weak partial matches).
    - Require **more token overlap** when the legal name has several distinctive tokens.
    - Higher fuzzy thresholds for single-token names to cut homonyms.
    """
    vn = (vendor_name or "").strip()
    if not vn:
        return True
    blob = f"{entity} {summary}".upper()
    g = (gst or "").strip().upper()

    if len(g) == 15 and g in blob:
        return True
    pan = _pan_from_gstin(g)
    if len(pan) == 10 and pan in blob:
        return True

    toks = _significant_tokens(vn)
    blob_compact = re.sub(r"\s+", " ", f"{entity} {summary}").strip()
    vn_upper = vn.upper()

    # ``partial_ratio`` tolerates extra words in the headline better than ``token_sort_ratio``,
    # while still staying low for homonym paragraphs that only share one short substring.
    if len(toks) >= 3:
        hits = sum(1 for t in toks if t in blob)
        if hits < 3:
            return False
        return fuzz.partial_ratio(vn_upper, blob_compact.upper()) >= 72

    if len(toks) == 2:
        hits = sum(1 for t in toks if t in blob)
        if hits < 2:
            return False
        return fuzz.partial_ratio(vn_upper, blob_compact.upper()) >= 66

    if len(toks) == 1:
        return toks[0] in blob and fuzz.token_sort_ratio(vn_upper, blob_compact) >= 78

    return fuzz.token_set_ratio(vn_upper, blob_compact) >= 82
