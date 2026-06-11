"""Confidence Scoring Framework — evidence quality, INDEPENDENT of risk.

Confidence answers "how much can we trust this assessment?", not "how risky is
the vendor?". A vendor can be HIGH risk + LOW confidence (one unverified news
report) or LOW risk + HIGH confidence (official records, clean, well sourced).

Confidence is a 0–100 score built from five additive drivers:

    Source count        (max 25)  — more independent corroboration ⇒ higher.
    Source quality      (max 25)  — Tier-1/2 coverage ⇒ higher.
    Freshness           (max 15)  — recent evidence ⇒ higher.
    Verification level  (max 20)  — share of findings that are VERIFIED FACTS.
    Entity-match        (max 15)  — entity-resolution confidence.

Band: ≥67 → HIGH, ≥34 → MEDIUM, else LOW.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.risk.fact_classification import FactType
from app.core.risk.source_credibility import SourceTier


@dataclass
class SourceFact:
    """Minimal per-finding signal needed to score confidence."""

    tier: SourceTier
    fact_type: FactType
    recency_factor: float = 0.6   # 0–1, from the adverse-media engine; neutral default


@dataclass
class ConfidenceResult:
    band: str                       # HIGH | MEDIUM | LOW
    score: int                      # 0–100
    drivers: list[str] = field(default_factory=list)
    components: dict[str, int] = field(default_factory=dict)


_ENTITY_MATCH_POINTS = {"HIGH": 15, "MEDIUM": 8, "LOW": 2}


def compute_confidence(
    facts: list[SourceFact],
    *,
    entity_confidence: str = "LOW",
) -> ConfidenceResult:
    """Compute an evidence-quality confidence band, independent of risk level."""
    drivers: list[str] = []

    distinct = facts  # caller is expected to pass de-duplicated facts
    n = len(distinct)

    # ── Source count (max 25) ────────────────────────────────────────────────
    count_pts = min(n, 5) / 5 * 25 if n else 0
    if n == 0:
        drivers.append("No usable sources retrieved")
    elif n >= 5:
        drivers.append(f"{n} independent sources")
    else:
        drivers.append(f"Only {n} source(s)")

    # ── Source quality (max 25) ──────────────────────────────────────────────
    tier1 = sum(1 for f in distinct if f.tier == SourceTier.GOVERNMENT)
    tier2 = sum(1 for f in distinct if f.tier == SourceTier.ESTABLISHED_MEDIA)
    if tier1:
        quality_pts = 25
        drivers.append(f"{tier1} Tier-1 official source(s)")
    elif tier2:
        quality_pts = 16
        drivers.append(f"{tier2} Tier-2 media source(s)")
    elif n:
        quality_pts = 6
        drivers.append("Only Tier-3/4 sources")
    else:
        quality_pts = 0

    # ── Freshness (max 15) ───────────────────────────────────────────────────
    if distinct:
        avg_recency = sum(f.recency_factor for f in distinct) / len(distinct)
        fresh_pts = avg_recency * 15
        if avg_recency >= 0.8:
            drivers.append("Evidence is recent")
        elif avg_recency <= 0.4:
            drivers.append("Evidence is largely historical")
    else:
        fresh_pts = 0

    # ── Verification level (max 20) ──────────────────────────────────────────
    verified = sum(1 for f in distinct if f.fact_type == FactType.VERIFIED_FACT)
    if distinct:
        verify_pts = verified / len(distinct) * 20
        if verified:
            drivers.append(f"{verified} verified fact(s) on official record")
        else:
            drivers.append("No findings verified on an official record")
    else:
        verify_pts = 0

    # ── Entity match (max 15) ────────────────────────────────────────────────
    em = (entity_confidence or "LOW").upper()
    entity_pts = _ENTITY_MATCH_POINTS.get(em, 2)
    drivers.append(f"Entity resolution: {em} confidence")

    components = {
        "source_count": round(count_pts),
        "source_quality": round(quality_pts),
        "freshness": round(fresh_pts),
        "verification": round(verify_pts),
        "entity_match": round(entity_pts),
    }
    score = int(min(100, sum(components.values())))
    band = "HIGH" if score >= 67 else "MEDIUM" if score >= 34 else "LOW"

    return ConfidenceResult(band=band, score=score, drivers=drivers, components=components)
