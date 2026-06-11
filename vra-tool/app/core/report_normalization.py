"""Coerce non-conforming LLM JSON into ``VRAReport``-compatible dicts (legacy / search path)."""

from __future__ import annotations

import logging
from typing import Any

from app.core.risk.confidence import SourceFact, compute_confidence
from app.core.risk.fact_classification import FactType, classify_fact
from app.core.risk.recommendation import RECOMMENDATION_LABEL, recommend
from app.core.risk.source_credibility import SourceTier, classify_source

logger = logging.getLogger(__name__)

_VRA_LIST_KEYS = (
    "company_profile",
    "management",
    "credit_ratings",
    "financial_soundness",
    "borrowings",
    "funds_raised",
    "mca_filings",
    "defaults",
    "litigations",
    "statutory_compliance",
    "adverse_media",
    "fraud_aml",
    "connected_entities",
)

# Maps each report section to the 12-dimension rubric key. Some sections map 1:1;
# others (adverse_media, fraud_aml, management) are folded into the broader dimension.
_SECTION_TO_DIMENSION: dict[str, str] = {
    "company_profile":      "company_profile",
    "management":           "management_integrity",
    "credit_ratings":       "credit_ratings",
    "financial_soundness":  "financial_soundness",
    "borrowings":           "borrowings",
    "funds_raised":         "funds_raised",
    "mca_filings":          "mca_filings",
    "defaults":             "defaults",
    "litigations":          "litigations",
    "statutory_compliance": "statutory_compliance",
    "adverse_media":        "adverse_media",
    "fraud_aml":            "sanctions_aml_fraud",
}

# Weight (%) used in Step 2 of the rubric — must match the prompt instructions.
_DIMENSION_WEIGHTS: dict[str, int] = {
    "defaults":              15,
    "sanctions_aml_fraud":   15,
    "litigations":           10,
    "statutory_compliance":  10,
    "credit_ratings":         8,
    "adverse_media":         10,
    "borrowings":             7,
    "mca_filings":            5,
    "management_integrity":  10,
    "financial_soundness":    5,
    "funds_raised":           3,
    "company_profile":        2,
}
_ALL_DIMENSIONS: tuple[str, ...] = tuple(_DIMENSION_WEIGHTS.keys())

# Map severity tokens → 0/25/50/75/100 dimension score band.
_SEVERITY_TO_SCORE: dict[str, int] = {
    "HIGH":    75,
    "MEDIUM":  50,
    "LOW":     25,
    "INFO":     0,
    "NONE":     0,
    "":         0,
}

# Ordinal ranking of the three risk ratings, used to reconcile the
# mechanically-derived rating with the LLM's own rating (see
# _ensure_calibrated_rubric).
_RATING_ORDER: dict[str, int] = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def _score_for_finding_text(text: str) -> int:
    """Bump score to 100 when the finding clearly cites a veto-class event."""
    t = (text or "").lower()
    veto_markers = (
        "wilful default", "willful default",
        "sanction", "ofac", "un consolidated",
        "ed chargesheet", "pmla", "sfio", "cbi chargesheet",
        "sebi debarment", "debarred",
        "cirp", "insolvency admitted", "liquidation order",
        "gst cancelled", "gstin cancelled", "fake invoic",
        "convicted", "conviction",
        "struck off", "struck-off", "disqualified director",
        "uapa", "fatf black",
    )
    return 100 if any(m in t for m in veto_markers) else 0


def _is_severe_nature(text: str) -> bool:
    """True when the matter is of an inherently severe *nature*.

    Broader than the veto-marker set: it catches fraud / criminal / insolvency /
    sanctions / regulatory-ban matters even when phrased as an *allegation* or an
    ongoing *probe* (no conviction word). Such matters never auto-REJECT — but
    they are material concerns that must be investigated (ENHANCED DUE DILIGENCE)
    rather than waved through as ordinary adverse media.
    """
    from app.core.risk.litigation import classify_litigation

    return classify_litigation(text or "").risk_band == "HIGH"


def _finding_is_verified_severe(f: dict[str, Any], *, dimension: str | None = None) -> bool:
    """True when a HIGH finding cites a veto-class event AND is on an official record.

    This is the gate that prevents hallucinated / media-only claims from
    triggering an auto-veto. A veto-class score (100) is only reachable when the
    finding's source is a Tier-1 official record (regulator, court, registry,
    sanctions list). Media or inference-grade citations top out at 75 (HIGH but
    not auto-veto), routing the case to ENHANCED DUE DILIGENCE instead of REJECT.
    """
    text = str(f.get("point") or f.get("summary") or "")
    if _score_for_finding_text(text) < 100:
        return False
    src = str(f.get("source") or f.get("search_hyperlink") or "")
    return classify_fact(text, src, dimension=dimension) == FactType.VERIFIED_FACT


