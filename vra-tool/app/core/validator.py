"""URL validation and finding cleanup before PDF / persistence."""

from __future__ import annotations

import logging
import re
import urllib.parse

import httpx

from app.core.adverse_relevance import adverse_text_matches_vendor
from app.schemas import AdverseFinding, Finding, VRAReport

logger = logging.getLogger(__name__)

# Practical HTTP(S) URL pattern for OSINT sources
_URL_RE = re.compile(
    r"^https?://[^\s]+$",
    re.IGNORECASE,
)

# Well-known canonical portals that are always valid reference points even if
# they block automated HEAD/GET requests.  Skip reachability probes for these.
_TRUSTED_DOMAINS: frozenset[str] = frozenset({
    # ── Govt / Regulatory ─────────────────────────────────────────────────
    "mca.gov.in",
    "gst.gov.in",
    "rbi.org.in",
    "sebi.gov.in",
    "ibbi.gov.in",
    "ecourts.gov.in",
    "sci.gov.in",
    "nclt.gov.in",
    "drt.gov.in",
    "incometax.gov.in",
    "office.incometaxindia.gov.in",
    "epfindia.gov.in",
    "esic.gov.in",
    "cbic.gov.in",
    "ewaybillgst.gov.in",
    "commercial.tax.up.nic.in",
    "mahagst.gov.in",
    "udyamregistration.gov.in",
    "csr.gov.in",
    "sfio.nic.in",
    "enforcementdirectorate.gov.in",
    "cbi.gov.in",
    "cybercrime.gov.in",
    "mha.gov.in",
    "pib.gov.in",
    "fiuindia.gov.in",
    "fcraonline.nic.in",
    "ngodarpan.gov.in",
    "socialjustice.gov.in",
    "delhipolice.ncog.gov.in",
    "delhihighcourt.nic.in",
    "allahabadhighcourt.in",
    # ── Credit / Financial ────────────────────────────────────────────────
    "crisil.com",
    "icra.in",
    "careratings.com",
    "indiaratings.co.in",
    "brickworkratings.com",
    "suit.cibil.com",
    "watchoutinvestors.com",
    "ibapi.in",
    "crifhighmark.com",
    "npci.org.in",
    # ── Sanctions / AML / International ──────────────────────────────────
    "un.org",
    "sanctionssearch.ofac.treas.gov",
    "eeas.europa.eu",
    "gov.uk",
    "interpol.int",
    "fatf-gafi.org",
    "opensanctions.org",
    "offshoreleaks.icij.org",
    "aleph.occrp.org",
    "transparency.org",
    "stockmaniacs.net",
    # ── Indian News & Media ───────────────────────────────────────────────
    "economictimes.indiatimes.com",
    "timesofindia.indiatimes.com",
    "livemint.com",
    "financialexpress.com",
    "thehindubusinessline.com",
    "thehindu.com",
    "hindustantimes.com",
    "indiatoday.in",
    "theprint.in",
    "cnbctv18.com",
    "moneycontrol.com",
    "indianexpress.com",
    "news18.com",
    "republicworld.com",
    "etnownews.com",
    "zeebiz.com",
    "abplive.com",
    "deccanchronicle.com",
    "freepressjournal.in",
    "ibtimes.co.in",
    "india.com",
    "newindianexpress.com",
    "rediff.com",
    "thehansindia.com",
    "thestatesman.com",
    "timesnownews.com",
    "tribuneindia.com",
    "wionews.com",
    "the420.in",
    "consumercomplaints.in",
    # ── Global / Wire ─────────────────────────────────────────────────────
    "business-standard.com",
    "reuters.com",
    "bloomberg.com",
    "google.com",
    "indiankanoon.org",
})


def _is_trusted_domain(url: str) -> bool:
    """Return True if the URL's host is in the trusted-domain whitelist."""
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        return host in _TRUSTED_DOMAINS or any(
            host.endswith("." + d) for d in _TRUSTED_DOMAINS
        )
    except Exception:
        return False


