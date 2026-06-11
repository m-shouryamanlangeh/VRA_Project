"""Filter adverse-media rows that are not about the assessed vendor (noisy news / homonyms)."""

from __future__ import annotations

import re
import urllib.parse

from rapidfuzz import fuzz

# Generic org suffixes — not distinctive for matching
_STOP = frozenset(
    {
        "PRIVATE",
        "LIMITED",
        "PARTNERSHIP",
        "LLP",
        "LTD",
        "INDIA",
        "COMPANY",
        "THE",
        "AND",
        "ENTERPRISES",
        "SERVICES",
        "GROUP",
        "HOLDINGS",
        "GLOBAL",
        "SOLUTIONS",
        "TECHNOLOGIES",
        "INDUSTRIES",
        "INFRA",
        "INFRASTRUCTURE",
        "DEVELOPERS",
        "REALTY",
        "ESTATES",
        "MEDIA",
        "POWER",
        "ENERGY",
        "CORP",
        "CORPORATION",
    }
)


def _significant_tokens(name: str) -> list[str]:
    parts: list[str] = []
    for w in re.split(r"[^\w]+", (name or "").upper()):
        if len(w) >= 4 and w not in _STOP:
            parts.append(w)
    return parts


def _pan_from_gstin(gst: str) -> str:
    """Embedded PAN inside a 15-char GSTIN (positions 3–12)."""
    g = (gst or "").strip().upper()
    if len(g) != 15:
        return ""
    return g[2:12]


def adverse_text_matches_vendor(
    entity: str,
    summary: str,
    *,
    vendor_name: str,
    gst: str = "",
) -> bool:
    """
    Return True if entity + summary plausibly refer to ``vendor_name``.

    Stricter than a plain substring search:
    - Prefer **full GSTIN** or embedded **PAN** in the text (avoids weak partial matches).
    - Require **more token overlap** when the legal name has several distinctive tokens.
    - Higher fuzzy thresholds for single-token names to cut homonyms.
    """
    vn = (vendor_name or "").strip()
    if not vn:
        return True
    blob = f"{entity} {summary}".upper()
    g = (gst or "").strip().upper()

    if len(g) == 15 and g in blob:
        return True
    pan = _pan_from_gstin(g)
    if len(pan) == 10 and pan in blob:
        return True

    toks = _significant_tokens(vn)
    blob_compact = re.sub(r"\s+", " ", f"{entity} {summary}").strip()
    vn_upper = vn.upper()

    # ``partial_ratio`` tolerates extra words in the headline better than ``token_sort_ratio``,
    # while still staying low for homonym paragraphs that only share one short substring.
    if len(toks) >= 3:
        hits = sum(1 for t in toks if t in blob)
        if hits < 3:
            return False
        return fuzz.partial_ratio(vn_upper, blob_compact.upper()) >= 72

    if len(toks) == 2:
        hits = sum(1 for t in toks if t in blob)
        if hits < 2:
            return False
        return fuzz.partial_ratio(vn_upper, blob_compact.upper()) >= 66

    if len(toks) == 1:
        # Single-word vendor name (e.g. "KINGFISHER", "PAYTM"): the one
        # distinctive token is all we have. Require it as a WHOLE word — so a
        # short token can't hit inside a larger word ("MART" → "WALMART") — and
        # score with partial_ratio like the multi-token branches.
        #
        # NOTE: previously this used token_sort_ratio, which is length-sensitive
        # — comparing a ~10-char name against a full headline scored ~20, far
        # below 78, so it silently rejected EVERY real headline for one-word
        # vendors (Kingfisher's ED/CBI/wilful-default news scored 0 findings).
        if not re.search(rf"\b{re.escape(toks[0])}\b", blob):
            return False
        return fuzz.partial_ratio(vn_upper, blob_compact.upper()) >= 78

    return fuzz.token_set_ratio(vn_upper, blob_compact) >= 82


# ── Junk / vendor-as-publisher filters ───────────────────────────────────────
# Web-search snippets and page-enrichment text are noisier than RSS headlines:
# they include scraped navigation chrome and the vendor's OWN marketing / help /
# explainer pages. Both carry risk keywords ("fraud", "wilful default", "NCLT")
# yet describe nothing adverse ABOUT the vendor. These helpers let the report
# builder apply the same scrutiny to web findings that adverse_media already
# gets, so a single keyword hit can't drive a false HIGH / auto-veto.

# Visible-text fragments that only appear in site chrome / search-index dumps.
_PAGE_CHROME_MARKERS = (
    "skip to main content",
    "mobile navigation",
    "main navigation",
    "toggle navigation",
    "search engine for indian law",  # IndianKanoon site tagline
    "premium features",
    "filter results by",
    "sign in to continue",
    "create a free account",
    "cookie preferences",
    "accept all cookies",
)

# Knowledge-base / explainer phrasing. A page titled like this is educational
# content, never a report of wrongdoing by its subject.
_EXPLAINER_PHRASES = (
    "who is a", "who is an", "what is a", "what is an", "what is the",
    "what are", "complete guide", "guide to", "a guide to", "how to",
    "step by step", "step-by-step", "explained", "meaning of", "definition of",
    "guidelines", "should know", "everything you need", "frequently asked",
    "faqs", "tips to", "safety tips", "checklist", "kyc process", "e-kyc",
    "how do i", "how can i", "benefits of", "types of",
)