def _derive_dimension_score(findings: list[Any], *, dimension: str | None = None) -> int:
    """Highest severity-band across findings in a section, with veto bumps.

    Veto-text bump only fires when the LLM already scored the finding HIGH
    (base ≥ 75).  Applying it to LOW/INFO findings causes false positives
    because sentences like "Not found on sanctions lists" or "No wilful
    default records found" contain veto keywords in a NEGATIVE context.

    The 75→100 (auto-veto) bump additionally requires the finding to be a
    VERIFIED FACT (Tier-1 official source). An unverified severe claim stays at
    75: HIGH on the dimension, but never an automatic veto/REJECT.
    """
    if not findings:
        return 0
    best = 0
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "").upper()
        base = _SEVERITY_TO_SCORE.get(sev, 25)
        # Only apply text-based veto bump when the LLM already flagged HIGH.
        # LOW/INFO findings that mention "sanction" / "default" etc. are almost
        # always negative-context ("Not found on…") and must NOT be bumped.
        if base >= 75 and _finding_is_verified_severe(f, dimension=dimension):
            bumped = 100
        else:
            bumped = base
        best = max(best, bumped)
    return best


def _compute_risk_score(dimension_scores: dict[str, int]) -> int:
    """Weighted sum per Step 2 of the rubric — returns 0–100 integer."""
    total = 0.0
    for dim, weight in _DIMENSION_WEIGHTS.items():
        val = int(dimension_scores.get(dim, 0))
        val = max(0, min(100, val))
        total += (val * weight) / 100.0
    return max(0, min(100, int(round(total))))


def _score_to_rating(score: int, dim: dict[str, int], veto: bool) -> str:
    """Map score → HIGH/MEDIUM/LOW with veto + floor rules from Steps 3–4."""
    if veto:
        return "HIGH"
    if score >= 55:
        return "HIGH"
    # Floor rule: any of these ≥ 50 → cannot be LOW
    floor = (
        dim.get("litigations", 0) >= 50
        or dim.get("statutory_compliance", 0) >= 50
        or dim.get("adverse_media", 0) >= 50
    )
    if score >= 25 or floor:
        return "MEDIUM"
    return "LOW"


def _rating_to_recommendation(rating: str, confidence: str) -> str:
    """Step 6 mapping. LOW + LOW-confidence → CONDITIONAL (insufficient evidence)."""
    if rating == "HIGH":
        return "REJECT"
    if rating == "MEDIUM":
        return "CONDITIONAL"
    # rating == LOW
    if (confidence or "").upper() == "LOW":
        return "CONDITIONAL"
    return "PROCEED"


