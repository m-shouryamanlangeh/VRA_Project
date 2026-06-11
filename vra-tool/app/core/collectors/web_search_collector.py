"""
Free web search collector using DuckDuckGo (no API key required).

Runs targeted searches for each VRA risk dimension, returns structured
snippets that the LLM can analyse without needing its own internet access.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from app.config import settings as app_settings
from app.core.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

# Max results per individual search query. Higher recall makes high-signal
# items (e.g. NCLT/CIRP insolvency cases) appear more consistently across runs
# when using the non-deterministic DuckDuckGo fallback; relevance/quality gates
# downstream drop the extra low-signal hits.
_MAX_PER_QUERY = 5
# Seconds to wait between DDG requests to avoid rate-limiting
_DDG_DELAY = 0.3
# Hard per-query wall-clock cap enforced via asyncio.wait_for().
# NOTE: DDGS(timeout=N) only sets TCP *connect* timeout, not total response
# time — so we enforce this ourselves with asyncio.wait_for on each to_thread.
_QUERY_TIMEOUT = 15
# Hard wall-clock cap for the entire collector run (seconds).
_TOTAL_TIMEOUT = 120


def _safe_text(v: Any) -> str:
    return str(v or "").strip()


# ── Serper.dev (Google Search API) ────────────────────────────────────────────

def _get_serper_key() -> str:
    """Resolve Serper API key: env var first, then encrypted DB value."""
    env_key = (app_settings.SERPER_API_KEY or "").strip()
    if env_key:
        return env_key
    # Try DB (lazy import to avoid circular deps)
    try:
        from app.database import SessionLocal
        from app.core.kv_store import get_value
        from app.core.crypto import decrypt_secret
        with SessionLocal() as db:
            enc = get_value(db, "serper_api_key_enc", "")
            if enc:
                return decrypt_secret(enc)
    except Exception:
        pass
    return ""


async def _serper_search(query: str, max_results: int = _MAX_PER_QUERY) -> list[dict[str, str]]:
    """
    Google Search via Serper.dev API — reliable, deterministic results.
    Free tier: 2,500 searches/month. Sign up at https://serper.dev

    Falls back to DDG automatically if no SERPER_API_KEY is set.
    """
    api_key = _get_serper_key()
    if not api_key:
        return []
    try:
        async with httpx.AsyncClient(timeout=10, verify=False) as client:
            resp = await client.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results, "gl": "in", "hl": "en"},
            )
            resp.raise_for_status()
            data = resp.json()
        results = []
        for r in data.get("organic", [])[:max_results]:
            results.append({
                "title":   _safe_text(r.get("title")),
                "url":     _safe_text(r.get("link")),
                "snippet": _safe_text(r.get("snippet")),
            })
        # Also include news results if present
        for r in data.get("news", [])[:max_results]:
            results.append({
                "title":   _safe_text(r.get("title")),
                "url":     _safe_text(r.get("link")),
                "snippet": _safe_text(r.get("snippet")),
                "date":    _safe_text(r.get("date")),
                "source":  _safe_text(r.get("source")),
            })
        return results
    except Exception as exc:
        logger.debug("Serper search failed for %r: %s", query, exc)
        return []


async def _search(query: str, max_results: int = _MAX_PER_QUERY) -> list[dict[str, str]]:
    """
    Primary search dispatcher: Serper (Google) if key available, else DDG.
    This is the function all VRA queries should call.
    """
    if (app_settings.SERPER_API_KEY or "").strip():
        results = await _serper_search(query, max_results)
        if results:
            logger.debug("Serper returned %d results for %r", len(results), query[:60])
            return results
        logger.debug("Serper returned 0 results, falling back to DDG")
    # Fallback: DDG
    return await _query_with_timeout(_ddg_search, query, max_results)


# ── DuckDuckGo (fallback) ─────────────────────────────────────────────────────

def _ddg_search(query: str, max_results: int = _MAX_PER_QUERY) -> list[dict[str, str]]:
    """Run a DuckDuckGo text search synchronously. Returns list of {title, href, body}."""
    try:
        from ddgs import DDGS
        # verify=False: macOS Python 3.14 SSL cert issue workaround
        with DDGS(verify=False) as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
        return [
            {
                "title": _safe_text(r.get("title")),
                "url": _safe_text(r.get("href")),
                "snippet": _safe_text(r.get("body")),
            }
            for r in results
        ]
    except Exception as exc:
        logger.debug("DDG text search failed for %r: %s", query, exc)
        return []


def _ddg_news(query: str, max_results: int = _MAX_PER_QUERY) -> list[dict[str, str]]:
    """Run a DuckDuckGo news search synchronously."""
    try:
        from ddgs import DDGS
        # verify=False: macOS Python 3.14 SSL cert issue workaround
        with DDGS(verify=False) as ddgs:
            results = list(ddgs.news(query, max_results=max_results))
        return [
            {
                "title": _safe_text(r.get("title")),
                "url": _safe_text(r.get("url")),
                "snippet": _safe_text(r.get("body")),
                "date": _safe_text(r.get("date")),
                "source": _safe_text(r.get("source")),
            }
            for r in results
        ]
    except Exception as exc:
        logger.debug("DDG news search failed for %r: %s", query, exc)
        return []


async def _query_with_timeout(
    fn: Any, query: str, max_results: int = _MAX_PER_QUERY
) -> list[dict[str, str]]:
    """
    Run a DDG search function in a thread with a hard asyncio timeout.

    DDGS(timeout=N) only sets TCP connect timeout, not total response time.
    asyncio.wait_for() on the to_thread coroutine enforces the real wall-clock
    cap — when it fires, asyncio moves on while the thread finishes in the bg.
    """
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fn, query, max_results),
            timeout=_QUERY_TIMEOUT,
        )
    except asyncio.TimeoutError:
        logger.debug("Query timed out after %ds: %r", _QUERY_TIMEOUT, query[:80])
        return []
    except Exception as exc:
        logger.debug("Query failed: %r — %s", query[:80], exc)
        return []


# Company database domains whose pages contain rich financial/legal data
# AND work without JavaScript (server-side rendered).
# Tofler/Tracxn/ZaubaCorp are JS-heavy → skip them (return empty content).
_ENRICH_DOMAINS = (
    "indiankanoon.org",   # static HTML court judgments — very useful
    "nclt.gov.in",        # NCLT case records
    "scribd.com",         # uploaded legal docs
    "ibbi.gov.in",        # insolvency board orders
)


def _is_enrichable(url: str) -> bool:
    return any(d in url for d in _ENRICH_DOMAINS)


def _strip_html(html: str) -> str:
    """Very simple HTML→text stripper (no external deps)."""
    text = re.sub(r"<style[^>]*>.*?</style>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<script[^>]*>.*?</script>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


async def _fetch_page_snippet(
    url: str,
    vendor_name: str,
    max_chars: int = 2000,
    require_vendor_name: bool = True,
) -> str | None:
    """
    Fetch a static-HTML legal/company page and return the most risk-relevant
    text excerpt (court judgments, NCLT orders, financial data).

    Returns None on error, timeout, or when no useful content found.
    """
    try:
        async with httpx.AsyncClient(
            timeout=12,
            verify=False,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; VRATool/1.0)"},
        ) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                return None
            text = _strip_html(resp.text)
    except Exception as exc:
        logger.debug("Page fetch failed for %s: %s", url, exc)
        return None

    # Extract the chunk with the most risk-relevant keyword density
    keywords = [
        "nclt", "ibc", "insolvency", "petition", "court", "default", "npa",
        "fraud", "ed ", "pmla", "cbi", "sfio", "struck off", "cancelled",
        "charge", "sarfaesi", "drt", "wilful", "arbitration",
        "net profit", "ebitda", "revenue", "loss", "roe", "roce",
        "borrowing", "debt", "negative", "audit", "going concern",
        "director", "promoter", "arrested", "sebi", "misappropriat",
    ]
    vn_lower = vendor_name.lower()
    text_lower = text.lower()
    best_start = 0
    best_score = -1

    window = 2500
    step = 400
    for i in range(0, max(1, len(text) - window), step):
        chunk = text_lower[i : i + window]
        if require_vendor_name and vn_lower not in chunk:
            continue
        score = sum(chunk.count(k) for k in keywords)
        if score > best_score:
            best_score = score
            best_start = i

    if best_score < 0:
        return None

    excerpt = text[best_start : best_start + max_chars].strip()
    if len(excerpt) < 80:
        return None
    return excerpt


def _build_queries(vendor_name: str, gst: str) -> list[tuple[str, str]]:
    """
    Return (dimension, query) pairs for the highest-signal VRA dimensions.

    Strategy:
    - Lead with company-profile queries on reliable Indian databases (Tofler,
      ZaubaCorp, IndianKanoon) to find the company's actual legal name, CIN,
      and any charges/court cases filed against it.
    - Use BOTH quoted and unquoted forms so companies with punctuation in their
      legal name (e.g. "LIT (INDIA) PRIVATE LIMITED") are still matched.
    """
    vn = vendor_name.strip()
    gstin = (gst or "").strip().upper()
    vn_q = f'"{vn}"'  # quoted exact match

    queries = [
        # ── COMPANY PROFILE (IndianKanoon / Tofler / ZaubaCorp) ─────────────
        # These Indian company databases reliably have adverse records, CIN,
        # charges, and court cases even for small private companies.
        ("company_profile",
         f'{vn} site:tofler.in OR site:zaubacorp.com OR site:indiankanoon.org'),
        ("company_profile",
         f'{vn} site:tracxn.com OR site:thecompanycheck.com company profile India'),

        # ── LITIGATIONS ──────────────────────────────────────────────────────
        # Both forms quote the vendor name: an UNQUOTED "{vn} NCLT insolvency …"
        # query returns generic NCLT/IBC orders that never name the vendor, which
        # then get keyword-scored HIGH on litigations (false positive).
        ("litigations",
         f'{vn_q} NCLT OR CIRP OR IBC OR insolvency OR "corporate insolvency" petition India'),
        ("litigations",
         f'{vn_q} NCLT OR "High Court" OR eCourts OR litigation OR "winding up" OR court case India'),

        # ── DEFAULTS / WILFUL DEFAULT ────────────────────────────────────────
        ("defaults",
         f'{vn_q} "wilful defaulter" OR "wilful default" OR NPA OR CIBIL OR DRT OR SARFAESI India'),

        # ── ADVERSE MEDIA / FRAUD ─────────────────────────────────────────────
        ("adverse_media",
         f'{vn_q} fraud OR scam OR investigation OR misappropriation OR "loan default" India'),
        ("sanctions_aml_fraud",
         f'{vn_q} PMLA OR "Enforcement Directorate" OR SFIO OR CBI India'),

        # ── MANAGEMENT INTEGRITY ─────────────────────────────────────────────
        ("management_integrity",
         f'{vn_q} director OR promoter arrested OR "loan default" OR "court case" India'),

        # ── FINANCIAL HEALTH ─────────────────────────────────────────────────
        ("financial_soundness",
         f'{vn_q} "going concern" OR "audit qualification" OR financial loss OR EBITDA India'),
    ]

    # If GSTIN is provided, add a GST-specific lookup as the first query
    if gstin:
        queries.insert(0, ("company_profile", f'GST {gstin} {vn} India'))

    return queries


class WebSearchCollector(BaseCollector):
    """
    DuckDuckGo-based web search collector.

    Runs targeted searches for the 8 highest-signal VRA risk dimensions.
    Returns structured snippets ready for LLM synthesis.
    """

    name = "web_search"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        t0 = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._collect_inner(vendor_name, gst, org_type, t0),
                timeout=_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            ms = int((time.monotonic() - t0) * 1000)
            logger.warning(
                "WebSearchCollector timed out after %dms (limit=%ds) — returning partial results",
                ms, _TOTAL_TIMEOUT,
            )
            return CollectorResult(
                name=self.name,
                status="partial",
                data={"results_by_dimension": {}, "total_snippets": 0, "queries_run": 0},
                errors=[f"Timed out after {_TOTAL_TIMEOUT}s"],
                duration_ms=ms,
                sources=["https://duckduckgo.com"],
            )

    async def _collect_inner(
        self,
        vendor_name: str,
        gst: str,
        org_type: str,
        t0: float,
    ) -> CollectorResult:
        queries = _build_queries(vendor_name, gst)

        results_by_dim: dict[str, list[dict[str, str]]] = {}
        errors: list[str] = []

        # Run queries SEQUENTIALLY with per-query asyncio timeout.
        # Fully concurrent requests trigger mass DDG rate-limiting (all engines
        # blocked simultaneously → 0 results). Sequential + per-query timeout
        # lets slow queries fail fast without blocking the remaining ones.
        using_serper = bool((app_settings.SERPER_API_KEY or "").strip())
        if using_serper:
            logger.info("WebSearchCollector: using Serper.dev (Google Search API)")
        else:
            logger.info("WebSearchCollector: no SERPER_API_KEY — using DuckDuckGo fallback")

        for dim, query in queries:
            t_q = time.monotonic()
            snippets = await _search(query, _MAX_PER_QUERY)
            if not snippets:
                # DDG-only fallback: also try news search
                snippets = await _query_with_timeout(_ddg_news, query, _MAX_PER_QUERY)
            elapsed_q = time.monotonic() - t_q

            if snippets:
                existing = results_by_dim.get(dim, [])
                seen_urls = {r["url"] for r in existing}
                for s in snippets:
                    if s.get("url") and s["url"] not in seen_urls:
                        existing.append(s)
                        seen_urls.add(s["url"])
                results_by_dim[dim] = existing
                logger.debug("Query dim=%s got %d snippets in %.1fs", dim, len(snippets), elapsed_q)
            else:
                logger.debug("Query dim=%s returned 0 snippets in %.1fs", dim, elapsed_q)

            await asyncio.sleep(_DDG_DELAY)

        # Dedicated adverse-media news sweep — unquoted name catches legal variants
        news_query = f'{vendor_name.strip()} India fraud OR investigation OR default OR penalty OR court'
        news_results = await _search(news_query, _MAX_PER_QUERY * 2)
        if news_results:
            existing = results_by_dim.get("adverse_media", [])
            seen_urls = {r["url"] for r in existing}
            for s in news_results:
                if s.get("url") and s["url"] not in seen_urls:
                    existing.append(s)
                    seen_urls.add(s["url"])
            results_by_dim["adverse_media"] = existing

        # ── Page enrichment: fetch actual content from static-HTML legal pages ──
        # DDG only returns titles + short snippets. IndianKanoon/NCLT pages are
        # static HTML and contain full judgment text — fetch them directly.
        enriched_urls: set[str] = set()
        for dim_snips in results_by_dim.values():
            for snip in dim_snips:
                url = snip.get("url", "")
                if url and _is_enrichable(url) and url not in enriched_urls:
                    enriched_urls.add(url)

        # Always fetch IndianKanoon search directly — it's static HTML and has
        # court cases indexed. Use short name (first 3 words) to catch legal variants.
        vn_words = vendor_name.strip().split()
        ik_short = "+".join(vn_words[:3])
        ik_url = f"https://indiankanoon.org/search/?formInput={ik_short}"
        enriched_urls.add(ik_url)

        for url in list(enriched_urls)[:6]:  # cap fetches to stay in time budget
            # Always require the vendor name to appear in the captured window —
            # including IndianKanoon *search* pages. Previously these were fetched
            # with require_vendor_name=False, so a vendor with no real cases still
            # got the bare search-results index (nav chrome + filter sidebar)
            # dumped in as a "litigation" finding.
            page_text = await _fetch_page_snippet(url, vendor_name, require_vendor_name=True)
            if page_text:
                dim_key = "litigations" if "indiankanoon" in url or "nclt" in url else "company_profile"
                results_by_dim.setdefault(dim_key, []).append({
                    "title": f"[Page content] {url}",
                    "url": url,
                    "snippet": page_text,
                })
                logger.info("Enriched snippet from %s (%d chars)", url, len(page_text))

        ms = int((time.monotonic() - t0) * 1000)
        total_snippets = sum(len(v) for v in results_by_dim.values())

        status = "ok" if total_snippets > 0 else "failed"
        if total_snippets > 0 and errors:
            status = "partial"

        logger.info(
            "WebSearchCollector: %d snippets across %d dimensions in %dms",
            total_snippets, len(results_by_dim), ms,
        )

        return CollectorResult(
            name=self.name,
            status=status,
            data={
                "results_by_dimension": results_by_dim,
                "total_snippets": total_snippets,
                "queries_run": len(queries) + 1,
            },
            errors=errors,
            duration_ms=ms,
            sources=["https://duckduckgo.com"],
        )