def is_navigation_chrome(text: str) -> bool:
    """True for scraped navigation / search-index chrome captured as a finding.

    Example (IndianKanoon *search* results page): "[Page content]
    https://indiankanoon.org/search/?formInput=AIRTEL — AIRTEL Skip to main
    content Indian Kanoon - Search engine for Indian Law … Filter Results by …".
    Real document/article excerpts begin with substantive body text, so we only
    inspect the start of the excerpt — this spares genuine judgment text whose
    body happens to mention these phrases lower down.
    """
    t = (text or "").lower()
    # A "[Page content] …/search/…" dump is a result-listing index, not a doc.
    if t.startswith("[page content]") and ("/search/?" in t or "/search?" in t):
        return True
    head = t[:220]
    return any(m in head for m in _PAGE_CHROME_MARKERS)


def _registrable_label(host: str) -> str:
    """Best-effort registrable domain label (the part before the public suffix).

    'www.airtel.in' → 'airtel'; 'blog.airtel.in' → 'airtel';
    'economictimes.indiatimes.com' → 'indiatimes'. Enough to tell whether a page
    is hosted on the *vendor's own* website vs a third-party news outlet.
    """
    h = (host or "").lower().strip()
    if h.startswith("www."):
        h = h[4:]
    parts = [p for p in h.split(".") if p]
    if not parts:
        return ""
    if len(parts) >= 3 and parts[-2] in ("co", "gov", "org", "net", "ac", "nic", "edu"):
        return parts[-3]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0]


def is_vendor_published_or_explainer(title: str, url: str, vendor_name: str) -> bool:
    """True when a page is published BY the vendor or is generic explainer content.

    Two high-precision signals, both designed NOT to suppress real reporting:
      1. The page is hosted on the vendor's own website (its brand token is the
         registrable domain label, e.g. airtel.in / paytm.com). A company's own
         blog/help/marketing pages are never adverse media about that company —
         yet they surface under adverse queries because they discuss fraud, RBI
         rules, wilful default, KYC, etc.
      2. The title reads like an explainer ("Who is a Wilful Defaulter as per
         RBI?", "Complete Guide to e-KYC") AND is branded with the vendor as the
         site name ("… - Airtel"). News reporting wrongdoing is never phrased
         this way.

    A ≥5-char brand token must EQUAL the registrable domain label (not merely be
    a substring of it) — otherwise a vendor token like "express" would falsely
    match the news outlet "indianexpress.com" and suppress real reporting.
    """
    toks = [t.lower() for t in _significant_tokens(vendor_name)]
    if not toks:
        return False

    # 1. vendor's own domain
    try:
        host = urllib.parse.urlparse(url or "").netloc.lower()
    except Exception:
        host = ""
    label = _registrable_label(host)
    if label and any(len(tok) >= 5 and tok == label for tok in toks):
        return True

    # 2. explainer title branded with the vendor as the site name
    t = (title or "").lower()
    if not any(p in t for p in _EXPLAINER_PHRASES):
        return False
    return any(re.search(rf"[-|–—:]\s*{re.escape(tok)}\b", t) for tok in toks)


# ── Vendor-as-protector / non-adverse headlines ──────────────────────────────
# Headlines that contain risk words ("fraud", "scam") but describe the vendor
# PROTECTING against them, being CLEARED, or reporting routine business — not the
# vendor committing wrongdoing. The classic false positive for telcos / banks /
# fintechs whose security products and growth news surface under adverse queries.
_BENIGN_CONTEXT_MARKERS = (
    # Anti-fraud products / advisories (vendor is the protector)
    "fraud alert", "fraud protection", "fraud prevention", "fraud detection",
    "fraud-blocking", "fraud blocking", "fraud awareness", "fraud guide",
    "fraud solution", "fraud-fighting", "fraud fighting", "anti-fraud",
    "anti fraud", "scam prevention", "scam protection", "scam-blocking",
    "scam blocking", "scam awareness", "scam guide", "anti-scam", "anti scam",
    "safety tips", "protect customers", "protect users", "protect consumers",
    "protect subscribers", "protection against", "guard against",
    "shield against", "combat fraud", "fight fraud", "curb fraud",
    "prevent fraud", "tackle fraud", "block scams", "block scam",
    "reduces financial losses", "reduce financial losses",
    "reduced financial losses", "losses due to cyberfraud",
    # Exoneration / positive outcomes (vendor cleared)
    "clean chit", "clean-chit", "acquitted", "exonerated", "cleared of",
    "gives clean", "given clean", "no wrongdoing",
)

# Protective verb followed (within a few words) by fraud/scam → vendor is acting
# AGAINST fraud, e.g. "Airtel embeds AI at network layer to curb OTP-led banking
# fraud", "Bank deploys system to detect scams".
_PROTECTIVE_FRAUD_RE = re.compile(
    r"\b(curb|curbs|curbing|combat|combats|combating|fight|fights|fighting|"
    r"prevent|prevents|preventing|tackle|tackles|tackling|block|blocks|blocking|"
    r"stop|stops|stopping|detect|detects|detecting|reduce|reduces|reducing|"
    r"counter|counters|countering|crack down on|clamp down on|safeguard against|"
    r"protect against|protecting against)\b(?:\s+[\w/'-]+){0,4}\s+"
    r"\b(fraud|frauds|fraudulent|scam|scams|cyberfraud)\b",
    re.IGNORECASE,
)


def is_benign_protector_or_advisory(text: str) -> bool:
    """True if the vendor is the protector/adviser/cleared party, not the wrongdoer."""
    t = (text or "").lower()
    if any(m in t for m in _BENIGN_CONTEXT_MARKERS):
        return True
    return bool(_PROTECTIVE_FRAUD_RE.search(t))