def _ensure_calibrated_rubric(data: dict[str, Any]) -> None:
    """Fill missing rubric fields (dimension_scores, risk_score, rating mapping)
    using findings from the report. Idempotent — keeps Gemini-supplied values
    when present and valid.
    """
    es = data.get("executive_summary")
    if not isinstance(es, dict):
        es = {}
        data["executive_summary"] = es

    # 0. Litigation Intelligence (all paths). Re-grade each litigation finding by
    # the *nature* of the matter rather than mere keyword presence, so routine
    # commercial / consumer / appeal disputes and matters resolved in the
    # entity's favour are not scored like fraud or insolvency. Fraud / criminal /
    # insolvency-against / conviction language is (correctly) escalated to HIGH.
    for f in data.get("litigations") or []:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity") or "INFO").upper()
        if sev not in ("LOW", "MEDIUM", "HIGH"):
            continue
        from app.core.risk.litigation import classify_litigation

        assessment = classify_litigation(str(f.get("point") or ""))
        if assessment.risk_band != sev:
            f["severity"] = assessment.risk_band
            f["litigation_nature"] = assessment.nature.value
            f["litigation_outcome"] = assessment.outcome.value

    # Capture the LLM's own risk_rating *before* we recompute it. In hybrid mode
    # this is `synthesis.risk_rating` (already capped HIGH→MEDIUM by
    # build_vra_report when no GSTIN is verified); in the legacy/search path it
    # is Gemini's summary field. We do not trust it as the source of truth, but
    # we use it below as a one-directional floor so a flagged vendor can never be
    # silently reported as LOW/PROCEED.
    llm_rating = str(es.get("risk_rating") or "").upper()

    # 1. dimension_scores — if Gemini didn't return them, derive from findings.
    dim_raw = es.get("dimension_scores")
    if not isinstance(dim_raw, dict):
        dim_raw = {}
    dim: dict[str, int] = {}
    for k in _ALL_DIMENSIONS:
        v = dim_raw.get(k)
        try:
            dim[k] = max(0, min(100, int(round(float(v))))) if v is not None else -1
        except (TypeError, ValueError):
            dim[k] = -1

    # Findings are the ONLY ground truth for dimension scores.
    # Gemini's dimension_scores JSON field is systematically unreliable —
    # it hallucinates 100 (veto-class) even when per-section findings are
    # all INFO or LOW (e.g. "No records found").  The previous partial fix
    # (cap only when derived==0) still let hallucinated 100s through whenever
    # findings were LOW (derived=25).  The correct rule: always use derived.
    #
    # If Gemini genuinely found something severe it MUST have written a
    # HIGH-severity finding in the relevant section — the derived score will
    # then be ≥ 75 automatically.  There is no legitimate case where
    # Gemini's summary score should exceed what the per-section findings show.
    for section, dim_key in _SECTION_TO_DIMENSION.items():
        derived = _derive_dimension_score(data.get(section) or [], dimension=dim_key)
        dim[dim_key] = derived

    # Event-driven minimum-severity floor. The LLM is non-deterministic on
    # severity classification — two runs of the same vendor can land the
    # same RBI restriction at HIGH on one run and MEDIUM on the next (we
    # observed Paytm classified LOW/PROCEED at 08:53 and MEDIUM/CONDITIONAL
    # at 08:32 from the same prompt). Compliance reports cannot tolerate
    # that drift.
    #
    # Scan top_findings + executive narrative for unambiguous veto-class
    # event phrases. When a match is found, set a MINIMUM floor on the
    # relevant dimensions so the worst-case run still produces the correct
    # rating. The LLM can still go HIGHER (e.g. 100/veto) when its own
    # findings warrant it; this only prevents the run from going LOWER
    # than the documented event severity.
    narrative_text = ""
    for k in ("top_findings", "top_risk_drivers", "top_positives"):
        v = es.get(k)
        if isinstance(v, list):
            narrative_text += " " + " ".join(str(x) for x in v if x)
        elif isinstance(v, str):
            narrative_text += " " + v
    for k in ("summary", "narrative", "overview", "assessment"):
        v = es.get(k)
        if isinstance(v, str):
            narrative_text += " " + v
    narrative_text = narrative_text.lower()

    # Each entry: (phrase_match_set, {dimension: minimum_score})
    # Phrases must appear without a preceding negation ("no", "not", "without")
    # for the floor to apply — `_score_for_finding_text` already handles
    # negation; we reuse that pattern here.
    _EVENT_FLOORS: tuple[tuple[tuple[str, ...], dict[str, int]], ...] = (
        # RBI enforcement against the vendor / its banking arm
        (
            ("rbi cease", "rbi directed", "cease and desist", "stop onboarding",
             "stop accepting new", "stop further deposit", "stop banking",
             "license cancelled", "licence cancelled", "license revoked",
             "licence revoked", "rbi penalties", "rbi penalty",
             "supervisory restriction", "regulatory restriction",
             "rbi imposed restriction", "rbi enforcement"),
            {"defaults": 75, "statutory_compliance": 75, "credit_ratings": 50},
        ),
        # Money laundering / FEMA / ED probe
        (
            ("money laundering", "fema violation", "pmla chargesheet",
             "ed chargesheet", "ed probe", "ed investigation",
             "enforcement directorate", "fiu-ind penalty", "fiu penalty"),
            {"sanctions_aml_fraud": 75, "statutory_compliance": 75},
        ),
        # Director/promoter arrest / FIR / criminal investigation
        (
            ("director arrested", "founder arrested", "promoter arrested",
             "fir against", "criminal investigation", "ed summons",
             "named in fir", "arrested by"),
            {"management_integrity": 75, "litigations": 50, "sanctions_aml_fraud": 50},
        ),
        # Insolvency / liquidation
        (
            ("cirp admitted", "insolvency admitted", "liquidation order",
             "nclt admission"),
            {"financial_soundness": 75, "borrowings": 75, "defaults": 75},
        ),
        # Credit downgrade explicitly cited
        (
            ("credit rating downgrade", "rating downgrade",
             "rating watch negative", "negative outlook revised",
             "rating placed under watch"),
            {"credit_ratings": 50, "financial_soundness": 50},
        ),
        # Wilful default / SEBI debarment / GST cancellation
        (
            ("wilful defaulter listed", "sebi debarment", "sebi debarred",
             "gst cancelled", "gstin cancelled"),
            {"defaults": 75, "statutory_compliance": 75},
        ),
    )

    _NEG_WINDOW = 30  # chars of context to look back for negation
    _NEG_TOKENS = ("no ", "not ", "without ", "none of ", "absent ",
                   "did not", "doesn't ", "does not", "free of ", "free from ")

    def _phrase_present_unnegated(text: str, phrases: tuple[str, ...]) -> bool:
        for p in phrases:
            idx = text.find(p)
            if idx == -1:
                continue
            prefix = text[max(0, idx - _NEG_WINDOW):idx]
            if any(neg in prefix for neg in _NEG_TOKENS):
                continue
            return True
        return False

    applied_floors: list[str] = []
    for phrases, floors in _EVENT_FLOORS:
        if not _phrase_present_unnegated(narrative_text, phrases):
            continue
        for dim_key, floor in floors.items():
            current = dim.get(dim_key, 0)
            if current < floor:
                dim[dim_key] = floor
                applied_floors.append(f"{dim_key}: {current}→{floor} ({phrases[0]!r})")
    if applied_floors:
        logger.info(
            "_ensure_calibrated_rubric: event-driven severity floors applied: %s",
            "; ".join(applied_floors),
        )

    # Anything still negative → 0 (no signal).
    for k in _ALL_DIMENSIONS:
        if dim.get(k, -1) < 0:
            dim[k] = 0

    es["dimension_scores"] = dim

    # 2. risk_score — always recompute from derived dimension scores.
    # Gemini's own risk_score is NOT trusted because it was computed against
    # its hallucinated dimension_scores, not the corrected derived ones.
    score = _compute_risk_score(dim)
    es["risk_score"] = score

    # 3. veto_triggered — derived ONLY from dimension scores computed above.
    # Gemini's veto_triggered flag is NOT trusted — it is systematically set to
    # true even when no per-section finding justifies it (same hallucination
    # pattern as dimension_scores).  The single source of truth is whether any
    # derived dimension hit 100 (i.e. a HIGH-severity finding in that section).
    veto = any(v >= 100 for v in dim.values())
    es["veto_triggered"] = veto
    if veto:
        # Set or overwrite veto_reason to match the actual triggering dimension.
        for k in ("defaults", "sanctions_aml_fraud", "management_integrity",
                  "litigations", "statutory_compliance", "mca_filings"):
            if dim.get(k, 0) >= 100:
                es["veto_reason"] = f"Auto-HIGH: severe finding in {k.replace('_', ' ')}."
                break
    else:
        # Clear any hallucinated veto_reason from Gemini.
        es.pop("veto_reason", None)

    # 4. confidence — default to MEDIUM if LLM didn't set it.
    conf = str(es.get("confidence") or "").upper()
    if conf not in ("HIGH", "MEDIUM", "LOW"):
        conf = "MEDIUM"
    # Safety: if score=0 and ALL dimension scores are 0, the LLM has no adverse
    # signals — could be genuinely clean OR unknown small company.
    # Downgrade to LOW confidence (MEDIUM or HIGH) unless the LLM had real positive
    # evidence: HIGH confidence is only valid when GSTIN/CIN confirmed in ≥2 sources.
    # LOW confidence → CONDITIONAL recommendation (Step 6) prevents false PROCEED.
    if score == 0 and all(v == 0 for v in dim.values()) and conf in ("MEDIUM", "HIGH"):
        conf = "LOW"
        logger.info(
            "_ensure_calibrated_rubric: all dimensions=0, score=0 — downgrading "
            "confidence %s→LOW (no adverse signals found; forcing CONDITIONAL)", conf
        )
    es["confidence"] = conf

    # 5. risk_rating — start from the mechanically computed rating, then apply a
    # one-directional reconciliation against the LLM's rating (below).
    # Gemini's risk_rating is NOT trusted to *lower* the rating, and it
    # hallucinates HIGH even when all per-section findings are INFO/LOW and
    # veto=False — so the computed rating from our derived dimension scores +
    # veto flag remains the baseline. What changed: we no longer *discard* the
    # LLM rating outright, because doing so could silently downgrade a genuinely
    # high-risk vendor to LOW/PROCEED when its evidence reached us only as
    # narrative (routed at MEDIUM) and its wording isn't in _EVENT_FLOORS.
    rr = _score_to_rating(score, dim, veto)

    # Narrative-vs-rating consistency check.  When fallback models (e.g.
    # gemini-2.5-flash-lite under quota pressure) produce structured output
    # they sometimes set risk_rating=LOW while the free-form narrative and
    # `top_findings` describe concrete HIGH-severity events (license cancelled,
    # money laundering, arrest, fraud probe). The structured fields drive the
    # scorecard; the narrative goes into the executive summary. If we don't
    # reconcile, the reviewer sees a LOW badge alongside a REJECT narrative.
    #
    # Detection: rating == LOW (score 0, all dims 0) but `top_findings` /
    # summary text contains HIGH veto markers naming concrete events.
    # Resolution: promote rating to MEDIUM (cap), mark confidence LOW so the
    # recommendation drops to CONDITIONAL.  This surfaces the contradiction
    # as a data-quality issue rather than silently choosing one side.
    if rr == "LOW" and all(v == 0 for v in dim.values()):
        narrative_blobs: list[str] = []
        for k in ("top_findings", "top_risk_drivers", "top_positives"):
            v = es.get(k)
            if isinstance(v, list):
                narrative_blobs.extend(str(x) for x in v if x)
            elif isinstance(v, str):
                narrative_blobs.append(v)
        for k in ("summary", "narrative", "overview", "assessment"):
            v = es.get(k)
            if isinstance(v, str):
                narrative_blobs.append(v)
        joined = " ".join(narrative_blobs).lower()
        if joined and _score_for_finding_text(joined) >= 100:
            logger.warning(
                "_ensure_calibrated_rubric: rating=LOW but narrative contains HIGH "
                "markers — promoting to MEDIUM + confidence=LOW so the recommendation "
                "drops to CONDITIONAL. Likely cause: lite-fallback model producing "
                "inconsistent structured output."
            )
            rr = "MEDIUM"
            conf = "LOW"
            es["confidence"] = conf
            es.setdefault(
                "_rating_promoted_reason",
                "Narrative cites HIGH-severity events but structured "
                "dimensions are empty; promoted to MEDIUM with LOW confidence "
                "for manual review.",
            )

    # LLM-rating floor. The block above only fires on a fixed veto-phrase list;
    # this generalizes it to ANY case where the LLM's own structured rating
    # outranks what our dimension scores produced. Because _score_to_rating
    # already returns HIGH whenever the structured evidence supports it, an
    # llm_rating that exceeds `rr` is by definition NOT corroborated by the
    # per-section findings. We therefore reconcile *upward but cautiously*:
    #   • never let the final rating sit below the LLM's rating (kills the
    #     silent HIGH→LOW/PROCEED downgrade this guards against), but
    #   • cap an unverified escalation at MEDIUM and drop confidence to LOW so
    #     the recommendation becomes CONDITIONAL (manual review) — we do NOT
    #     auto-REJECT on an unverifiable LLM HIGH, since Gemini is known to
    #     hallucinate HIGH/veto on clean vendors.
    if llm_rating in _RATING_ORDER and _RATING_ORDER[llm_rating] > _RATING_ORDER.get(rr, 0):
        promoted = "MEDIUM" if llm_rating == "HIGH" else llm_rating
        if _RATING_ORDER[promoted] > _RATING_ORDER.get(rr, 0):
            logger.warning(
                "_ensure_calibrated_rubric: LLM rated %s but derived dimensions "
                "only support %s — promoting to %s + confidence=LOW so the "
                "recommendation drops to CONDITIONAL. Structured evidence does "
                "not independently corroborate the higher rating (likely sparse "
                "web evidence routed as narrative).",
                llm_rating, rr, promoted,
            )
            rr = promoted
            conf = "LOW"
            es["confidence"] = conf
            es.setdefault(
                "_rating_promoted_reason",
                f"LLM assessed risk as {llm_rating} but automated dimension "
                "scores could not corroborate it; promoted to MEDIUM with LOW "
                "confidence for manual review rather than reported as low risk.",
            )

    es["risk_rating"] = rr

    # 6. recommendation — always derive mechanically from computed rating.
    # Gemini's recommendation is NOT trusted (it follows its hallucinated rating).
    # The computed recommendation from our derived rating + confidence is the
    # single source of truth.  Safety-bias: never let PROCEED through when rating
    # is MEDIUM or HIGH — the expected_rec from _rating_to_recommendation already
    # enforces this.
    rec = _rating_to_recommendation(rr, conf)

    # Empty-signal safety: when every dimension scored 0, the system found
    # nothing about this vendor in public sources. That is NOT the same as
    # "verified clean" — it usually means the vendor is small / unknown /
    # outside the LLM's training data. Two protections apply:
    #
    # (a) Refuse to recommend PROCEED on an empty report. The confidence
    #     downgrade above usually forces this already, but belt-and-suspenders
    #     in case any rating/confidence combo could still produce PROCEED.
    # (b) Set `empty_signal_warning` on the executive_summary so the PDF
    #     renderer can show an amber callout — the all-zero scorecard
    #     should not visually read as "verified clean".
    if all(v == 0 for v in dim.values()):
        if rec == "PROCEED":
            logger.info(
                "_ensure_calibrated_rubric: all dimensions=0 — forcing "
                "PROCEED→CONDITIONAL."
            )
            rec = "CONDITIONAL"
        es["empty_signal_warning"] = (
            "No public-domain signal found for this vendor. The all-zero "
            "scorecard does NOT mean the vendor is verified clean — it "
            "means the system could not find substantive information. "
            "Obtain KYC, GSTIN, and incorporation proof before relying on "
            "this report."
        )

    data["recommendation"] = rec

    # 7. Auditable score rationale — show WHY the score is what it is: the
    # weighted contribution of each non-zero dimension, the veto trigger (if
    # any), and the rating→recommendation step. An auditor should be able to
    # reconstruct the number from this line.
    contribs = [
        (d, dim.get(d, 0), w, round(dim.get(d, 0) * w / 100.0, 1))
        for d, w in _DIMENSION_WEIGHTS.items()
        if dim.get(d, 0) > 0
    ]
    contribs.sort(key=lambda x: x[3], reverse=True)
    if contribs:
        breakdown = "; ".join(f"{d} {sc} (×{w}% = {pts})" for d, sc, w, pts in contribs)
    else:
        breakdown = "all 12 dimensions scored 0 (no adverse signal found)"
    veto_note = (
        f"VETO: {es.get('veto_reason')}"
        if es.get("veto_triggered")
        else "No veto rule triggered"
    )
    # The final disposition is set by the evidence-gated six-tier engine in
    # _apply_production_framework; it appends the recommendation clause so the
    # rationale and the headline recommendation always use one vocabulary.
    es["risk_score_rationale"] = (
        f"Risk score {score}/100 = weighted sum of dimension scores "
        f"[{breakdown}]. {veto_note}. Rating {rr} (confidence {conf})."
    )
    es["score_contributions"] = [
        {"dimension": d, "score": sc, "weight_pct": w, "points": pts}
        for d, sc, w, pts in contribs
    ]

    # 8. Production risk framework — annotate provenance, compute an independent
    # confidence band, derive the six-tier recommendation gated on verified
    # facts, and assemble the mandatory explainability block.
    _apply_production_framework(data, dim, score, rr)


