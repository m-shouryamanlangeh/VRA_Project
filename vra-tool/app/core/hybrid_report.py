"""Assemble ``VRAReport`` from deterministic evidence + LLM synthesis."""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from app.core.adverse_relevance import adverse_text_matches_vendor
from app.core.collectors.orchestrator import EvidencePack
from app.schemas import GST_RE, AdverseFinding, Finding, SynthesisResult, VRAReport

logger = logging.getLogger(__name__)

_PLACEHOLDER_SOURCE = "https://www.mca.gov.in/"


def _finding(point: str, severity: str = "INFO") -> Finding:
    return Finding(point=point, source=_PLACEHOLDER_SOURCE, severity=severity)  # type: ignore[arg-type]


# ── Severity classification brain (stakeholder-owned keyword tiers) ───────────
# Matching is word-boundary (regex) so short tokens like "raid"/"fine"/"npa"/
# "pil" don't fire inside unrelated words ("afraid", "defined", "company",
# "pile"). HIGH is checked before MEDIUM before LOW; the first tier to match wins
# (so "gst fraud" → HIGH via "fraud", "insolvency petition" → HIGH via
# "insolvency", as the tiers intend).

# 🔴 HIGH — veto-class adverse signals
# Short/ambiguous tokens (e.g. FIR, ED) are written as phrases so the
# word-boundary regex can't misfire on "fir tree" / a name "Ed".
_HIGH_MARKERS = (
    "arrested", "money laundering", "terror financing", "fraud", "fraudulent",
    "defrauded", "cheating", "pmla", "ed raid", "ed probe", "ed summons",
    "ed attaches", "cbi case", "cbi probe", "cbi raid", "sfio probe", "sebi ban",
    "debarred", "blacklisted", "blacklist", "convicted", "conviction",
    "insolvency", "wilful default", "wilful defaulter", "ponzi", "scam",
    "shell company", "shell firm", "black money", "enforcement directorate",
    "look-out notice", "lookout circular", "chargesheet", "charge sheet",
    "hawala", "benami", "fir registered", "fir filed", "fir lodged",
    "fir against", "fugitive", "absconding", "red corner notice",
    "proceeds of crime", "attachment of assets", "asset attachment",
    "criminal conspiracy", "criminal breach of trust", "forgery", "embezzled",
    "siphoned", "siphoning", "diversion of funds", "round-tripping",
    "loan fraud", "bank fraud", "disproportionate assets",
)

# 🟠 MEDIUM — serious red flags
_MEDIUM_MARKERS = (
    "probe", "investigation", "scrutiny", "notice", "summons", "summoned",
    "show cause", "show-cause", "penalty", "fine", "gst fraud", "loan default",
    "npa", "non-performing", "tax evasion", "tax demand", "raid",
    "search operation", "search and seizure", "nclt", "nclat", "ibc",
    "liquidation", "winding-up", "winding up", "sarfaesi", "drt", "cibil",
    "default notice", "recovery proceedings", "auction notice", "downgrade",
    "rating cut", "rating downgrade", "negative outlook", "credit watch",
    "going concern", "audit qualification", "qualified opinion",
    "auditor resign", "data breach", "data leak", "ransomware",
    "regulatory action", "rbi action", "sebi order", "violation",
    "non-compliance", "arbitration", "dispute", "allegation", "alleged",
    "whistleblower", "irregularity", "misappropriation", "embezzlement",
)

# 🟡 LOW — minor / civil signals
_LOW_MARKERS = (
    "complaint", "pil", "legal battle", "court case", "suit filed",
    "tribunal", "consumer complaint", "consumer court", "consumer forum",
    "labour dispute", "service dispute", "defamation", "stay order",
    "interim order", "warning", "advisory", "caution", "objection",
    "disagreement", "controversy", "protest",
)

# Benign / informational language → keep INFO
_INFO_NEGATIONS = (
    "no ", "not found", "clean", "no adverse",
    "profitable", "growth", "expansion", "launches",
)


def _compile_markers(markers: tuple[str, ...]) -> re.Pattern[str]:
    alternation = "|".join(re.escape(m) for m in markers)
    return re.compile(rf"\b(?:{alternation})\b", re.IGNORECASE)


_HIGH_RE = _compile_markers(_HIGH_MARKERS)
_MEDIUM_RE = _compile_markers(_MEDIUM_MARKERS)
_LOW_RE = _compile_markers(_LOW_MARKERS)


