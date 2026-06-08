"""Assemble ``VRAReport`` from deterministic evidence + LLM synthesis."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.adverse_relevance import adverse_text_matches_vendor
from app.core.collectors.orchestrator import EvidencePack
from app.schemas import GST_RE, AdverseFinding, Finding, SynthesisResult, VRAReport

logger = logging.getLogger(__name__)

_PLACEHOLDER_SOURCE = "https://www.mca.gov.in/"


def _finding(point: str, severity: str = "INFO") -> Finding:
    return Finding(point=point, source=_PLACEHOLDER_SOURCE, severity=severity)  # type: ignore[arg-type]


# Veto-class keywords → severity=HIGH (also bump dimension to 100 via report_normalization)
_HIGH_MARKERS = (
    "wilful default", "willful default",
    "ofac", "un sanction", "uapa",
    "ed chargesheet", "pmla", "sfio", "cbi chargesheet",
    "sebi debarment", "debarred",
    "cirp admitted", "insolvency admitted", "liquidation order",
    "gst cancelled", "gstin cancelled", "fake invoic",
    "convicted", "conviction",
    "struck off", "struck-off", "disqualified director",
    "licence cancelled", "licence revoked", "license cancelled", "license revoked",
    "banking licence", "banking license",
    "money laundering", "fema violation", "forex violation",
    "founder arrested", "director arrested", "promoter arrested",
    "fraud", "scam", "embezzle",
)

# Moderate red flags → severity=MEDIUM
_MEDIUM_MARKERS = (
    "penalty", "fine imposed", "show cause notice", "investigation",
    "downgrade", "rating revised", "negative outlook",
    "default", "npa ", "sarfaesi", "drt ",
    "nclt", "insolvency petition", "winding up",
    "raid", "search and seizure",
    "lawsuit", "litigation", "court case",
    "data breach", "data theft", "ransomware",
    "regulatory action", "rbi action", "rbi penalty",
)

# Benign / informational language → keep INFO
_INFO_NEGATIONS = (
    "no ", "not found", "clean", "no adverse",
    "profitable", "growth", "expansion", "launches",
)


def _classify_snippet_severity(text: str) -> str:
    """Classify a web-search snippet by keyword markers.
    Returns HIGH / MEDIUM / INFO so dimension scorer can pick it up.
    """
    t = (text or "").lower()
    if not t:
        return "INFO"
    # Strong negation in opening clause → INFO (e.g. "No SARFAESI / DRT signals…")
    head = t[:60]
    if any(neg in head for neg in ("no sarfaesi", "no gst cancel", "no credit rating",
                                   "no sebi observ", "no wilful", "no going concern",
                                   "no ecourts", "no adverse")):
        return "INFO"
    if any(m in t for m in _HIGH_MARKERS):
        return "HIGH"
    if any(m in t for m in _MEDIUM_MARKERS):
        return "MEDIUM"
    return "INFO"


def _severity_for_title(title: str, mapping: list[dict[str, Any]]) -> str:
    t = (title or "").strip().lower()
    for row in mapping:
        rt = str(row.get("title") or row.get("headline") or "").strip().lower()
        if rt and (rt in t or t in rt):
            s = str(row.get("severity") or "MEDIUM").upper()
            if s in ("HIGH", "MEDIUM", "LOW"):
                return s
    return "MEDIUM"


def build_vra_report(evidence: EvidencePack, synthesis: SynthesisResult, *, date_str: str) -> VRAReport:
    """Merge evidence pack and model synthesis into a full ``VRAReport``."""
    v = evidence.vendor
    gst = evidence.gst_data or {}
    mca = evidence.mca_data or {}

    es: dict[str, Any] = dict(synthesis.executive_summary or {})
    es.setdefault("risk_rating", synthesis.risk_rating)
    es.setdefault("risk_level", synthesis.risk_rating)
    # Without a verified GSTIN, do not let the model label the whole case HIGH (name-only OSINT is ambiguous).
    gstin_ok = bool(GST_RE.match(str(v.get("gst") or "").strip().upper()))
    if not gstin_ok and synthesis.risk_rating == "HIGH":
        logger.info("Hybrid: capping portfolio risk_rating HIGH→MEDIUM (no verified GSTIN on request)")
        es["risk_rating"] = "MEDIUM"
        es["risk_level"] = "MEDIUM"
    es["top_findings"] = list(synthesis.top_findings or [])
    es["top_positives"] = list(synthesis.top_positives or [])
    company_profile: list[Finding] = []
    if gst:
        if gst.get("legal_name"):
            company_profile.append(
                _finding(f"GST legal name: {gst['legal_name']}")
            )
        if gst.get("trade_name"):
            company_profile.append(_finding(f"GST trade name: {gst['trade_name']}"))
        if gst.get("gst_status"):
            company_profile.append(_finding(f"GST status (API): {gst['gst_status']}"))
        if gst.get("registration_date"):
            company_profile.append(
                _finding(f"GST registration date (API): {gst['registration_date']}")
            )
        if gst.get("state_jurisdiction"):
            company_profile.append(
                _finding(f"State jurisdiction (API): {gst['state_jurisdiction']}")
            )
        if gst.get("business_type"):
            company_profile.append(_finding(f"Constitution / business type (API): {gst['business_type']}"))
        if gst.get("address"):
            company_profile.append(_finding(f"Principal address (API): {gst['address'][:500]}"))
    if not company_profile:
        if not (str(v.get("gst") or "").strip()):
            company_profile.append(
                _finding(
                    "No GSTIN provided — profile is based on vendor name, news/RSS, and web-style "
                    "OSINT only. Obtain a GSTIN for statutory verification on "
                    "https://services.gst.gov.in/services/searchgstin ."
                )
            )
        else:
            company_profile.append(
                _finding(
                    "Hybrid mode: GST public API returned no usable fields — verify GSTIN manually "
                    f"on https://services.gst.gov.in/services/searchgstin ."
                )
            )

    management: list[Finding] = []
    directors = mca.get("directors") if isinstance(mca.get("directors"), list) else []
    if directors:
        for d in directors[:20]:
            if isinstance(d, dict):
                line = ", ".join(f"{k}: {v}" for k, v in d.items() if v)
                management.append(_finding(f"Director / signatory (MCA): {line}"))
    else:
        management.append(
            _finding(
                "Hybrid mode: MCA director scrape / API not available (CAPTCHA). "
                "Director due-diligence is manual for this run."
            )
        )

    mca_filings: list[Finding] = []
    if mca:
        for key in ("cin", "company_status", "incorporation_date", "auth_capital", "paid_up_capital", "roc_code"):
            if mca.get(key):
                mca_filings.append(_finding(f"MCA {key}: {mca[key]}"))
    if not mca_filings:
        mca_filings.append(
            _finding(
                "Hybrid mode: no MCA master data retrieved — CIN / charge filings require MCA21 or vendor disclosure."
            )
        )

    ws = evidence.web_search_results or {}

    def _web_findings(dim_key: str, fallback: str) -> list[Finding]:
        """Convert web search snippets for a dimension into Finding objects."""
        snippets = ws.get(dim_key, [])
        if not snippets:
            return [_finding(fallback)]
        findings = []
        for s in snippets[:5]:
            title = s.get("title", "")
            snippet = s.get("snippet", "")
            url = s.get("url", "") or _PLACEHOLDER_SOURCE
            text = f"{title} — {snippet}".strip(" —")
            if text:
                sev = _classify_snippet_severity(text)
                findings.append(Finding(point=text[:1000], source=url, severity=sev))  # type: ignore[arg-type]
        return findings or [_finding(fallback)]

    credit_ratings = _web_findings(
        "credit_ratings",
        "No credit rating downgrade or wilful-defaulter records found via web search. "
        "Verify manually on crisil.com, icra.in, watchoutinvestors.com.",
    )
    financial_soundness = _web_findings(
        "financial_soundness",
        "No going-concern or auditor qualification signals found via web search. "
        "Full accounts are out of scope for automated collectors.",
    )
    borrowings = _web_findings(
        "borrowings",
        "No SARFAESI / DRT / NPA signals found via web search. "
        "Request MCA CHG-7 / lender confirmations for material exposures.",
    )
    funds_raised = _web_findings(
        "funds_raised",
        "No SEBI observations or fundraising controversies found via web search.",
    )
    defaults = _web_findings(
        "defaults",
        "No wilful-defaulter listing or CIBIL suit filings found via web search. "
        "Verify manually on rbi.org.in and watchoutinvestors.com.",
    )
    litigations = _web_findings(
        "litigations",
        "No eCourts / NCLT cases surfaced via web search. "
        "Manual verification on indiankanoon.org recommended.",
    )
    statutory_compliance = _web_findings(
        "statutory_compliance",
        "No GST cancellation or CBIC enforcement notices found via web search.",
    )

    entity_link = (
        (evidence.news_meta or {}).get("entity_google_search_hyperlink")
        or f"https://www.google.com/search?q={v.get('name', '')}"
    )

    adverse_media: list[AdverseFinding] = []
    sev_map = synthesis.news_severity or []
    vendor_label = str(v.get("name") or "")
    gstin = str(v.get("gst") or "")
    gstin_verified = bool(GST_RE.match(gstin.strip().upper()))
    for h in evidence.news_headlines[:20]:
        title = str(h.get("title") or "")
        link = str(h.get("link") or entity_link)
        if not adverse_text_matches_vendor("", title, vendor_name=vendor_label, gst=gstin):
            continue
        sev = _severity_for_title(title, sev_map)
        # RSS + name-only OSINT: never flag a headline as HIGH without a verified GSTIN match path.
        if sev == "HIGH" and not gstin_verified:
            sev = "MEDIUM"
        adverse_media.append(
            AdverseFinding(
                entity=vendor_label,
                search_hyperlink=entity_link,
                summary=title[:2000],
                severity=sev,  # type: ignore[arg-type]
                source=link if link.startswith("http") else None,
            )
        )
    if not adverse_media:
        adverse_media.append(
            AdverseFinding(
                entity=v.get("name", ""),
                search_hyperlink=entity_link,
                summary="No adverse headlines returned from Google News RSS for the constructed query.",
                severity="LOW",
                source=None,
            )
        )

    fraud_aml: list[AdverseFinding] = []
    for row in adverse_media:
        if row.severity == "HIGH":
            fraud_aml.append(row)

    connected: list[dict[str, Any]] = []
    if isinstance(mca.get("connected"), list):
        connected = [x for x in mca["connected"] if isinstance(x, dict)]

    return VRAReport(
        vendor=dict(v),
        date_of_search=date_str,
        executive_summary=es,
        company_profile=company_profile,
        management=management,
        credit_ratings=credit_ratings,
        financial_soundness=financial_soundness,
        borrowings=borrowings,
        funds_raised=funds_raised,
        mca_filings=mca_filings,
        defaults=defaults,
        litigations=litigations,
        statutory_compliance=statutory_compliance,
        adverse_media=adverse_media,
        fraud_aml=fraud_aml,
        connected_entities=connected,
        recommendation=synthesis.recommendation,
    )


def compact_evidence_json(evidence: EvidencePack, *, max_chars: int = 56_000) -> str:
    """Serialize evidence for prompts with a soft size cap.

    Web search results are the richest signal — include them prominently.
    """
    # Trim web search to top 4 snippets per dimension to stay under token budget
    web_trimmed: dict[str, Any] = {}
    for dim, snippets in (evidence.web_search_results or {}).items():
        web_trimmed[dim] = snippets[:4]

    payload = {
        "vendor": evidence.vendor,
        "gst_data": evidence.gst_data,
        "mca_data": evidence.mca_data,
        "news_headlines": evidence.news_headlines[:20],
        "web_search_results": web_trimmed,          # pre-fetched evidence per dimension
        "news_meta": evidence.news_meta,
        "collector_status": evidence.collector_status,
        "collector_errors": evidence.collector_errors,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    # Progressively trim web results to fit
    for max_per_dim in (3, 2, 1):
        for dim in web_trimmed:
            web_trimmed[dim] = web_trimmed[dim][:max_per_dim]
        payload["web_search_results"] = web_trimmed
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        if len(text) <= max_chars:
            return text
    return text[: max_chars - 20] + "\n… truncated …\n"