# Veto-class markers used to decide whether a HIGH finding is "severe" for the
# purpose of REJECT-gating. Reuses the same vocabulary as `_score_for_finding_text`.
_FINDING_SECTIONS_FOR_FRAMEWORK = (
    "company_profile", "management", "credit_ratings", "financial_soundness",
    "borrowings", "funds_raised", "mca_filings", "defaults", "litigations",
    "statutory_compliance",
)
_ADVERSE_SECTIONS_FOR_FRAMEWORK = ("adverse_media", "fraud_aml")

# High-weight dimensions whose *absence* of signal is a genuine positive worth
# surfacing as a mitigant (generalized — no entity names).
# Wording is deliberately scoped to "in the OSINT gathered" — the automated
# collectors do not directly screen official sanctions/PEP lists or credit
# bureaus, so the report must not imply those registers were queried.
_POSITIVE_WHEN_ZERO: dict[str, str] = {
    "sanctions_aml_fraud": "No sanctions / AML / fraud signals surfaced in the OSINT gathered "
                           "(official sanctions/PEP lists not directly screened)",
    "defaults": "No wilful-defaulter or default records surfaced in the OSINT gathered",
    "litigations": "No adverse litigation surfaced in the OSINT gathered",
    "statutory_compliance": "No statutory / regulatory enforcement surfaced in the OSINT gathered",
    "credit_ratings": "No credit-rating downgrade or distress signal surfaced in the OSINT gathered",
}


