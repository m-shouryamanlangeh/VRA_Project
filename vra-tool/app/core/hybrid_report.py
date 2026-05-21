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

    credit_ratings = [
        _finding(
            "Hybrid mode: CRISIL/ICRA credit feeds are not automated in this release; "
            "obtain rating letters from the vendor if material."
        )
    ]
    financial_soundness = [
        _finding(
            "Hybrid mode: financial soundness is inferred from public news + GST posture only; "
            "full accounts are out of scope for collectors."
        )
    ]
    borrowings = [
        _finding(
            "Hybrid mode: borrowings / charge data not scraped (MCA CAPTCHA). "
            "Request MCA CHG-7 / lender confirmations for material exposures."
        )
    ]
    funds_raised = [
        _finding(
            "Hybrid mode: funds-raised review is manual; check MCA filings and press when relevant."
        )
    ]
    defaults = [
        _finding(
            "Hybrid mode: defaults / wilful defaulter screening is manual — verify via RBI / CIBIL portals."
        )
    ]
    litigations = [
        _finding(
            "Hybrid mode: eCourts / NCLT scraping deferred (CAPTCHA / paid APIs). "
            "News scan may surface litigation hints only."
        )
    ]
    statutory_compliance = [
        _finding(
            "Hybrid mode: statutory compliance is limited to GST status in this release."
        )
    ]

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


def compact_evidence_json(evidence: EvidencePack, *, max_chars: int = 48_000) -> str:
    """Serialize evidence for prompts with a soft size cap."""
    payload = {
        "vendor": evidence.vendor,
        "gst_data": evidence.gst_data,
        "mca_data": evidence.mca_data,
        "news_headlines": evidence.news_headlines[:20],
        "news_meta": evidence.news_meta,
        "collector_status": evidence.collector_status,
        "collector_errors": evidence.collector_errors,
    }
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 20] + "\n… truncated …\n"
