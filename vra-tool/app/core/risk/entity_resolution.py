"""Entity Resolution Layer — resolve an input string to ONE legal entity.

This is the highest-priority gate: before any risk is scored, the engine must
decide *which* legal person it is assessing and how confident it is, so that
findings about a different entity that merely shares a name token are never
attributed to the vendor.

Given the input name (+ optional GSTIN / declared org type) and whatever
identity evidence the collectors surfaced (GST legal/trade name, MCA master
data, and candidate legal names mined from search results), this module:

  • extracts candidate legal entities (``… Limited / LLP / Trust / plc …``),
  • selects the most likely one,
  • records a match CONFIDENCE (HIGH/MEDIUM/LOW) and a human-readable RATIONALE,
  • flags AMBIGUITY when several distinct strong candidates exist
    (e.g. "Bharti Airtel Limited" vs "Airtel Payments Bank Limited"),
  • exposes ``is_linked_to_entity`` so downstream findings can be rejected when
    they cannot be confidently tied to the resolved entity.

No entity-specific logic — it works purely off name structure and identifiers.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from app.core.adverse_relevance import _significant_tokens, adverse_text_matches_vendor

# Legal-form suffixes used to recognise a candidate *legal* entity name. Ordered
# longest-first so "Private Limited" wins over "Limited".
_LEGAL_FORMS = (
    "private limited",
    "public limited",
    "limited liability partnership",
    "payments bank limited",
    "small finance bank limited",
    "pvt ltd",
    "pvt. ltd.",
    "llp",
    "limited",
    "ltd",
    "plc",
    "inc",
    "corporation",
    "foundation",
    "trust",
    "society",
    "association",
)

# Build a regex that captures up to ~6 capitalised words preceding a legal form.
_NAME_WORD = r"[A-Z][A-Za-z&.\-]*"
_LEGAL_ALT = "|".join(re.escape(f) for f in _LEGAL_FORMS)
_CANDIDATE_RE = re.compile(
    rf"((?:{_NAME_WORD}\s+){{0,6}}(?:{_LEGAL_ALT}))\b",
    re.IGNORECASE,
)

_GSTIN_RE = re.compile(r"\b[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_CIN_RE = re.compile(r"\b[ULul]\d{5}[A-Za-z]{2}\d{4}[A-Za-z]{3}\d{6}\b")


@dataclass
class EntityCandidate:
    legal_name: str
    support: int               # how many sources mentioned it
    token_overlap: float       # 0–1 overlap with the input name tokens
    score: float               # ranking score

    def as_dict(self) -> dict:
        return {
            "legal_name": self.legal_name,
            "support": self.support,
            "token_overlap": round(self.token_overlap, 2),
            "score": round(self.score, 2),
        }


@dataclass
class EntityResolution:
    input_name: str
    resolved_name: str
    confidence: str                       # HIGH | MEDIUM | LOW
    rationale: str
    gstin: str = ""
    cin: str = ""
    org_type: str = ""
    ambiguous: bool = False
    candidates: list[EntityCandidate] = field(default_factory=list)
    identifiers_verified: bool = False

    def as_dict(self) -> dict:
        return {
            "input_name": self.input_name,
            "resolved_name": self.resolved_name,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "gstin": self.gstin,
            "cin": self.cin,
            "org_type": self.org_type,
            "ambiguous": self.ambiguous,
            "identifiers_verified": self.identifiers_verified,
            "candidates": [c.as_dict() for c in self.candidates[:6]],
        }


_GST_RE_FULL = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


def _normalize_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip()).strip(" .,-")


def _token_overlap(candidate: str, input_tokens: list[str]) -> float:
    if not input_tokens:
        return 0.0
    cand_tokens = {t.lower() for t in _significant_tokens(candidate)}
    if not cand_tokens:
        return 0.0
    hits = sum(1 for t in input_tokens if t.lower() in cand_tokens)
    return hits / len(input_tokens)


def _extract_candidates(blobs: list[str], input_tokens: list[str]) -> list[EntityCandidate]:
    """Mine candidate legal-entity names from text blobs and rank them."""
    raw_counter: Counter[str] = Counter()
    for blob in blobs:
        for m in _CANDIDATE_RE.finditer(blob or ""):
            name = _normalize_name(m.group(1))
            # Discard fragments that are only a legal form ("Limited") or too short.
            stripped = name.lower()
            if len(name) < 5 or stripped in _LEGAL_FORMS:
                continue
            raw_counter[name] += 1

    # Collapse case/spacing variants by a normalised key, keep the most common surface form.
    by_key: dict[str, Counter[str]] = {}
    for surface, count in raw_counter.items():
        key = surface.lower()
        by_key.setdefault(key, Counter())[surface] += count

    candidates: list[EntityCandidate] = []
    for key, surfaces in by_key.items():
        surface = surfaces.most_common(1)[0][0]
        support = sum(surfaces.values())
        overlap = _token_overlap(surface, input_tokens)
        # A candidate must share at least one distinctive token with the input,
        # otherwise it's a co-mentioned but unrelated company.
        if overlap <= 0:
            continue
        score = support * 1.0 + overlap * 3.0
        candidates.append(EntityCandidate(surface, support, overlap, score))

    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


def resolve_entity(
    *,
    input_name: str,
    gst: str = "",
    org_type: str = "",
    gst_legal_name: str = "",
    gst_trade_name: str = "",
    mca_name: str = "",
    cin: str = "",
    evidence_texts: list[str] | None = None,
) -> EntityResolution:
    """Resolve ``input_name`` to a single legal entity with a confidence band.

    Confidence ladder (evidence-quality, not risk):
      HIGH   — a valid GSTIN/CIN anchors the identity AND an official legal name
               is available, with no competing strong candidate.
      MEDIUM — a single dominant legal-name candidate matches the input across
               ≥2 sources, but no verified identifier.
      LOW    — only name overlap, sparse evidence, or several competing
               candidates (ambiguous) such that the exact legal person is unsure.
    """
    input_name = _normalize_name(input_name)
    gstin = (gst or "").strip().upper()
    gstin_valid = bool(_GST_RE_FULL.match(gstin))
    input_tokens = [t.lower() for t in _significant_tokens(input_name)]

    blobs = list(evidence_texts or [])
    candidates = _extract_candidates(blobs, input_tokens)

    # Official-name anchor (GST legal name > MCA name > GST trade name).
    official_name = _normalize_name(gst_legal_name or mca_name or gst_trade_name or "")
    identifiers_verified = bool(gstin_valid or cin)

    # Detect ambiguity: ≥2 distinct candidates with comparable, high support.
    # Distinctness is measured on the DISTINCTIVE tokens only (legal forms like
    # "Limited" are excluded), so "Bharti Airtel Limited" vs "Airtel Payments
    # Bank Limited" reads as two different legal persons, not a near-duplicate.
    ambiguous = False
    if len(candidates) >= 2:
        top, second = candidates[0], candidates[1]
        t1 = {t.lower() for t in _significant_tokens(top.legal_name)}
        t2 = {t.lower() for t in _significant_tokens(second.legal_name)}
        union = t1 | t2
        jaccard = (len(t1 & t2) / len(union)) if union else 1.0
        distinct = jaccard < 0.6
        if distinct and second.score >= max(2.0, top.score * 0.6):
            ambiguous = True

    # ── Resolve the name ─────────────────────────────────────────────────────
    if official_name:
        resolved = official_name
    elif candidates:
        resolved = candidates[0].legal_name
    else:
        resolved = input_name

    # ── Confidence ───────────────────────────────────────────────────────────
    reasons: list[str] = []
    if identifiers_verified and official_name:
        confidence = "HIGH"
        anchor = f"GSTIN {gstin}" if gstin_valid else f"CIN {cin}"
        reasons.append(f"Identity anchored by verified {anchor} and official legal name '{official_name}'.")
    elif identifiers_verified:
        confidence = "MEDIUM"
        reasons.append(
            f"Verified identifier present ({'GSTIN' if gstin_valid else 'CIN'}) but no official "
            "legal name retrieved; identity probable, name unconfirmed."
        )
    elif candidates and candidates[0].token_overlap >= 0.6 and candidates[0].support >= 2 and not ambiguous:
        confidence = "MEDIUM"
        reasons.append(
            f"Single dominant legal-name candidate '{resolved}' matched across "
            f"{candidates[0].support} sources, but no GSTIN/CIN verified."
        )
    else:
        confidence = "LOW"
        if not candidates:
            reasons.append("No legal-entity name could be extracted from open sources; name-only match.")
        elif ambiguous:
            reasons.append(
                f"Multiple distinct entities share the name token "
                f"(e.g. '{candidates[0].legal_name}' vs '{candidates[1].legal_name}'); "
                "the exact legal person is ambiguous."
            )
        else:
            reasons.append("Sparse public footprint and no verified identifier; identity not established.")

    if ambiguous and confidence == "HIGH":
        # An identifier removes ambiguity even if homonyms exist in the news.
        reasons.append("Verified identifier disambiguates the homonyms found in news.")
    elif ambiguous:
        confidence = "LOW"

    rationale = " ".join(reasons)

    return EntityResolution(
        input_name=input_name,
        resolved_name=resolved,
        confidence=confidence,
        rationale=rationale,
        gstin=gstin if gstin_valid else "",
        cin=cin or "",
        org_type=org_type or "",
        ambiguous=ambiguous,
        candidates=candidates,
        identifiers_verified=identifiers_verified,
    )


def is_linked_to_entity(
    text: str,
    resolution: EntityResolution,
    *,
    extra_source: str = "",
) -> bool:
    """True if a finding text can be confidently tied to the resolved entity.

    Uses the resolved legal name (more specific than the raw input) and the
    GSTIN/embedded-PAN match logic. When resolution confidence is LOW and no
    identifier exists, the bar is the same fuzzy match but the caller should
    treat the link as provisional (the report flags this).
    """
    name = resolution.resolved_name or resolution.input_name
    if adverse_text_matches_vendor("", f"{text} {extra_source}", vendor_name=name, gst=resolution.gstin):
        return True
    # Fall back to the original input name (the resolved name may be longer).
    if resolution.input_name and resolution.input_name != name:
        return adverse_text_matches_vendor(
            "", f"{text} {extra_source}", vendor_name=resolution.input_name, gst=resolution.gstin
        )
    return False