def _gstin_verified(data: dict[str, Any]) -> bool:
    from app.schemas import GST_RE
    gst = str((data.get("vendor") or {}).get("gst") or "").strip().upper()
    return bool(GST_RE.match(gst))


def _apply_production_framework(
    data: dict[str, Any], dim: dict[str, int], score: int, rating: str
) -> None:
    """Annotate provenance, compute confidence + six-tier recommendation, and
    build the WHY-THIS-RATING explainability block.

    This runs after the deterministic dimension scoring. It is entity-agnostic:
    every decision keys off the provenance/severity of the evidence, never the
    vendor's identity.
    """
    es = data.get("executive_summary")
    if not isinstance(es, dict):
        return

    facts: list[SourceFact] = []
    seen_hosts: set[str] = set()
    verified_severe = False
    serious_unverified = False
    minor_findings = False
    credible_count = 0
    missing_sections: list[str] = []

    def _process_section(section: str, items: list[Any], *, is_adverse: bool) -> None:
        nonlocal verified_severe, serious_unverified, minor_findings, credible_count
        dim_key = _SECTION_TO_DIMENSION.get(section, section)
        section_has_signal = False
        for f in items:
            if not isinstance(f, dict):
                continue
            text = str(f.get("summary") if is_adverse else f.get("point") or "")
            if is_adverse and not text:
                text = str(f.get("point") or "")
            src = str(f.get("source") or f.get("search_hyperlink") or "")
            sev = str(f.get("severity") or "INFO").upper()

            cred = classify_source(src, dimension=dim_key)
            ft = classify_fact(text, src, dimension=dim_key)
            # Annotate the finding in place so the schema / PDF can show provenance.
            f["fact_type"] = ft.value
            f["source_tier"] = int(cred.tier)

            if sev in ("LOW", "MEDIUM", "HIGH"):
                section_has_signal = True
                if sev in ("LOW", "MEDIUM"):
                    minor_findings = True
                if sev == "HIGH":
                    has_veto_marker = _score_for_finding_text(text) >= 100
                    # Only a *confirmed* severe event (sanctions listing,
                    # conviction, CIRP admission, strike-off, wilful-default
                    # tag) sitting on an official Tier-1 record may justify
                    # REJECT.
                    if has_veto_marker and ft == FactType.VERIFIED_FACT:
                        verified_severe = True
                    # Everything else that is severe — an unverified confirmed
                    # event, OR a severe-nature matter (fraud / criminal /
                    # insolvency / sanctions / ban, including mere allegations
                    # or ongoing probes) — is a material concern that routes to
                    # ENHANCED DUE DILIGENCE for investigation, never REJECT.
                    elif has_veto_marker or _is_severe_nature(text):
                        serious_unverified = True

            # Count distinct credible sources for confidence (Tier 1–3, not
            # search-pointers / inference-only).
            if ft in (FactType.VERIFIED_FACT, FactType.MEDIA_REFERENCE) and cred.host:
                if cred.host not in seen_hosts:
                    seen_hosts.add(cred.host)
                    credible_count += 1
                    facts.append(SourceFact(tier=cred.tier, fact_type=ft))

        # A finding section that produced no adverse signal AND no verified
        # registry fact is "missing information" worth surfacing.
        if not is_adverse and not section_has_signal:
            verified_here = any(
                isinstance(f, dict) and f.get("fact_type") == FactType.VERIFIED_FACT.value
                for f in items
            )
            if not verified_here:
                missing_sections.append(section.replace("_", " "))

    for section in _FINDING_SECTIONS_FOR_FRAMEWORK:
        _process_section(section, data.get(section) or [], is_adverse=False)
    for section in _ADVERSE_SECTIONS_FOR_FRAMEWORK:
        _process_section(section, data.get(section) or [], is_adverse=True)

    gstin_ok = _gstin_verified(data)

    # ── Entity resolution context (provided by the pipeline, or a fallback) ──
    er = es.get("entity_resolution") if isinstance(es.get("entity_resolution"), dict) else {}
    if not er:
        # Legacy / search path: the pipeline did not resolve the entity. Mine
        # candidate legal names from the report's own finding text so the report
        # still records which entity was assessed and at what confidence.
        from app.core.risk.entity_resolution import resolve_entity

        vendor = data.get("vendor") or {}
        texts: list[str] = []
        for section in _FINDING_SECTIONS_FOR_FRAMEWORK:
            for f in data.get(section) or []:
                if isinstance(f, dict) and f.get("point"):
                    texts.append(str(f["point"]))
        for section in _ADVERSE_SECTIONS_FOR_FRAMEWORK:
            for f in data.get(section) or []:
                if isinstance(f, dict) and f.get("summary"):
                    texts.append(str(f["summary"]))
        resolution = resolve_entity(
            input_name=str(vendor.get("name") or ""),
            gst=str(vendor.get("gst") or ""),
            org_type=str(vendor.get("org_type") or ""),
            evidence_texts=texts[:60],
        )
        er = resolution.as_dict()
        es["entity_resolution"] = er
    entity_conf = str(er.get("confidence") or ("HIGH" if gstin_ok else "LOW")).upper()
    entity_ambiguous = bool(er.get("ambiguous", False))

    # ── Confidence (independent of risk) ─────────────────────────────────────
    conf_result = compute_confidence(facts, entity_confidence=entity_conf)
    es["confidence"] = conf_result.band
    es["confidence_score"] = conf_result.score
    es["confidence_drivers"] = conf_result.drivers
    es["confidence_components"] = conf_result.components

    # ── Risk-band elevation for genuine but unverified severe signals ────────
    if rating == "LOW" and serious_unverified:
        rating = "MEDIUM"
        es["risk_rating"] = "MEDIUM"
        es.setdefault(
            "_rating_promoted_reason",
            "A severe but unverified signal was found; elevated to MEDIUM pending "
            "official-record confirmation.",
        )

    # ── Evidence sufficiency ─────────────────────────────────────────────────
    evidence_sufficient = bool(credible_count >= 1 or gstin_ok or er.get("identifiers_verified"))

    # ── Six-tier recommendation, gated on verified facts ─────────────────────
    rec_result = recommend(
        risk_band=rating,
        confidence_band=conf_result.band,
        verified_severe=verified_severe,
        serious_unverified=serious_unverified,
        minor_findings=minor_findings,
        evidence_sufficient=evidence_sufficient,
        entity_ambiguous=entity_ambiguous,
    )
    data["recommendation"] = rec_result.legacy
    es["recommendation_tier"] = rec_result.recommendation
    es["recommendation_label"] = RECOMMENDATION_LABEL.get(rec_result.recommendation, "")
    es["recommendation_rationale"] = rec_result.rationale
    es["recommendation_next_actions"] = rec_result.next_actions

    # Reconcile the auditable score rationale with the final six-tier disposition
    # so the report never mixes legacy (PROCEED/…) and six-tier vocabularies.
    if es.get("risk_score_rationale"):
        label = (es["recommendation_label"] or rec_result.recommendation.replace("_", " ").title()).rstrip(". ")
        es["risk_score_rationale"] = f"{str(es['risk_score_rationale']).rstrip()} Recommendation: {label}."

    # ── Mandatory explainability block ───────────────────────────────────────
    contribs = es.get("score_contributions") or []
    top_negative = [
        f"{c['dimension'].replace('_', ' ')} scored {c['score']} "
        f"(weight {c['weight_pct']}%, +{c['points']} pts)"
        for c in contribs[:3]
    ]
    positives = list(es.get("key_mitigants") or [])
    for dim_key, label in _POSITIVE_WHEN_ZERO.items():
        if dim.get(dim_key, 0) == 0 and label not in positives:
            positives.append(label)
    if gstin_ok:
        positives.insert(0, "Legal identity anchored by a verified GSTIN")

    missing: list[str] = []
    if not gstin_ok:
        missing.append("No verified GSTIN — statutory identity unconfirmed")
    if entity_ambiguous:
        missing.append("Legal entity is ambiguous (multiple name matches)")
    if credible_count == 0:
        missing.append("No Tier-1/2 corroborating source retrieved")
    if missing_sections:
        missing.append(
            "No verified record for: " + ", ".join(sorted(set(missing_sections))[:6])
        )

    es["why_rating"] = {
        "rating": es.get("risk_rating"),
        "risk_score": score,
        "confidence": conf_result.band,
        "recommendation": rec_result.recommendation,
        "top_negative_factors": top_negative or ["No adverse dimension scored above zero."],
        "top_positive_factors": positives[:5] or ["No positive signal could be evidenced."],
        "missing_information": missing or ["None material — assessment is well evidenced."],
        "confidence_drivers": conf_result.drivers,
        "recommended_next_actions": rec_result.next_actions,
    }