def _classify_snippet_severity(text: str) -> str:
    """Classify a snippet/headline by the keyword brain.

    Returns HIGH / MEDIUM / LOW / INFO. INFO means no risk keyword matched.
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
    if _HIGH_RE.search(t):
        return "HIGH"
    if _MEDIUM_RE.search(t):
        return "MEDIUM"
    if _LOW_RE.search(t):
        return "LOW"
    return "INFO"


def _severity_for_title(title: str, mapping: list[dict[str, Any]]) -> str:
    """Severity for a news headline.

    Prefer an explicit LLM-provided mapping row; otherwise fall back to the
    keyword brain. A headline returned by the adverse queries that matches no
    risk keyword is recorded as LOW (a returned hit is, at minimum, low signal).
    """
    t = (title or "").strip().lower()
    for row in mapping:
        rt = str(row.get("title") or row.get("headline") or "").strip().lower()
        if rt and (rt in t or t in rt):
            s = str(row.get("severity") or "MEDIUM").upper()
            if s in ("HIGH", "MEDIUM", "LOW"):
                return s
    kw = _classify_snippet_severity(title)
    return kw if kw in ("HIGH", "MEDIUM", "LOW") else "LOW"


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
    # Surface the full collected battery (NewsCollector caps at MAX_HEADLINES=40),
    # not just the first 20 — the goal is to report ALL of a vendor's negative news.
    for h in evidence.news_headlines[:40]:
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

    # Synthesis-finding routing. When the web search collector returned nothing
    # for a dimension (common on cloud hosts where DDG blocks egress) every
    # section above falls back to a single INFO placeholder. But the LLM's
    # `synthesis.top_findings` array often contains real risk descriptions
    # tagged with a dimension prefix, e.g. "sanctions_aml_fraud: PPBL penalized
    # for money laundering...". Without routing, those findings land only in
    # executive_summary.top_findings and never drive dimension_scores, so the
    # scorecard reads 0/Clean even though the narrative cites real risks.
    #
    # Route each top_finding into the matching per-section list at MEDIUM
    # severity (NOT HIGH — the LLM narrative is not source-verifiable on its
    # own; capping at MEDIUM forces CONDITIONAL recommendation and surfaces
    # the need for manual confirmation).  Apply only when the existing
    # section list is just the placeholder INFO row (don't clobber real web
    # evidence).
    _DIM_TO_FINDING_SECTION = {
        "company_profile":      "company_profile",
        "management_integrity": "management",
        "credit_ratings":       "credit_ratings",
        "financial_soundness":  "financial_soundness",
        "borrowings":           "borrowings",
        "funds_raised":         "funds_raised",
        "mca_filings":          "mca_filings",
        "defaults":             "defaults",
        "litigations":          "litigations",
        "statutory_compliance": "statutory_compliance",
    }
    _section_lookup: dict[str, list[Finding]] = {
        "company_profile":      company_profile,
        "management":           management,
        "credit_ratings":       credit_ratings,
        "financial_soundness":  financial_soundness,
        "borrowings":           borrowings,
        "funds_raised":         funds_raised,
        "mca_filings":          mca_filings,
        "defaults":             defaults,
        "litigations":          litigations,
        "statutory_compliance": statutory_compliance,
    }

    def _section_only_has_placeholder(rows: list[Finding]) -> bool:
        """True if the section's findings are exclusively INFO placeholders."""
        return bool(rows) and all(
            (getattr(r, "severity", "INFO") or "INFO").upper() == "INFO"
            for r in rows
        )

    def _split_dim_prefix(raw: str) -> tuple[str | None, str]:
        """Parse 'dim_key: text' → (dim_key, text). Falls back to (None, text)."""
        s = (raw or "").strip()
        if ":" not in s:
            return None, s
        head, _, tail = s.partition(":")
        head_norm = head.strip().lower().replace("-", "_").replace(" ", "_")
        if head_norm in _DIM_TO_FINDING_SECTION or head_norm in (
            "sanctions_aml_fraud", "adverse_media",
        ):
            return head_norm, tail.strip()
        return None, s

    _synthesis_routed = 0
    for raw in (synthesis.top_findings or []):
        dim_key, text = _split_dim_prefix(str(raw))
        if not text:
            continue
        # Handle the two AdverseFinding-typed dims separately.
        if dim_key in ("sanctions_aml_fraud", "adverse_media"):
            target_list = fraud_aml if dim_key == "sanctions_aml_fraud" else adverse_media
            already = any(
                (text[:80].lower() in (str(getattr(a, "summary", "")) or "").lower())
                for a in target_list
            )
            if already:
                continue
            target_list.append(
                AdverseFinding(
                    entity=vendor_label,
                    search_hyperlink=entity_link,
                    summary=(text + " [Verify manually: source not retrieved by collectors.]")[:2000],
                    severity="MEDIUM",  # type: ignore[arg-type]
                    source=None,
                )
            )
            _synthesis_routed += 1
            continue
        # Finding-typed sections.
        section_name = _DIM_TO_FINDING_SECTION.get(dim_key) if dim_key else None
        if not section_name:
            continue
        target_findings = _section_lookup[section_name]
        if not _section_only_has_placeholder(target_findings):
            # Real evidence already populated this section from web search;
            # don't add another MEDIUM derived only from narrative.
            continue
        target_findings.clear()  # remove the INFO placeholder
        target_findings.append(
            Finding(
                point=(text + " [Verify manually: source not retrieved by collectors.]")[:1000],
                source=_PLACEHOLDER_SOURCE,
                severity="MEDIUM",  # type: ignore[arg-type]
            )
        )
        _synthesis_routed += 1

    if _synthesis_routed:
        logger.info(
            "hybrid_report: routed %d synthesis top_findings into per-section "
            "lists (collectors returned no web evidence for those dimensions).",
            _synthesis_routed,
        )

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
