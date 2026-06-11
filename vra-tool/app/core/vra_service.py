"""Orchestrate prompts, LLM calls (with key fallback), validation, and PDF."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.crypto import decrypt_secret
from app.core.kv_store import get_value, next_pdf_sequence, set_value
from app.core.llm.gemini import (
    GeminiProvider,
    is_retryable_with_fallback,
    is_selectable_generate_content_model,
    resolve_model_candidates,
)
from app.core.llm.openrouter import OpenRouterProvider
from app.core.pdf_generator import render_vra_pdf
from app.core.collectors import gather_evidence
from app.core.hybrid_report import build_vra_report, deterministic_synthesis
from app.core.narrative import populate_narrative
from app.core.prompts import (
    format_adverse_media_prompt,
    format_synthesis_prompt,
    format_vra_full_prompt,
    format_vra_knowledge_prompt,
)
from app.core import quota
from app.core.report_normalization import _ensure_calibrated_rubric, normalize_legacy_vra_payload
from app.core.timeutil import utcnow
from app.core.validator import validate_report_async
from app.models import ApiKey, AuditLog
from app.schemas import AdversePassResult, SynthesisResult, VRAReport

logger = logging.getLogger(__name__)

ADVERSE_JSON_TAIL = (
    "\n\nRespond with JSON only using this shape: "
    '{"executive_summary": {"risk_level": "LOW|MEDIUM|HIGH"}, '
    '"findings": [{"entity": "", "search_hyperlink": "", "summary": "", '
    '"severity": "HIGH|MEDIUM|LOW", "source": null}]}'
)

VRA_MAIN_JSON_TAIL = (
    "\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "RISK-RATING METHODOLOGY — MANDATORY, REPRODUCIBLE\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "You will NOT guess a risk level. You will COMPUTE it. Follow these four steps exactly,\n"
    "and reflect every step in the `executive_summary` object.\n\n"
    "STEP 1 — Score each of these 12 risk dimensions on a 0/25/50/75/100 scale based on what\n"
    "open-source search actually surfaced:\n"
    "  • 0   = Clean. No adverse signal. Vendor has measurable positive footprint OR is too\n"
    "          small/private to have public adverse coverage AND no negative signals found.\n"
    "  • 25  = Minor / soft signal: dated news (>3 years), one-off consumer complaint,\n"
    "          ROC compounding for procedural lapse, single low-value litigation as defendant.\n"
    "  • 50  = Material concern with recent evidence (<24 months): regulator show-cause,\n"
    "          GST notice, NPA risk reporting, multiple pending civil suits, director\n"
    "          resignations cluster, adverse media in tier-1 outlets without enforcement.\n"
    "  • 75  = Significant: active investigation, statutory penalty order issued, RBI/SEBI\n"
    "          adverse finding, NCLT petition admitted, multiple recent court orders against,\n"
    "          credit rating downgrade to non-investment grade.\n"
    "  • 100 = Severe / confirmed: criminal conviction, sanctions hit, wilful-defaulter\n"
    "          listing confirmed, SFIO/ED/CBI chargesheet, GSTIN cancelled for fraud,\n"
    "          insolvency admitted, debarment in force.\n\n"
    "The 12 dimensions (use these exact keys in `executive_summary.dimension_scores`):\n"
    "  defaults, sanctions_aml_fraud, litigations, statutory_compliance, credit_ratings,\n"
    "  adverse_media, borrowings, mca_filings, management_integrity, financial_soundness,\n"
    "  funds_raised, company_profile\n\n"
    "STEP 2 — Compute `risk_score` (0–100) as the weighted sum:\n"
    "  defaults 15% + sanctions_aml_fraud 15% + litigations 10% + statutory_compliance 10% +\n"
    "  credit_ratings 8% + adverse_media 10% + borrowings 7% + mca_filings 5% +\n"
    "  management_integrity 10% + financial_soundness 5% + funds_raised 3% + company_profile 2%\n"
    "  Round to nearest integer. Show the math is INTERNALLY consistent with dimension_scores.\n\n"
    "STEP 3 — Apply VETO RULES. If ANY of these is found in actual search results, set\n"
    "`veto_triggered=true`, fill `veto_reason` with the specific trigger + source URL, and\n"
    "FORCE `risk_rating=HIGH` regardless of computed score:\n"
    "  V1. Active sanctions hit (OFAC SDN / UN / EU / UK / OpenSanctions exact match)\n"
    "  V2. Wilful-defaulter listing (RBI / CIBIL Suit Filed / WatchOutInvestors) for the\n"
    "      vendor or any named director\n"
    "  V3. Active ED/PMLA, SFIO, CBI prosecution or chargesheet naming the vendor or director\n"
    "  V4. SEBI debarment order in force\n"
    "  V5. NCLT insolvency petition admitted (CIRP initiated) or liquidation order\n"
    "  V6. GSTIN cancelled for fraud / fake-invoicing / suo-moto with cause\n"
    "  V7. Criminal conviction (any) of director, promoter, or beneficial owner\n"
    "  V8. MCA struck-off / disqualified directors list match\n"
    "  V9. Inclusion on MHA UAPA banned-organisation list or FATF black-list jurisdiction nexus\n\n"
    "STEP 4 — Map to `risk_rating`:\n"
    "  • risk_score 0–24   → LOW\n"
    "  • risk_score 25–54  → MEDIUM\n"
    "  • risk_score 55–100 → HIGH\n"
    "  • veto_triggered=true → HIGH (overrides score)\n"
    "  FLOOR rule: if litigations≥50 OR statutory_compliance≥50 OR adverse_media≥50, the\n"
    "  rating CANNOT be LOW — escalate to at least MEDIUM.\n\n"
    "STEP 5 — Assign `confidence` (HIGH/MEDIUM/LOW) — separate from risk rating, measures\n"
    "evidence quality:\n"
    "  • HIGH   = GSTIN/CIN/PAN cited in ≥2 independent sources; clear identity confirmed\n"
    "  • MEDIUM = legal name match in ≥2 credible portals/news; identifiers partial\n"
    "  • LOW    = sparse public footprint OR only name overlap with no identifier match\n"
    "             OR you have no training knowledge about this specific vendor\n"
    "  IMPORTANT: If you are working from training knowledge only (no live search) and\n"
    "  this vendor is not a nationally prominent company, default to LOW confidence.\n"
    "  Small private companies with no public footprint = LOW confidence.\n\n"
    "STEP 6 — Recommendation mapping (use this exactly):\n"
    "  • LOW    + HIGH/MEDIUM confidence → PROCEED\n"
    "  • MEDIUM + any confidence         → CONDITIONAL\n"
    "  • HIGH   + any confidence         → REJECT\n"
    "  • LOW    + LOW  confidence        → CONDITIONAL (insufficient evidence to clear)\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "REQUIRED FIELDS IN executive_summary (NO substitutes, NO abbreviations):\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "  risk_rating:        \"HIGH\" | \"MEDIUM\" | \"LOW\"  (single word, computed above)\n"
    "  risk_score:         integer 0–100              (weighted score from Step 2)\n"
    "  confidence:         \"HIGH\" | \"MEDIUM\" | \"LOW\"  (evidence quality from Step 5)\n"
    "  veto_triggered:     true | false               (Step 3)\n"
    "  veto_reason:        string or null             (which V-rule + source URL, if triggered)\n"
    "  summary:            3–5 sentence analyst narrative that EXPLICITLY cites the top\n"
    "                      drivers from the computed scores and explains the recommendation.\n"
    "                      Do not write generic boilerplate. Reference specific findings.\n"
    "  key_risk_drivers:   array of 3 short strings — the dimensions/findings pushing\n"
    "                      the score up (e.g. \"Active SEBI enforcement order dated...\")\n"
    "  key_mitigants:      array of 2 short strings — counter-balancing positives\n"
    "                      (e.g. \"No sanctions hits across OFAC/UN/EU\")\n"
    "  dimension_scores:   object with all 12 keys above, each an integer in {0,25,50,75,100}\n\n"
    "DO NOT WRITE: \"Risk rating: Medium because the company appears legitimate.\"\n"
    "DO WRITE: \"Risk rating MEDIUM (score 38, confidence HIGH). Primary drivers: two pending\n"
    "DRT cases (litigations=50) and a 2024 CBIC GST mismatch notice (statutory_compliance=50).\n"
    "Mitigants: no sanctions/wilful-default exposure, directors clean across MCA disqualified\n"
    "list. Recommendation: CONDITIONAL pending counsel review of DRT-Mumbai OA-1234/2024.\"\n"
    "\n\nADDITIONAL CRITICAL INSTRUCTIONS:\n"
    "2. Every one of the 10 detailed section arrays (company_profile, management, credit_ratings, "
    "financial_soundness, borrowings, funds_raised, mca_filings, defaults, litigations, "
    "statutory_compliance) MUST contain at least 3-5 concrete, specific findings for this vendor. "
    "Each finding must be a full sentence describing a verifiable fact, observation, or 'no public "
    "record found' conclusion — not a one-line label. Do NOT leave any array empty or return [].\n"
    "3. Each finding MUST include a real HTTPS source URL from authoritative portals. "
    "Generic placeholders like 'https://example.com' are strictly forbidden. "
    "Preferred sources by section — company_profile/management/mca_filings: mca.gov.in; "
    "statutory_compliance: gst.gov.in, incometax.gov.in, cbic.gov.in, ewaybillgst.gov.in; "
    "defaults: suit.cibil.com, rbi.org.in, watchoutinvestors.com, ibbi.gov.in, drt.gov.in; "
    "litigations: ecourts.gov.in, sci.gov.in, indiankanoon.org, nclt.gov.in, sebi.gov.in; "
    "credit_ratings: watchoutinvestors.com, rbi.org.in, crisil.com, icra.in, careratings.com; "
    "fraud_aml: sfio.nic.in, enforcementdirectorate.gov.in, cbi.gov.in, cybercrime.gov.in, "
    "opensanctions.org, sanctionssearch.ofac.treas.gov, un.org; "
    "adverse_media: economictimes.indiatimes.com, livemint.com, thehindu.com, theprint.in, "
    "the420.in, indianexpress.com, moneycontrol.com, cnbctv18.com. "
    "If no specific page exists for this vendor, use the root portal URL and state the absence clearly.\n"
    "4. company_profile: report what open search reveals about the company. "
    "INCLUDE basic identity information (CIN if known, registered office state, "
    "incorporation year, business line, paid-up capital range, MCA active/strike-off "
    "status) so the reviewer can verify they are looking at the right legal entity — "
    "this is especially important for small / unknown vendors where adverse findings "
    "are absent. THEN report any regulatory notices, enforcement actions, news "
    "coverage, government orders, or operational issues. Do NOT pad with unrelated "
    "history (e.g. founder's previous companies) — stay on the vendor.\n"
    "5. management: name the current directors / promoters from public records "
    "(MCA, IPO prospectus, official website) so the reviewer can act on KYC. "
    "For each named director, indicate whether public search surfaces any fraud, "
    "ED/CBI/SEBI orders, court cases, criminal record, or adverse news. "
    "Clean directors should be reported as 'No adverse findings against [name] in "
    "open-source search'.\n"
    "6. adverse_media and fraud_aml: include at least one entry each. If nothing adverse is found, "
    "state 'No adverse records found for [vendor name] in open-source search as of [date]' "
    "with severity LOW and a Google News search hyperlink.\n"
    "7. All findings must be based on what you actually find through internet search. "
    "Never reproduce static registry data as a finding. Never fabricate citations.\n"
    "\n"
    "8. SEVERITY RUBRIC — apply these classifications consistently. Same event, "
    "same severity, every run.\n"
    "   HIGH events (severity=HIGH; ALSO bump the listed dimensions to score 75):\n"
    "     • RBI cease-and-desist, license cancellation, supervisory restriction, "
    "       directive to stop onboarding / accepting deposits / banking transactions\n"
    "         → defaults=HIGH, statutory_compliance=HIGH, credit_ratings=HIGH\n"
    "     • Money-laundering, FEMA, FIU-IND penalty, ED probe, PMLA chargesheet\n"
    "         → fraud_aml=HIGH, statutory_compliance=HIGH\n"
    "     • Director/promoter arrest, FIR, criminal investigation, ED summons\n"
    "         → management=HIGH, fraud_aml=HIGH, litigations=HIGH\n"
    "     • CIRP admitted, NCLT liquidation, GST cancellation, wilful default listing,\n"
    "       SEBI debarment\n"
    "         → corresponding dimension=HIGH\n"
    "     • Adverse credit rating downgrade explicitly cited\n"
    "         → credit_ratings=HIGH, financial_soundness=HIGH\n"
    "   MEDIUM events (severity=MEDIUM; dimension score ~50):\n"
    "     • Stock price decline, market-cap loss, analyst concerns, profitability "
    "       pressure (without a regulator action driving it)\n"
    "     • Routine consumer complaints aggregated on complaint sites\n"
    "     • Show-cause notices that have not yet resulted in a final order\n"
    "   LOW / INFO events (severity=LOW or INFO; dimension score ≤25):\n"
    "     • Standard MCA / GST filings completed on time\n"
    "     • Routine commercial litigation typical of large-scale operations\n"
    "     • IPO completion, funding rounds with no irregularities\n"
    "     • No public record found after authoritative-portal search\n"
    "   CRITICAL: an RBI cease-and-desist or license cancellation is NEVER a MEDIUM. "
    "   It is ALWAYS HIGH on defaults AND statutory_compliance, regardless of how the "
    "   narrative is phrased. Do not soften regulator-enforcement events to 'scrutiny' "
    "   or 'under watch' — those are MEDIUM phrasings reserved for situations where no "
    "   binding order has been issued.\n"
    "\n"
    "9. CONSISTENCY: for the same vendor on different runs, dimension scores must "
    "be reproducible within ±15 points. Use the rubric above as the contract — do "
    "not rephrase 'cease-and-desist' to 'regulatory uncertainty' to lower severity.\n"
)

_VENDOR_SCOPE_NOTE = (
    "\n\nIMPORTANT — VENDOR SCOPE: All findings, news, and adverse media MUST relate directly "
    "to the specific entity '{vendor_name}' (GST: {gst}). "
    "Exclude any results about unrelated companies, individuals, hospitals, institutions, or "
    "entities that merely share a word with the vendor name. "
    "If a search result is not clearly about this exact vendor, do NOT include it.\n"
)

# ---------------------------------------------------------------------------
# Key management helpers
# ---------------------------------------------------------------------------

def _ordered_provider_keys(db: Session, provider: str) -> list[ApiKey]:
    """Return active API key rows for *provider* sorted primary → fallback → other."""
    rows = list(
        db.execute(
            select(ApiKey).where(
                ApiKey.provider == provider,
                ApiKey.is_active.is_(True),
            )
        )
        .scalars()
        .all()
    )

    def sort_key(r: ApiKey) -> tuple[int, int]:
        label = r.label.lower()
        if label == "primary":
            return (0, r.id)
        if label.startswith("fallback"):
            return (1, r.id)
        return (2, r.id)

    return sorted(rows, key=sort_key)


def _ordered_gemini_keys(db: Session) -> list[ApiKey]:
    return _ordered_provider_keys(db, "gemini")


def build_gemini_key_candidates(
    db: Session,
    *,
    user_api_key: str | None = None,
) -> list[tuple[ApiKey | None, str, str]]:
    """(db_row_or_none, plaintext_secret, label) in retry order — Gemini keys only.

    Multi-tenant flow: when a user-supplied key arrives via the X-Gemini-Api-Key
    request header, it takes top priority and is used directly without storing
    in the DB. This isolates each browser session — user A's key is never
    persisted, user B's key never touches user A's request.

    Fallback order if no user key:
      1. DB-stored keys (encrypted, active)
      2. GEMINI_API_KEY env var (deployment bootstrap only)
    """
    out: list[tuple[ApiKey | None, str, str]] = []
    if user_api_key and user_api_key.strip():
        # User-provided key: prepended as a one-shot candidate. row=None means
        # _deactivate_bad_key is skipped (we never persist this key), and the
        # label "USER" lets logs distinguish it from ENV / DB rows.
        out.append((None, user_api_key.strip(), "USER"))
        return out
    for row in _ordered_gemini_keys(db):
        try:
            plain = decrypt_secret(row.encrypted_key)
        except Exception as exc:
            logger.warning("Skipping API key id=%s: %s", row.id, exc)
            continue
        out.append((row, plain, row.label))
    env_key = (app_settings.GEMINI_API_KEY or "").strip()
    if not out and env_key:
        out.append((None, env_key, "ENV"))
    return out


def build_openrouter_key_candidates(
    db: Session,
    *,
    user_api_key: str | None = None,
) -> list[tuple[ApiKey | None, str, str]]:
    """(db_row_or_none, plaintext_secret, label) in retry order — OpenRouter keys only.

    Same multi-tenant pattern as build_gemini_key_candidates: user-supplied key
    (via X-OpenRouter-Api-Key header) takes precedence; otherwise DB / ENV.
    """
    out: list[tuple[ApiKey | None, str, str]] = []
    if user_api_key and user_api_key.strip():
        out.append((None, user_api_key.strip(), "USER"))
        return out
    for row in _ordered_provider_keys(db, "openrouter"):
        try:
            plain = decrypt_secret(row.encrypted_key)
        except Exception as exc:
            logger.warning("Skipping OpenRouter key id=%s: %s", row.id, exc)
            continue
        out.append((row, plain, row.label))
    env_key = (getattr(app_settings, "OPENROUTER_API_KEY", None) or "").strip()
    if not out and env_key:
        out.append((None, env_key, "ENV"))
    return out


def build_key_candidates(
    db: Session,
    provider: str,
    *,
    user_api_key: str | None = None,
) -> list[tuple[ApiKey | None, str, str]]:
    """Dispatch to the right candidate builder by provider name."""
    if provider == "openrouter":
        return build_openrouter_key_candidates(db, user_api_key=user_api_key)
    return build_gemini_key_candidates(db, user_api_key=user_api_key)


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _ensure_vendor(report: VRAReport, vendor_name: str, gst: str, org_type: str) -> VRAReport:
    data = report.model_dump()
    vendor = dict(data.get("vendor") or {})
    vendor.setdefault("name", vendor_name)
    vendor.setdefault("gst", gst)
    vendor.setdefault("org_type", org_type)
    data["vendor"] = vendor
    return VRAReport.model_validate(data)


def _merge_adverse(report: VRAReport, adverse: AdversePassResult) -> VRAReport:
    data = report.model_dump()
    if adverse.executive_summary:
        base_es = dict(data.get("executive_summary") or {})
        for k, v in adverse.executive_summary.items():
            if k not in base_es or base_es[k] in (None, "", [], {}):
                base_es[k] = v
        data["executive_summary"] = base_es
    seen = {(x.get("entity"), x.get("search_hyperlink")) for x in data["adverse_media"]}
    for f in adverse.findings:
        key = (f.entity, f.search_hyperlink)
        if key not in seen:
            data["adverse_media"].append(f.model_dump(mode="json"))
            seen.add(key)
    return VRAReport.model_validate(data)


# ---------------------------------------------------------------------------
# LLM invocation — provider-agnostic
# ---------------------------------------------------------------------------

async def _run_gemini_attempts(
    db: Session,
    candidates: list[tuple[ApiKey | None, str, str]],
    *,
    model: str,
    temperature: float,
    max_output_tokens: int,
    prompt: str,
    schema: Any,
    use_search: bool = True,
) -> tuple[dict[str, Any], ApiKey | None, str, int]:
    """Try each key × each Gemini model fallback until success."""
    total_tokens = 0
    last_error: BaseException | None = None
    _, first_secret, _ = candidates[0]
    models_to_try = await resolve_model_candidates(model, first_secret)
    for try_model in models_to_try:
        for row, secret, label in candidates:
            prov = GeminiProvider(
                secret,
                model=try_model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            try:
                out = await prov.generate(prompt, schema, use_search=use_search)
                total_tokens += prov.last_total_token_count or 0
                if try_model != model:
                    logger.info("Used fallback model %s (preferred: %s)", try_model, model)
                if row is not None:
                    row.last_used_at = utcnow()
                    quota.increment_usage(db, row.id, 1)
                    db.add(row)
                return out, row, label, total_tokens
            except Exception as exc:
                last_error = exc
                if is_retryable_with_fallback(exc):
                    logger.warning(
                        "Gemini failed (key=%s, model=%s), trying next: %s",
                        label, try_model, exc,
                    )
                    continue
                raise
    if last_error:
        raise last_error
    raise RuntimeError("No Gemini API keys configured")


# Free model fallback chain — tried in order when the configured model is rate-limited.
# Only free models; skip paid ones so there's no surprise charge.
_FREE_MODEL_FALLBACKS = [
    "openai/gpt-oss-120b:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "google/gemma-4-31b-it:free",
    "openai/gpt-oss-20b:free",
]


def _is_rate_limit_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    return "429" in s or "rate limit" in s or "rate-limit" in s or "quota" in s or "temporarily" in s


async def _run_openrouter_attempts(
    db: Session,
    candidates: list[tuple[ApiKey | None, str, str]],
    *,
    model: str,
    temperature: float,
    max_output_tokens: int,
    prompt: str,
    schema: Any,
    use_search: bool = True,
) -> tuple[dict[str, Any], ApiKey | None, str, int]:
    """
    Try each OpenRouter key × model in order until success.

    When the configured model is rate-limited (429), automatically rotates
    through FREE_MODEL_FALLBACKS so free-tier users don't hit a dead end.
    """
    total_tokens = 0
    last_error: BaseException | None = None

    # Build full model list: configured model first, then fallbacks (deduplicated)
    models_to_try = [model] + [m for m in _FREE_MODEL_FALLBACKS if m != model]

    for try_model in models_to_try:
        for row, secret, label in candidates:
            prov = OpenRouterProvider(
                secret,
                model=try_model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
            )
            try:
                out = await prov.generate(prompt, schema, use_search=use_search)
                total_tokens += prov.last_total_token_count or 0
                if try_model != model:
                    logger.info(
                        "OpenRouter: used fallback model %s (configured: %s)", try_model, model
                    )
                if row is not None:
                    row.last_used_at = utcnow()
                    quota.increment_usage(db, row.id, 1)
                    db.add(row)
                return out, row, label, total_tokens
            except Exception as exc:
                last_error = exc
                if _is_rate_limit_error(exc):
                    logger.warning(
                        "OpenRouter rate-limited (key=%s, model=%s) — trying next: %s",
                        label, try_model, exc,
                    )
                    continue  # try next key, then next model
                raise  # non-rate-limit error → propagate immediately

    if last_error:
        raise last_error
    raise RuntimeError("No OpenRouter API keys configured")


async def _run_llm_attempts(
    db: Session,
    provider: str,
    candidates: list[tuple[ApiKey | None, str, str]],
    *,
    model: str,
    temperature: float,
    max_output_tokens: int,
    prompt: str,
    schema: Any,
    use_search: bool = True,
) -> tuple[dict[str, Any], ApiKey | None, str, int]:
    """Provider-agnostic dispatcher."""
    if provider == "openrouter":
        return await _run_openrouter_attempts(
            db, candidates,
            model=model,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            prompt=prompt,
            schema=schema,
            use_search=use_search,
        )
    # Default: Gemini
    return await _run_gemini_attempts(
        db, candidates,
        model=model,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        prompt=prompt,
        schema=schema,
        use_search=use_search,
    )


# ---------------------------------------------------------------------------
# Default model per provider
# ---------------------------------------------------------------------------

_DEFAULT_MODEL: dict[str, str] = {
    "gemini": "gemini-2.5-flash",
    # gpt-oss-120b is 120B — strong knowledge, currently available free tier.
    # System auto-falls back to llama-3.3-70b → gemma-4-31b → gpt-oss-20b if rate-limited.
    "openrouter": "openai/gpt-oss-120b:free",
}

# Offline fallback model list for the Settings dropdown — used only when the
# live ListModels call fails (no key / offline). The real list is fetched from
# the API (see GET /api/models). A per-request X-Llm-Model override is validated
# with is_selectable_generate_content_model(), not against this tuple.
GEMINI_MODEL_CHOICES: tuple[str, ...] = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

async def _generate_deterministic_bundle(
    db: Session,
    *,
    vendor_name: str,
    gst: str,
    org_type: str,
    request_type: str = "SINGLE",
    verify_urls: bool = True,
    user: str = "system",
) -> tuple[VRAReport, str, AuditLog]:
    """LLM-free pipeline: collectors → deterministic framework → templated narrative → PDF.

    Invoked when ``USE_LLM=false``. No Gemini / OpenRouter call is made and no API
    key is required. Facts come from the collectors; dimension scores, confidence,
    the six-tier recommendation and entity resolution come from
    ``app.core.report_normalization``; the executive narrative is templated from
    those calibrated outputs by ``app.core.narrative``.
    """
    date_str = utcnow().strftime("%Y-%m-%d")
    try:
        evidence = await gather_evidence(vendor_name, gst, org_type)
        report = build_vra_report(evidence, deterministic_synthesis(), date_str=date_str)
        report = _ensure_vendor(report, vendor_name, gst, org_type)

        report = await validate_report_async(report, verify_urls=verify_urls)

        # Authoritative deterministic scoring + explainability block.
        final = report.model_dump()
        _ensure_calibrated_rubric(final)
        # Templated analyst prose from the calibrated why_rating block.
        populate_narrative(final)
        report = VRAReport.model_validate(final)

        seq = next_pdf_sequence(db)
        pdf_path = render_vra_pdf(report, seq, vendor_name)
        rel = f"output/{pdf_path.name}"

        set_value(db, "status_last_generation_iso", utcnow().isoformat())

        audit = AuditLog(
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            provider_used="deterministic",
            key_label_used="deterministic",
            tokens_used=None,
            pdf_path=rel,
            status="SUCCESS",
            user=user,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return report, rel, audit

    except Exception as exc:
        logger.exception("Deterministic VRA generation failed")
        db.rollback()
        audit = AuditLog(
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            provider_used="deterministic",
            key_label_used=None,
            tokens_used=None,
            pdf_path=None,
            status="FAILED",
            error_message=str(exc)[:4000],
            user=user,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        raise RuntimeError(str(exc)) from exc


async def generate_vra_bundle(
    db: Session,
    *,
    vendor_name: str,
    gst: str,
    org_type: str,
    request_type: str = "SINGLE",
    verify_urls: bool = True,
    user: str = "system",
    user_api_key: str | None = None,
    user_model: str | None = None,
) -> tuple[VRAReport, str, AuditLog]:
    """
    Full pipeline: primary + adverse LLM passes, validation, PDF, audit row.

    ``user_api_key`` is a per-request override from the caller (e.g. a value
    the frontend pulled from localStorage and sent as X-Gemini-Api-Key). When
    present, it skips DB and ENV keys entirely — the LLM call uses the
    caller's key. The key is never persisted.

    Returns:
        Tuple of (report, relative PDF path ``output/...``, audit ORM object).
    """
    # Fully deterministic, LLM-free mode: no provider, no API key, no model.
    # The whole report is produced by collectors + the deterministic risk
    # framework + the templated narrative. Short-circuit all LLM setup below.
    use_llm = app_settings.USE_LLM
    if not use_llm:
        return await _generate_deterministic_bundle(
            db,
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            verify_urls=verify_urls,
            user=user,
        )

    provider = (get_value(db, "llm_provider", "gemini") or "gemini").strip().lower()
    candidates = build_key_candidates(db, provider, user_api_key=user_api_key)

    if not candidates:
        provider_display = provider.capitalize()
        raise ValueError(
            f"No {provider_display} API keys configured. "
            f"Add a Primary key in Settings under the '{provider_display}' provider."
        )

    default_model = _DEFAULT_MODEL.get(provider, "gemini-2.5-flash")
    model = get_value(db, "llm_model", default_model)
    # Per-request, browser-local model override (sent as X-Llm-Model). Honored
    # only for Gemini and only when it's an allowlisted choice — otherwise we
    # keep the server default so a stale localStorage value can't break a run.
    requested_model = (user_model or "").strip()
    if provider == "gemini" and is_selectable_generate_content_model(requested_model):
        model = requested_model
        logger.info("Using browser-selected model: %s", model)
    elif requested_model:
        logger.info("Ignoring unsupported X-Llm-Model=%r; using %s", requested_model, model)
    # Temperature 0.0 — the same vendor must produce the same structured output
    # across runs. With temp > 0 we saw Paytm classified as LOW/PROCEED on one
    # run and MEDIUM/CONDITIONAL on the next, 21 minutes apart, same prompt.
    # Compliance reports cannot tolerate that drift.
    temperature = float(get_value(db, "llm_temperature", "0.0"))
    max_output_tokens = int(get_value(db, "llm_max_output_tokens", "16384"))

    try:
        date_str = utcnow().strftime("%Y-%m-%d")

        if app_settings.USE_HYBRID_MODE:
            evidence = await gather_evidence(vendor_name, gst, org_type)
            total_web_snippets = sum(
                len(v) for v in (evidence.web_search_results or {}).values()
            )
            news_count = len(evidence.news_headlines or [])
            web_sparse = total_web_snippets == 0

            # Fall back to the LLM-only path ONLY when there is no structured
            # signal at all — neither web snippets NOR news headlines. Google
            # News RSS works even on cloud hosts where DuckDuckGo egress is
            # blocked, so abundant headlines (e.g. Kingfisher: ED/CBI/wilful
            # default) MUST drive the report through build_vra_report instead of
            # being discarded. Counting only web snippets sent news-rich vendors
            # into the grounded-only fallback and produced an all-zero scorecard.
            no_structured_signal = web_sparse and news_count == 0

            if web_sparse and not no_structured_signal:
                logger.info(
                    "Web search returned 0 snippets but %d news headlines present "
                    "— using synthesis path with search grounding (vendor=%s).",
                    news_count, vendor_name,
                )
            elif no_structured_signal:
                logger.warning(
                    "No web snippets and no news headlines — falling back to LLM-knowledge mode",
                )
            elif total_web_snippets < 10:
                logger.warning(
                    "Web search returned only %d snippets — synthesis may be incomplete",
                    total_web_snippets,
                )

            if no_structured_signal and provider == "openrouter":
                # OpenRouter free models have no live search plugin.
                # Use the knowledge-mode prompt: asks the model to reason from
                # training data instead of pretending to search the internet.
                main_token_cap = max(max_output_tokens, 16384)
                vendor_scope = _VENDOR_SCOPE_NOTE.format(vendor_name=vendor_name, gst=gst)
                main_prompt = (
                    format_vra_knowledge_prompt(vendor_name, gst, org_type, date_str)
                    + VRA_MAIN_JSON_TAIL
                    + vendor_scope
                )
                main_raw, _row1, label1, tok1 = await _run_llm_attempts(
                    db,
                    provider,
                    candidates,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=main_token_cap,
                    prompt=main_prompt,
                    schema=VRAReport,
                    use_search=False,
                )
                main_raw = normalize_legacy_vra_payload(
                    main_raw,
                    date_str=date_str,
                    vendor_name=vendor_name,
                    gst=gst,
                    org_type=org_type,
                )
                report = _ensure_vendor(VRAReport.model_validate(main_raw), vendor_name, gst, org_type)
                tok2 = 0
            elif no_structured_signal and provider == "gemini":
                # Web search collector (DuckDuckGo) is reliably blocked on cloud
                # hosts (Render, AWS Lambda, etc.). When it returns 0 snippets we
                # cannot synthesize per-section findings from evidence — they
                # come out as INFO placeholders, and the scorecard shows
                # all-zeros even though Gemini's grounded search could find the
                # real signals.
                #
                # Fall back to the legacy full-VRA prompt with Gemini grounding
                # enabled: one call asks the LLM for the complete report
                # (per-section findings + risk_rating + top_findings) using
                # Google Search as the grounding tool. This is the same path
                # used when USE_HYBRID_MODE=False and produces the
                # VRA_45-quality output regardless of host IP.
                logger.info(
                    "hybrid_mode: collectors returned 0 web snippets — "
                    "falling back to legacy full-VRA prompt with Gemini "
                    "grounded search (vendor=%s).",
                    vendor_name,
                )
                main_token_cap = max(max_output_tokens, 16384)
                vendor_scope = _VENDOR_SCOPE_NOTE.format(vendor_name=vendor_name, gst=gst)
                main_prompt = (
                    format_vra_full_prompt(vendor_name, gst, org_type, date_str)
                    + VRA_MAIN_JSON_TAIL
                    + vendor_scope
                )
                main_raw, _row1, label1, tok1 = await _run_llm_attempts(
                    db,
                    provider,
                    candidates,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=main_token_cap,
                    prompt=main_prompt,
                    schema=VRAReport,
                    use_search=True,  # Gemini grounded Google Search
                )
                main_raw = normalize_legacy_vra_payload(
                    main_raw,
                    date_str=date_str,
                    vendor_name=vendor_name,
                    gst=gst,
                    org_type=org_type,
                )
                report = _ensure_vendor(VRAReport.model_validate(main_raw), vendor_name, gst, org_type)
                tok2 = 0
            else:
                # Hybrid path: evidence pack + optional LLM search grounding (Gemini)
                synthesis_prompt = format_synthesis_prompt(vendor_name, gst, org_type, evidence)
                synthesis_raw, _row1, label1, tok1 = await _run_llm_attempts(
                    db,
                    provider,
                    candidates,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    prompt=synthesis_prompt,
                    schema=SynthesisResult,
                    # Turn on Gemini grounding whenever web search came back empty
                    # (cloud DDG block) — it complements the news headlines that
                    # are now driving this path instead of routing to the
                    # grounded-only fallback.
                    use_search=web_sparse,
                )
                report = build_vra_report(
                    evidence,
                    SynthesisResult.model_validate(synthesis_raw),
                    date_str=date_str,
                )
                report = _ensure_vendor(report, vendor_name, gst, org_type)
                tok2 = 0
        else:
            # Legacy path: send everything to the LLM with search enabled.
            # Sub-16k limits often truncate mid-JSON, so enforce a minimum cap.
            main_token_cap = max(max_output_tokens, 16384)
            vendor_scope = _VENDOR_SCOPE_NOTE.format(vendor_name=vendor_name, gst=gst)
            main_prompt = (
                format_vra_full_prompt(vendor_name, gst, org_type, date_str)
                + VRA_MAIN_JSON_TAIL
                + vendor_scope
            )
            adverse_prompt = (
                format_adverse_media_prompt(vendor_name, gst, org_type, date_str)
                + ADVERSE_JSON_TAIL
                + vendor_scope
            )

            main_raw, _row1, label1, tok1 = await _run_llm_attempts(
                db,
                provider,
                candidates,
                model=model,
                temperature=temperature,
                max_output_tokens=main_token_cap,
                prompt=main_prompt,
                schema=VRAReport,
            )
            main_raw = normalize_legacy_vra_payload(
                main_raw,
                date_str=date_str,
                vendor_name=vendor_name,
                gst=gst,
                org_type=org_type,
            )
            report = _ensure_vendor(VRAReport.model_validate(main_raw), vendor_name, gst, org_type)

            tok2 = 0
            try:
                adverse_raw, _row2, _lbl2, tok2 = await _run_llm_attempts(
                    db,
                    provider,
                    candidates,
                    model=model,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    prompt=adverse_prompt,
                    schema=AdversePassResult,
                )
                report = _merge_adverse(report, AdversePassResult.model_validate(adverse_raw))
            except Exception as exc:
                logger.warning(
                    "Adverse-media pass failed (continuing with primary report only): %s",
                    exc,
                )

        report = await validate_report_async(report, verify_urls=verify_urls)

        # Final rubric pass — recompute dimension_scores / risk_score / rating from
        # the final findings set so the PDF always shows a calibrated scorecard.
        _final = report.model_dump()
        _ensure_calibrated_rubric(_final)
        report = VRAReport.model_validate(_final)

        seq = next_pdf_sequence(db)
        pdf_path = render_vra_pdf(report, seq, vendor_name)
        rel = f"output/{pdf_path.name}"

        total_tok = tok1 + tok2
        set_value(db, "status_last_generation_iso", utcnow().isoformat())

        audit = AuditLog(
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            provider_used=provider,
            key_label_used=label1,
            tokens_used=total_tok or None,
            pdf_path=rel,
            status="SUCCESS",
            user=user,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        return report, rel, audit

    except Exception as exc:
        logger.exception("VRA generation failed")
        db.rollback()
        audit = AuditLog(
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            provider_used=provider,
            key_label_used=None,
            tokens_used=None,
            pdf_path=None,
            status="FAILED",
            error_message=str(exc)[:4000],
            user=user,
        )
        db.add(audit)
        db.commit()
        db.refresh(audit)
        raise RuntimeError(str(exc)) from exc