def normalize_legacy_vra_payload(
    raw: dict[str, Any],
    *,
    date_str: str,
    vendor_name: str,
    gst: str,
    org_type: str,
) -> dict[str, Any]:
    """
    Fix common alternate shapes (e.g. ``vendor_assessment`` root) before ``VRAReport.model_validate``.
    """
    data = dict(raw)
    if "vendor_assessment" in data and "vendor" not in data:
        va = data.pop("vendor_assessment")
        logger.info("Normalizing LLM payload: mapping vendor_assessment → vendor / executive_summary")
        if isinstance(va, dict):
            if isinstance(va.get("vendor"), dict):
                data["vendor"] = dict(va["vendor"])
            inner_es = va.get("executive_summary")
            if isinstance(inner_es, dict):
                data.setdefault("executive_summary", dict(inner_es))
            elif inner_es is not None:
                data.setdefault("executive_summary", {"summary": str(inner_es)})
            if "recommendation" not in data and va.get("recommendation"):
                r = str(va["recommendation"]).upper()
                if r in ("PROCEED", "CONDITIONAL", "REJECT"):
                    data["recommendation"] = r  # type: ignore[assignment]
            for list_key in _VRA_LIST_KEYS:
                if list_key not in data and list_key in va and isinstance(va[list_key], list):
                    data[list_key] = list(va[list_key])
        else:
            data.setdefault("executive_summary", {"summary": str(va)[:8000]})

    data.setdefault("vendor", {"name": vendor_name, "gst": gst, "org_type": org_type})
    data.setdefault("date_of_search", date_str)

    if "executive_summary" not in data or data["executive_summary"] in (None, {}):
        data["executive_summary"] = {"risk_level": "MEDIUM"}
    elif isinstance(data["executive_summary"], dict):
        es = data["executive_summary"]
        has_narrative = any(
            isinstance(es.get(k), str) and len((es.get(k) or "").strip()) > 40
            for k in ("summary", "text", "narrative", "overview", "description", "assessment")
        )
        if not has_narrative:
            logger.info("executive_summary has no narrative; leaving risk fields only for PDF fallback")

    if "recommendation" not in data or not data["recommendation"]:
        data["recommendation"] = "CONDITIONAL"

    rec = str(data["recommendation"]).upper()
    if rec not in ("PROCEED", "CONDITIONAL", "REJECT"):
        data["recommendation"] = "CONDITIONAL"
    else:
        data["recommendation"] = rec

    for k in _VRA_LIST_KEYS:
        if k not in data or data[k] is None:
            data[k] = []

    # Final pass: deterministically fill the calibrated rubric (dimension_scores,
    # risk_score, veto, confidence) and enforce Step-6 rating↔recommendation
    # mapping — even if Gemini ignored the new instructions in the prompt.
    _ensure_calibrated_rubric(data)

    return data