# Canonical fallback source per section when LLM provides a bad/missing URL.
# Each URL is the most authoritative open-source portal for that section.
_SECTION_FALLBACK_URL: dict[str, str] = {
    "company_profile":      "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "management":           "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "credit_ratings":       "https://www.watchoutinvestors.com/wilful_defaulters.asp",
    "financial_soundness":  "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "borrowings":           "https://www.rbi.org.in/scripts/PublicationsView.aspx?id=21620",
    "funds_raised":         "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "mca_filings":          "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do",
    "defaults":             "https://suit.cibil.com/",
    "litigations":          "https://ecourts.gov.in/ecourts_home/",
    "statutory_compliance": "https://www.gst.gov.in/commonhome",
}
_DEFAULT_FALLBACK_URL = "https://www.mca.gov.in/mcafoportal/viewCompanyMasterData.do"


def _fallback_osint_search_url(vendor_name: str, section: str) -> str:
    """
    When the model omits a real citation, prefer an explicit Google search link
    over a generic ministry homepage — the latter falsely implies the ministry
    substantiates the bullet.
    """
    vn = (vendor_name or "").strip()
    if not vn:
        return _SECTION_FALLBACK_URL.get(section, _DEFAULT_FALLBACK_URL)
    q = f'"{vn}" verification OR MCA OR GST OR litigation OR fraud'
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})


def is_plausible_url(url: str) -> bool:
    """Return True if ``url`` matches a minimal URL pattern."""
    u = (url or "").strip()
    return bool(u) and bool(_URL_RE.match(u))


def _rescue_finding(
    f: Finding,
    section: str,
    *,
    vendor_name: str = "",
    reason: str = "missing",
) -> Finding:
    """Replace bad source URL with a vendor-scoped search link.

    Only appends a transparency note when the URL was truly absent or malformed
    (reason='missing').  Unreachable-but-plausible URLs (reason='unreachable')
    are silently swapped so the report stays clean.
    """
    fallback = _fallback_osint_search_url(vendor_name, section)
    point = (f.point or "").rstrip()
    if reason == "missing" and "original source URL was missing or invalid" not in point:
        point = point + " [Verify manually: original source URL was missing or invalid.]"
    return f.model_copy(update={"source": fallback, "point": point})


async def check_url_reachable(url: str, timeout: float = 5.0) -> bool:
    """
    Best-effort HEAD request; returns False on any failure (network, timeout, 4xx).
    """
    if not is_plausible_url(url):
        return False
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.head(url)
            if resp.status_code >= 400:
                # Some servers block HEAD; try lightweight GET
                resp = await client.get(url, headers={"Range": "bytes=0-0"})
            return resp.status_code < 400
    except httpx.HTTPError as exc:
        logger.info("URL check failed for %s: %s", url, exc)
        return False
    except Exception as exc:
        logger.warning("Unexpected error checking URL %s: %s", url, exc)
        return False


def _clean_finding_list(
    items: list[Finding],
    section: str,
    *,
    vendor_name: str = "",
) -> list[Finding]:
    """Rescue findings with bad source URLs using a canonical fallback; never drop."""
    kept: list[Finding] = []
    for f in items:
        src = (f.source or "").strip()
        if not is_plausible_url(src):
            logger.warning(
                "[%s] Missing/malformed source URL — replacing with fallback for: %s",
                section,
                f.point[:200],
            )
            f = _rescue_finding(f, section, vendor_name=vendor_name, reason="missing")
        kept.append(f)
    return kept


async def _clean_finding_list_async(
    items: list[Finding],
    section: str,
    do_head: bool,
    *,
    vendor_name: str = "",
) -> list[Finding]:
    kept: list[Finding] = []
    for f in items:
        src = (f.source or "").strip()
        if not is_plausible_url(src):
            logger.warning(
                "[%s] Missing/malformed source URL — replacing with fallback for: %s",
                section,
                f.point[:200],
            )
            f = _rescue_finding(f, section, vendor_name=vendor_name, reason="missing")
        elif do_head and not _is_trusted_domain(src) and not await check_url_reachable(src):
            # Only replace URLs that are both unreachable AND not a canonical trusted portal.
            # Trusted-domain URLs (gov portals, major outlets) are valid even if they block bots.
            logger.warning(
                "[%s] Unreachable non-trusted source URL — replacing with search fallback for: %s",
                section,
                f.point[:200],
            )
            f = _rescue_finding(f, section, vendor_name=vendor_name, reason="unreachable")
        kept.append(f)
    return kept


