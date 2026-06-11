"""Adverse Media Engine — score articles, don't just count keyword hits.

The legacy rule was "headline mentions vendor + risk word ⇒ risk". This module
replaces that with a multi-dimensional relevance/impact model. For every
article it answers:

    • Is it actually NEGATIVE about the entity (or is the entity the protector
      / a product launch / an explainer)?               → relevance gate
    • Is the entity the PRIMARY subject?                 → relevance gate
    • How SEVERE is the alleged conduct?                 → severity dimension
    • Was it PROVEN, alleged, or resolved in their favour?→ outcome dimension
    • How CREDIBLE is the source?                        → credibility dimension
    • How RECENT is it?                                  → recency dimension
    • What is the monetary impact?                       → severity modifier

The composite (0–100) and band are fully explainable. By construction a
lower-credibility source can never on its own produce a HIGH impact, and a
resolved/cleared matter scores ~0.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from app.core.adverse_relevance import (
    adverse_text_matches_vendor,
    is_benign_protector_or_advisory,
    is_vendor_published_or_explainer,
)
from app.core.risk.litigation import LitigationOutcome, classify_litigation
from app.core.risk.source_credibility import classify_source
from app.core.timeutil import utcnow

_BAND_SEVERITY = {"HIGH": 1.0, "MEDIUM": 0.6, "LOW": 0.3, "INFO": 0.0}
_SEVERITY_BAND = [(0.85, "HIGH"), (0.45, "MEDIUM"), (0.15, "LOW")]


@dataclass
class AdverseMediaScore:
    is_adverse: bool
    primary_subject: bool
    band: str                      # HIGH | MEDIUM | LOW | INFO
    composite: int                 # 0–100
    severity: float                # 0–1
    recency_factor: float          # 0–1
    credibility: float             # 0–1 (tier weight)
    relevance: float               # 0–1
    outcome_factor: float          # 0–1
    rationale: str
    drop_reason: str | None = None
    components: dict[str, float] = field(default_factory=dict)


# Outcome → factor. A proven matter carries full weight; an allegation/probe
# is discounted; a matter resolved in the entity's favour collapses to ~0.
_OUTCOME_FACTOR = {
    LitigationOutcome.CONVICTED: 1.0,
    LitigationOutcome.PENALIZED: 0.9,
    LitigationOutcome.ADMITTED: 0.9,
    LitigationOutcome.FILED: 0.6,
    LitigationOutcome.PENDING: 0.6,
    LitigationOutcome.UNKNOWN: 0.55,
    LitigationOutcome.SETTLED: 0.3,
    LitigationOutcome.DISMISSED: 0.05,
    LitigationOutcome.ACQUITTED: 0.05,
}

_MONTH_RE = re.compile(
    r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+((?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

# Monetary scale hints — escalate large amounts, dampen small ones.
_LARGE_MONEY_RE = re.compile(r"\b(crore|billion|bn|thousand crore|lakh crore)\b", re.IGNORECASE)
_SMALL_MONEY_RE = re.compile(r"\b(lakh|thousand|million)\b", re.IGNORECASE)

# Primary-subject heuristic: the entity token appears in the first ~40 chars of
# the headline or is used possessively.
def _is_primary_subject(title: str, vendor_name: str) -> bool:
    from app.core.adverse_relevance import _significant_tokens  # local import: shared tokeniser

    toks = [t.lower() for t in _significant_tokens(vendor_name)]
    if not toks:
        return True
    head = (title or "")[:48].lower()
    if any(t in head for t in toks):
        return True
    body = (title or "").lower()
    return any(f"{t}'s" in body or f"{t}’s" in body for t in toks)


def _recency_factor_from_text(text: str, published: str | None) -> tuple[float, str]:
    """Return (factor 0–1, basis) using the freshest date we can parse."""
    now = utcnow()
    age_years: float | None = None

    for src in (published or "", text or ""):
        m = _ISO_DATE_RE.search(src)
        if m:
            try:
                d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                age_years = max(0.0, (now - d).days / 365.25)
                break
            except ValueError:
                pass
        m = _MONTH_RE.search(src)
        if m:
            try:
                d = dt.datetime.strptime(f"{m.group(1)[:3].title()} {m.group(2)}", "%b %Y")
                age_years = max(0.0, (now - d).days / 365.25)
                break
            except ValueError:
                pass

    if age_years is None:
        years = [int(y) for y in _YEAR_RE.findall(f"{published or ''} {text or ''}")]
        plausible = [y for y in years if 1990 <= y <= now.year]
        if plausible:
            age_years = float(now.year - max(plausible))

    if age_years is None:
        return 0.6, "date unknown"
    if age_years <= 0.5:
        return 1.0, "≤6 months"
    if age_years <= 2:
        return 0.85, "≤2 years"
    if age_years <= 4:
        return 0.6, "≤4 years"
    if age_years <= 7:
        return 0.4, "≤7 years"
    return 0.25, ">7 years (historical)"


def _band_for(score01: float) -> str:
    for threshold, band in _SEVERITY_BAND:
        if score01 >= threshold:
            return band
    return "LOW" if score01 > 0 else "INFO"


def score_article(
    *,
    title: str,
    snippet: str = "",
    url: str | None = None,
    published: str | None = None,
    vendor_name: str = "",
    gst: str = "",
    base_band: str | None = None,
    dimension: str | None = None,
) -> AdverseMediaScore:
    """Score one article across the five dimensions and return a composite.

    ``base_band`` is the keyword-classifier severity (HIGH/MEDIUM/LOW/INFO) from
    the existing severity brain; when omitted, the litigation classifier alone
    supplies the base. The engine then modulates it by outcome, source
    credibility, recency, and monetary scale, gated by relevance.
    """
    text = f"{title} {snippet}".strip()
    lit = classify_litigation(text)

    # ── Relevance gate ───────────────────────────────────────────────────────
    if vendor_name and not adverse_text_matches_vendor("", text, vendor_name=vendor_name, gst=gst):
        return AdverseMediaScore(False, False, "INFO", 0, 0, 0, 0, 0, 0,
                                 "Article does not refer to this entity.",
                                 drop_reason="not about this entity")
    if is_benign_protector_or_advisory(text):
        return AdverseMediaScore(False, False, "INFO", 0, 0, 0, 0, 0, 0,
                                 "Entity is the protector / advisory subject, not the wrongdoer.",
                                 drop_reason="vendor-as-protector / non-adverse")
    if is_vendor_published_or_explainer(title, url or "", vendor_name):
        return AdverseMediaScore(False, False, "INFO", 0, 0, 0, 0, 0, 0,
                                 "Entity-published or explainer content, not reporting of wrongdoing.",
                                 drop_reason="vendor-published / explainer")

    primary = _is_primary_subject(title, vendor_name)

    # ── Severity dimension ───────────────────────────────────────────────────
    base = (base_band or lit.risk_band or "INFO").upper()
    # The litigation classifier can ESCALATE (it understands fraud/criminal) but
    # we keep the higher of the two so a fraud headline isn't softened.
    if _BAND_SEVERITY.get(lit.risk_band, 0) > _BAND_SEVERITY.get(base, 0):
        base = lit.risk_band
    severity = _BAND_SEVERITY.get(base, 0.0)
    if severity == 0.0:
        return AdverseMediaScore(False, primary, "INFO", 0, severity, 0, 0, 0, 0,
                                 "No adverse signal in the article (routine / positive news).",
                                 drop_reason="no risk signal")

    # ── Outcome dimension ────────────────────────────────────────────────────
    outcome_factor = _OUTCOME_FACTOR.get(lit.outcome, 0.55)

    # Monetary scale modifier (the "monetary impact" axis).
    money_note = ""
    if _LARGE_MONEY_RE.search(text):
        severity = min(1.0, severity + 0.15)
        money_note = "large monetary scale (+)"
    elif _SMALL_MONEY_RE.search(text) and base in ("MEDIUM", "LOW"):
        severity = max(0.2, severity - 0.1)
        money_note = "small monetary scale (–)"

    # ── Credibility dimension ────────────────────────────────────────────────
    cred = classify_source(url, dimension=dimension)
    credibility = cred.weight

    # ── Recency dimension ────────────────────────────────────────────────────
    recency_factor, recency_basis = _recency_factor_from_text(text, published)

    # ── Relevance dimension (primary subject sharpens, secondary dampens) ─────
    relevance = 1.0 if primary else 0.6

    # ── Composite ────────────────────────────────────────────────────────────
    impact01 = severity * outcome_factor * credibility * relevance
    impact01 *= (0.7 + 0.3 * recency_factor)   # recency modulates ±30%
    composite = max(0, min(100, round(impact01 * 100)))
    band = _band_for(impact01)

    rationale = (
        f"severity={base}({severity:.2f}) × outcome={lit.outcome.value}({outcome_factor:.2f}) × "
        f"credibility={cred.label.split(' — ')[0]}({credibility:.2f}) × "
        f"relevance={'primary' if primary else 'secondary'}({relevance:.2f}), "
        f"recency={recency_basis}({recency_factor:.2f})"
        + (f", {money_note}" if money_note else "")
        + f" ⇒ impact {composite}/100 ({band})."
    )

    return AdverseMediaScore(
        is_adverse=composite > 0,
        primary_subject=primary,
        band=band,
        composite=composite,
        severity=severity,
        recency_factor=recency_factor,
        credibility=credibility,
        relevance=relevance,
        outcome_factor=outcome_factor,
        rationale=rationale,
        components={
            "severity": round(severity, 3),
            "outcome": round(outcome_factor, 3),
            "credibility": round(credibility, 3),
            "relevance": round(relevance, 3),
            "recency": round(recency_factor, 3),
        },
    )
