"""Litigation Intelligence — classify litigation by nature, not by existence.

The legacy engine treated *any* litigation keyword as risk, so a routine
commercial suit scored the same as a fraud prosecution. This module instead
asks the questions a litigation analyst would:

    • What is the NATURE of the matter? (fraud/criminal vs commercial dispute)
    • What is the OUTCOME / current STATUS? (convicted vs dismissed vs pending)
    • Is the entity the DEFENDANT or the one who filed it?
    • How RECENT is it?

and produces an explainable risk band. The classification is generalized:
it keys off legal subject-matter and outcome language only, never the entity.

Risk-band contract (mirrors the requested taxonomy):

  HIGH    — fraud, money laundering, bribery, corruption, criminal
            prosecution, insolvency *against* the entity, regulatory bans,
            sanctions violations.
  MEDIUM  — major regulatory investigations, significant tax disputes, large
            penalties, competition-law investigations.
  LOW     — commercial / contractual / service disputes, appeals,
            industry-wide litigation.
  INFO    — resolved in the entity's favour, or the entity is the complainant,
            or no litigation present.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

from app.core.timeutil import utcnow


class LitigationNature(str, Enum):
    FRAUD_CRIMINAL = "fraud / criminal"
    INSOLVENCY_AGAINST = "insolvency against entity"
    REGULATORY_BAN = "regulatory ban / debarment"
    SANCTIONS = "sanctions violation"
    REGULATORY_INVESTIGATION = "regulatory investigation"
    TAX_DISPUTE = "tax dispute"
    COMPETITION = "competition law"
    PENALTY = "monetary penalty"
    COMMERCIAL = "commercial / contractual dispute"
    SERVICE = "service / consumer dispute"
    APPEAL = "appeal"
    UNKNOWN = "general litigation"


class LitigationOutcome(str, Enum):
    CONVICTED = "convicted / guilty"
    PENALIZED = "penalty / order against"
    ADMITTED = "admitted / initiated"
    PENDING = "pending"
    FILED = "filed"
    SETTLED = "settled / withdrawn"
    DISMISSED = "dismissed / quashed"
    ACQUITTED = "acquitted / cleared"
    UNKNOWN = "status unknown"


@dataclass
class LitigationAssessment:
    risk_band: str                       # HIGH | MEDIUM | LOW | INFO
    nature: LitigationNature
    outcome: LitigationOutcome
    rationale: str
    entity_is_complainant: bool = False
    recency_years: float | None = None
    adjustments: list[str] = field(default_factory=list)


# ── Nature keyword sets (checked HIGH→LOW; first match wins) ─────────────────
_NATURE_HIGH = {
    LitigationNature.FRAUD_CRIMINAL: (
        "fraud", "fraudulent", "cheating", "forgery", "embezzle", "siphon",
        "money laundering", "pmla", "bribery", "corruption", "criminal breach",
        "criminal conspiracy", "criminal prosecution", "criminal case",
        "chargesheet", "charge sheet", "fir", "cbi", "enforcement directorate",
        "ed probe", "ed case", "sfio", "disproportionate assets", "bank fraud",
        "loan fraud", "ponzi", "misappropriat", "benami", "hawala",
    ),
    LitigationNature.SANCTIONS: (
        "sanctions violation", "ofac", "sdn list", "sanctions breach",
        "export control violation", "terror financing",
    ),
    LitigationNature.INSOLVENCY_AGAINST: (
        "insolvency petition against", "cirp", "corporate insolvency",
        "liquidation order", "winding up petition", "winding-up petition",
        "section 7", "section 9", "ibc petition", "nclt admitted",
    ),
    LitigationNature.REGULATORY_BAN: (
        "debarred", "debarment", "banned", "license cancelled",
        "licence cancelled", "license revoked", "registration cancelled",
        "cease and desist", "prohibited from accessing",
    ),
}

_NATURE_MEDIUM = {
    LitigationNature.REGULATORY_INVESTIGATION: (
        "sebi investigation", "rbi investigation", "regulatory investigation",
        "show cause", "show-cause", "summons", "probe into", "under investigation",
        "adjudication proceedings",
    ),
    LitigationNature.TAX_DISPUTE: (
        "tax dispute", "tax demand", "gst demand", "income tax notice",
        "tax evasion", "service tax demand", "customs duty demand",
        "transfer pricing",
    ),
    LitigationNature.COMPETITION: (
        "cci", "competition commission", "antitrust", "anti-competitive",
        "abuse of dominance", "cartel",
    ),
    LitigationNature.PENALTY: (
        "penalty of", "fined", "fine of", "monetary penalty", "imposed a penalty",
    ),
}

_NATURE_LOW = {
    LitigationNature.COMMERCIAL: (
        "commercial dispute", "contractual dispute", "breach of contract",
        "recovery suit", "money suit", "arbitration", "payment dispute",
        "supply dispute", "trademark dispute", "ip dispute",
    ),
    LitigationNature.SERVICE: (
        "consumer complaint", "consumer forum", "consumer court",
        "service dispute", "deficiency in service", "labour dispute",
        "employment dispute", "defamation",
    ),
    LitigationNature.APPEAL: (
        "appeal", "writ petition", "stay order", "interim order",
        "special leave petition",
    ),
}

# Outcome language → outcome enum (checked in this order; later, stronger
# signals can override earlier ones via priority weighting below).
_OUTCOME_RULES: tuple[tuple[LitigationOutcome, tuple[str, ...]], ...] = (
    (LitigationOutcome.CONVICTED, ("convicted", "found guilty", "conviction", "sentenced")),
    (LitigationOutcome.ACQUITTED, ("acquitted", "exonerated", "cleared of", "given clean chit",
                                   "clean chit", "discharged of all charges")),
    (LitigationOutcome.DISMISSED, ("dismissed", "quashed", "set aside", "rejected by the court",
                                   "petition dismissed", "case closed", "no case made out")),
    (LitigationOutcome.SETTLED, ("settled", "withdrawn", "consent terms", "amicably resolved",
                                 "out-of-court settlement", "compounded")),
    (LitigationOutcome.PENALIZED, ("penalty", "fined", "order against", "directed to pay",
                                   "held liable", "upheld the penalty")),
    (LitigationOutcome.ADMITTED, ("admitted", "cirp initiated", "moratorium",
                                  "resolution professional appointed")),
    (LitigationOutcome.FILED, ("filed against", "petition filed", "case filed", "suit filed",
                               "fir registered", "fir lodged", "complaint lodged")),
    (LitigationOutcome.PENDING, ("pending", "next hearing", "sub judice", "ongoing", "hearing on")),
)

# The entity is the one bringing the action (so the matter is not adverse to it).
_COMPLAINANT_PATTERNS = (
    r"\bmoves? (?:the )?court\b",
    r"\bfiles? (?:a )?(?:suit|case|complaint|petition|fir)\b",
    r"\bapproaches? (?:the )?(?:court|tribunal|nclt|high court)\b",
    r"\bseeks? (?:damages|injunction|relief)\b",
    r"\bsues\b",
    r"\bdrags .* to court\b",
)

_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _detect_recency_years(text: str) -> float | None:
    """Estimate years since the most recent 4-digit year mentioned, if any."""
    years = [int(m.group(0)) for m in _YEAR_RE.finditer(text or "")]
    if not years:
        return None
    now = utcnow().year
    plausible = [y for y in years if 1990 <= y <= now]
    if not plausible:
        return None
    return float(now - max(plausible))


def _first_nature(text: str, table: dict[LitigationNature, tuple[str, ...]]) -> LitigationNature | None:
    for nature, markers in table.items():
        if any(m in text for m in markers):
            return nature
    return None


def _detect_outcome(text: str) -> LitigationOutcome:
    for outcome, markers in _OUTCOME_RULES:
        if any(m in text for m in markers):
            return outcome
    return LitigationOutcome.UNKNOWN


def _entity_is_complainant(text: str) -> bool:
    return any(re.search(p, text) for p in _COMPLAINANT_PATTERNS)


# Base band per nature.
_NATURE_BAND: dict[LitigationNature, str] = {
    LitigationNature.FRAUD_CRIMINAL: "HIGH",
    LitigationNature.SANCTIONS: "HIGH",
    LitigationNature.INSOLVENCY_AGAINST: "HIGH",
    LitigationNature.REGULATORY_BAN: "HIGH",
    LitigationNature.REGULATORY_INVESTIGATION: "MEDIUM",
    LitigationNature.TAX_DISPUTE: "MEDIUM",
    LitigationNature.COMPETITION: "MEDIUM",
    LitigationNature.PENALTY: "MEDIUM",
    LitigationNature.COMMERCIAL: "LOW",
    LitigationNature.SERVICE: "LOW",
    LitigationNature.APPEAL: "LOW",
    LitigationNature.UNKNOWN: "LOW",
}

_BAND_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
_ORDER_BAND = {v: k for k, v in _BAND_ORDER.items()}


def _bump(band: str, delta: int) -> str:
    return _ORDER_BAND[max(0, min(3, _BAND_ORDER[band] + delta))]


def classify_litigation(text: str) -> LitigationAssessment:
    """Classify a litigation snippet into an explainable risk band.

    The band is the base band for the matter's NATURE, then adjusted for
    outcome (favourable outcome lowers it; conviction/penalty raises it),
    the entity's role (complainant ⇒ not adverse), and recency (stale matters
    are dampened).
    """
    t = (text or "").lower().strip()
    if not t:
        return LitigationAssessment(
            "INFO", LitigationNature.UNKNOWN, LitigationOutcome.UNKNOWN,
            "No litigation text supplied.",
        )

    nature = (
        _first_nature(t, _NATURE_HIGH)
        or _first_nature(t, _NATURE_MEDIUM)
        or _first_nature(t, _NATURE_LOW)
        or LitigationNature.UNKNOWN
    )
    outcome = _detect_outcome(t)
    complainant = _entity_is_complainant(t)
    recency = _detect_recency_years(t)

    band = _NATURE_BAND[nature]
    adjustments: list[str] = []

    # Outcome adjustments.
    if outcome in (LitigationOutcome.DISMISSED, LitigationOutcome.ACQUITTED):
        band = "INFO"
        adjustments.append(f"Resolved favourably ({outcome.value}) → reduced to INFO")
    elif outcome == LitigationOutcome.SETTLED:
        band = _bump(band, -1)
        adjustments.append("Settled / withdrawn → reduced one band")
    elif outcome == LitigationOutcome.CONVICTED:
        band = "HIGH"
        adjustments.append("Conviction recorded → escalated to HIGH")
    elif outcome == LitigationOutcome.PENALIZED and band == "LOW":
        # A low-band matter (e.g. a commercial dispute) where a binding order
        # went against the entity escalates to MEDIUM. A matter whose nature is
        # already MEDIUM (a regulatory penalty) is NOT double-counted to HIGH —
        # only fraud/criminal/insolvency/bans/conviction reach HIGH.
        band = _bump(band, +1)
        adjustments.append("Order against entity in a low-band matter → escalated to MEDIUM")

    # Role: if the entity itself initiated the action, it is not adverse to it.
    if complainant and outcome not in (LitigationOutcome.CONVICTED, LitigationOutcome.PENALIZED):
        band = "INFO"
        adjustments.append("Entity is the complainant / petitioner → not adverse")

    # Recency dampening: matters with a most-recent reference > 5 years old, and
    # no active/adverse outcome, are softened by one band.
    if (
        recency is not None
        and recency > 5
        and outcome in (LitigationOutcome.UNKNOWN, LitigationOutcome.PENDING,
                        LitigationOutcome.FILED, LitigationOutcome.SETTLED)
        and band in ("MEDIUM", "LOW")
    ):
        band = _bump(band, -1)
        adjustments.append(f"Stale matter (~{recency:.0f}y old, unresolved) → dampened one band")

    rationale = (
        f"Nature: {nature.value}; outcome: {outcome.value}; "
        f"role: {'complainant' if complainant else 'respondent/defendant'}; "
        f"recency: {('~%.0f y' % recency) if recency is not None else 'unknown'}. "
        f"Risk band {band}."
    )
    return LitigationAssessment(
        risk_band=band,
        nature=nature,
        outcome=outcome,
        rationale=rationale,
        entity_is_complainant=complainant,
        recency_years=recency,
        adjustments=adjustments,
    )