def _clean_adverse(items: list[AdverseFinding], section: str) -> list[AdverseFinding]:
    kept: list[AdverseFinding] = []
    for f in items:
        link = (f.search_hyperlink or "").strip()
        if not is_plausible_url(link):
            # Replace bad hyperlink with a Google search for the entity name
            entity_q = (f.entity or "").replace(" ", "+")
            fallback_link = (
                f"https://www.google.com/search?q=%22{entity_q}%22"
                "+%28fraud+OR+%22adverse+news%22+OR+legal+OR+investigation%29"
            )
            logger.warning(
                "[%s] Bad hyperlink for '%s' — replacing with Google search",
                section, f.entity,
            )
            f = f.model_copy(update={"search_hyperlink": fallback_link})
        src = f.source
        if src is not None and str(src).strip() and not is_plausible_url(str(src).strip()):
            f = f.model_copy(update={"source": None})
        kept.append(f)
    return kept


def _clean_adverse_vendor_scoped(
    items: list[AdverseFinding],
    section: str,
    *,
    vendor_name: str,
    gst: str,
) -> list[AdverseFinding]:
    """URL checks plus drop homonym / off-topic news rows."""
    kept = _clean_adverse(items, section)
    vn = (vendor_name or "").strip()
    if not vn:
        return kept
    out: list[AdverseFinding] = []
    for f in kept:
        if adverse_text_matches_vendor(
            f.entity or "",
            f.summary or "",
            vendor_name=vn,
            gst=gst or "",
        ):
            out.append(f)
        else:
            logger.warning(
                "[%s] Dropped adverse (not vendor-relevant): %s",
                section,
                (f.summary or f.entity or "")[:120],
            )
    return out


async def validate_report_async(report: VRAReport, verify_urls: bool = True) -> VRAReport:
    """
    Enforce URL rules on findings; optionally verify reachability.
    """
    vn = str((report.vendor or {}).get("name") or "")
    sections = [
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
    ]
    data = report.model_dump()
    for name in sections:
        findings = [Finding.model_validate(x) for x in data.get(name, [])]
        cleaned = await _clean_finding_list_async(
            findings, name, do_head=verify_urls, vendor_name=vn
        )
        data[name] = [x.model_dump() for x in cleaned]

    gs = str((report.vendor or {}).get("gst") or "")
    data["adverse_media"] = [
        x.model_dump()
        for x in _clean_adverse_vendor_scoped(
            report.adverse_media, "adverse_media", vendor_name=vn, gst=gs
        )
    ]
    data["fraud_aml"] = [
        x.model_dump()
        for x in _clean_adverse_vendor_scoped(report.fraud_aml, "fraud_aml", vendor_name=vn, gst=gs)
    ]

    return VRAReport.model_validate(data)


def validate_report_sync(report: VRAReport, verify_urls: bool = False) -> VRAReport:
    """Synchronous variant (no HTTP checks unless verify_urls and extended)."""
    data = report.model_dump()
    vn = str((report.vendor or {}).get("name") or "")
    sections = [
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
    ]
    for name in sections:
        findings = [Finding.model_validate(x) for x in data.get(name, [])]
        cleaned = _clean_finding_list(findings, name, vendor_name=vn)
        data[name] = [x.model_dump() for x in cleaned]

    gs = str((report.vendor or {}).get("gst") or "")
    data["adverse_media"] = [
        x.model_dump()
        for x in _clean_adverse_vendor_scoped(
            report.adverse_media, "adverse_media", vendor_name=vn, gst=gs
        )
    ]
    data["fraud_aml"] = [
        x.model_dump()
        for x in _clean_adverse_vendor_scoped(report.fraud_aml, "fraud_aml", vendor_name=vn, gst=gs)
    ]
    return VRAReport.model_validate(data)
