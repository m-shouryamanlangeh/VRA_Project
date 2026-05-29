"""Orchestrate prompts, LLM calls (with key fallback), validation, and PDF."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.crypto import decrypt_secret
from app.core.kv_store import get_value, next_pdf_sequence, set_value
from app.core.llm.gemini import (
    GeminiProvider,
    is_permanent_invalid_key_error,
    is_retryable_with_fallback,
    resolve_model_candidates,
)
from app.core.pdf_generator import render_vra_pdf
from app.core.collectors import gather_evidence
from app.core.hybrid_report import build_vra_report
from app.core.prompts import (
    format_adverse_media_prompt,
    format_synthesis_prompt,
    format_vra_full_prompt,
)
from app.core import quota
from app.core.report_normalization import _ensure_calibrated_rubric, normalize_legacy_vra_payload
from app.core.validator import validate_report_async
from app.models import ApiKey, AuditLog
from app.schemas import GST_RE, AdversePassResult, Finding, SynthesisResult, VRAReport

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
    "  • LOW    = sparse public footprint OR only name overlap with no identifier match\n\n"
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
    "2. For every one of the 10 detailed section arrays (company_profile, management, credit_ratings, "
    "financial_soundness, borrowings, funds_raised, mca_filings, defaults, litigations, "
    "statutory_compliance) you MUST perform real web search and report what the search actually returns. "
    "Specifically: \n"
    "   • If your search returns real public-record material about the vendor (regulatory orders, "
    "news coverage, court cases, sanctions hits, etc.) you MUST include those findings — each as a "
    "specific, verifiable statement with the actual deep-link URL from the source (NOT a portal root). "
    "Do not omit known material public information about a well-known entity simply because it is "
    "easier to write 'no record found'. Omitting known public adverse information is a compliance "
    "failure equal in weight to fabricating findings.\n"
    "   • Aim for 3-5 substantive findings per section when real material exists. \n"
    "   • If — and only if — the search genuinely returns nothing material for that section, return a "
    "single 'no record found' statement with severity INFO citing the relevant authoritative portal "
    "root (e.g. 'No wilful defaulter listings for [vendor] found on RBI / CIBIL / WatchOutInvestors "
    "portals as of [date]', severity=INFO, source=rbi.org.in). \n"
    "   • DO NOT fabricate specific events, renames, partnerships, regulatory actions, FIRs, or "
    "investigations. Every specific claim MUST be traceable to a real search result you can cite. "
    "Fabrication is a compliance failure.\n"
    "   • DO NOT pad with filler bullets to reach a count. Three real findings is better than five "
    "with two fabricated ones.\n"
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
    "4. company_profile: report what open search reveals about the company — regulatory notices, "
    "enforcement actions, news coverage, operational status, government orders. "
    "Do NOT include CIN, registered address, authorized capital, AGM dates, or any static "
    "MCA registry data — these are not risk findings.\n"
    "5. management: report what open search reveals about named directors/founders/promoters — "
    "are any named in fraud, ED/CBI/SEBI orders, court cases, or adverse news? "
    "Do NOT simply list board composition or DIN numbers from MCA records.\n"
    "6. adverse_media and fraud_aml: include at least one entry each. If nothing adverse is found, "
    "state 'No adverse records found for [vendor name] in open-source search as of [date]' "
    "with severity INFO (NOT LOW — a clean no-signal result should not contribute to the risk "
    "score) and a Google News search hyperlink.\n"
    "7. All findings must be based on what you actually find through internet search. "
    "Never reproduce static registry data as a finding. Never fabricate citations.\n"
)

_VENDOR_SCOPE_NOTE = (
    "\n\nIMPORTANT — VENDOR SCOPE: All findings, news, and adverse media MUST relate directly "
    "to the specific entity '{vendor_name}' (GST: {gst}). "
    "Exclude any results about unrelated companies, individuals, hospitals, institutions, or "
    "entities that merely share a word with the vendor name. "
    "If a search result is not clearly about this exact vendor, do NOT include it.\n"
)


def _ordered_gemini_keys(db: Session) -> list[ApiKey]:
    rows = list(
        db.execute(
            select(ApiKey).where(
                ApiKey.provider == "gemini",
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


def build_gemini_key_candidates(db: Session) -> list[tuple[ApiKey | None, str, str]]:
    """(db_row_or_none, plaintext_secret, label) in retry order."""
    out: list[tuple[ApiKey | None, str, str]] = []
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


def _deactivate_bad_key(row_id: int) -> None:
    """Mark a key inactive in its own short transaction so the change survives
    even if the outer /generate flow rolls back."""
    from app.database import SessionLocal

    sess = SessionLocal()
    try:
        bad = sess.get(ApiKey, row_id)
        if bad is not None and bad.is_active:
            bad.is_active = False
            sess.add(bad)
            sess.commit()
            logger.warning("Auto-deactivated invalid Gemini key id=%s label=%s", bad.id, bad.label)
    except Exception as exc:
        logger.warning("Failed to auto-deactivate key id=%s: %s", row_id, exc)
        sess.rollback()
    finally:
        sess.close()


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
    """Try each key × each model fallback until success or non-retryable error."""
    total_tokens = 0
    last_error: BaseException | None = None
    invalid_key_count = 0
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
                    row.last_used_at = dt.datetime.utcnow()
                    quota.increment_usage(db, row.id, 1)
                    db.add(row)
                return out, row, label, total_tokens
            except Exception as exc:
                last_error = exc
                if is_permanent_invalid_key_error(exc):
                    invalid_key_count += 1
                    if row is not None:
                        _deactivate_bad_key(row.id)
                    logger.warning(
                        "Gemini key %s rejected as invalid; deactivated. Continuing.",
                        label,
                    )
                    continue
                if is_retryable_with_fallback(exc):
                    logger.warning(
                        "Gemini failed (key=%s, model=%s), trying next: %s",
                        label, try_model, exc,
                    )
                    continue
                raise
    if invalid_key_count and invalid_key_count == len(candidates):
        raise ValueError(
            "All Gemini API keys are invalid. Add a working key in Settings → API Keys "
            "(get one from https://aistudio.google.com/apikey)."
        )
    if last_error:
        raise last_error
    raise RuntimeError("No Gemini API keys configured")


async def generate_vra_bundle(
    db: Session,
    *,
    vendor_name: str,
    gst: str,
    org_type: str,
    request_type: str = "SINGLE",
    verify_urls: bool = True,
    user: str = "system",
) -> tuple[VRAReport, str, AuditLog]:
    """
    Full pipeline: primary + adverse LLM passes, validation, PDF, audit row.

    Returns:
        Tuple of (report, relative PDF path ``output/...``, audit ORM object).
    """
    candidates = build_gemini_key_candidates(db)
    if not candidates:
        raise ValueError(
            "No Gemini API keys configured. Add a Primary key in Settings "
            "or set GEMINI_API_KEY in the environment for bootstrap."
        )

    model = get_value(db, "llm_model", "gemini-2.5-flash")
    temperature = float(get_value(db, "llm_temperature", "0.2"))
    max_output_tokens = int(get_value(db, "llm_max_output_tokens", "16384"))

    try:
        date_str = dt.datetime.utcnow().strftime("%Y-%m-%d")

        if app_settings.USE_HYBRID_MODE:
            evidence = await gather_evidence(vendor_name, gst, org_type)
            synthesis_prompt = format_synthesis_prompt(vendor_name, gst, org_type, evidence)
            synthesis_raw, _row1, label1, tok1 = await _run_gemini_attempts(
                db,
                candidates,
                model=model,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                prompt=synthesis_prompt,
                schema=SynthesisResult,
                use_search=False,
            )
            report = build_vra_report(
                evidence,
                SynthesisResult.model_validate(synthesis_raw),
                date_str=date_str,
            )
            report = _ensure_vendor(report, vendor_name, gst, org_type)
            tok2 = 0
        else:
            # Legacy path uses Google Search (no API JSON mode) and a very large root schema;
            # sub-16k limits often truncate mid-JSON and break parsing.
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

            main_raw, _row1, label1, tok1 = await _run_gemini_attempts(
                db,
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
                adverse_raw, _row2, _lbl2, tok2 = await _run_gemini_attempts(
                    db,
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

        # Final rubric pass — after adverse-media merge and URL cleanup, recompute
        # dimension_scores / risk_score / rating from the final findings set so the
        # PDF always shows the calibrated scorecard even when the LLM omitted it
        # or when the hybrid path skipped normalize_legacy_vra_payload.
        _final = report.model_dump()

        # No-GSTIN safeguard (applies to BOTH hybrid and legacy paths). Without a
        # verified GSTIN, name-only OSINT can pull findings about related legal
        # entities sharing the trade name (e.g. searching "PAYTM" surfaces One97
        # Communications Ltd and Paytm Payments Bank Ltd, distinct legal persons).
        # Persist a flag so the calibrated rubric refuses to promote the rating
        # back to HIGH, and surface an explicit entity-scope warning at the top of
        # company_profile so reviewers see the caveat before any finding.
        gstin_ok = bool(GST_RE.match(str(gst or "").strip().upper()))
        if not gstin_ok:
            es = _final.get("executive_summary")
            if not isinstance(es, dict):
                es = {}
                _final["executive_summary"] = es
            es["_capped_no_gstin"] = True
            es["entity_scope_warning"] = (
                "Findings derived from name-only OSINT; specific legal entity not verified."
            )
            # If the LLM already labelled the case HIGH, fold to MEDIUM up front so
            # the rubric's promotion guard sees a MEDIUM baseline.
            rr = str(es.get("risk_rating") or es.get("risk_level") or "").upper()
            if rr == "HIGH":
                es["risk_rating"] = "MEDIUM"
                es["risk_level"] = "MEDIUM"
                logger.info(
                    "No-GSTIN cap (post-process): risk_rating HIGH→MEDIUM for vendor=%s",
                    vendor_name,
                )

            warning_text = (
                "ENTITY SCOPE WARNING: No GSTIN supplied. Findings below may concern "
                "related legal entities sharing the trade name (e.g. holding company, "
                "payments bank subsidiary, group affiliates). Verify the exact legal "
                "entity on https://services.gst.gov.in/services/searchgstin or "
                "https://www.mca.gov.in/ before relying on any conclusion."
            )
            cp = _final.get("company_profile") or []
            if not any(
                isinstance(r, dict) and "ENTITY SCOPE WARNING" in str(r.get("point") or "")
                for r in cp
            ):
                cp.insert(
                    0,
                    Finding(
                        point=warning_text,
                        source="https://services.gst.gov.in/services/searchgstin",
                        severity="MEDIUM",  # type: ignore[arg-type]
                    ).model_dump(),
                )
                _final["company_profile"] = cp

        _ensure_calibrated_rubric(_final)
        report = VRAReport.model_validate(_final)

        seq = next_pdf_sequence(db)
        pdf_path = render_vra_pdf(report, seq, vendor_name)
        rel = f"output/{pdf_path.name}"

        total_tok = tok1 + tok2
        set_value(db, "status_last_generation_iso", dt.datetime.utcnow().isoformat())

        audit = AuditLog(
            vendor_name=vendor_name,
            gst=gst,
            org_type=org_type,
            request_type=request_type,
            provider_used="gemini",
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
            provider_used="gemini",
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
