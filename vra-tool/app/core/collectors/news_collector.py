"""Adverse-media headlines — the news-fetching "brain".

Fires a fixed set of 4 searches per vendor (no API key needed for the first
three; the fourth uses the shared Serper→DuckDuckGo dispatcher):

    Google News #1:  "{name}" fraud OR scam OR cheating OR ED OR CBI OR SEBI
    Google News #2:  "{name}" money laundering OR default OR insolvency OR arrested
    Google News #3:  "{name}" PMLA OR SFIO OR NCB OR chargesheet
    DuckDuckGo  #1:  "{name}" fraud OR scam OR SEBI OR ED OR arrested India

Results from all four are merged and de-duplicated into a single headline list.
"""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any

import feedparser
import httpx

from app.core.collectors.base import BaseCollector, CollectorResult
from app.core.collectors.web_search_collector import _search

logger = logging.getLogger(__name__)

NEWS_TIMEOUT_S = 8.0
MAX_HEADLINES = 24
_MAX_PER_QUERY = 12

# ── The 4 search queries fired for every vendor ──────────────────────────────
# Term groups for the three Google News RSS queries (vendor name is prepended,
# quoted, at build time). Kept verbatim per the stakeholder-owned spec.
_GOOGLE_NEWS_TERMS = (
    "fraud OR scam OR cheating OR ED OR CBI OR SEBI",
    "money laundering OR default OR insolvency OR arrested",
    "PMLA OR SFIO OR NCB OR chargesheet",
)
# The single DuckDuckGo (Serper-or-DDG) adverse-media query.
_DDG_TERMS = "fraud OR scam OR SEBI OR ED OR arrested India"


def _google_news_rss_url(vendor_name: str, terms: str) -> str:
    q = f'"{vendor_name.strip()}" {terms}'
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    )


def _ddg_query(vendor_name: str) -> str:
    return f'"{vendor_name.strip()}" {_DDG_TERMS}'


def _google_web_search_url(vendor_name: str, gst: str) -> str:
    """
    Build a live Google query in the requested open-search format:
    "[Name/ID]" AND (fraud OR "adverse news" OR "legal" OR "investigation").
    """
    name = vendor_name.strip()
    gstin = (gst or "").strip().upper()
    entity = f"{name} {gstin}".strip() if gstin else name
    q = f'"{entity}" AND (fraud OR "adverse news" OR legal OR investigation)'
    return "https://www.google.com/search?" + urllib.parse.urlencode({"q": q})


def _parse_feed_bytes(content: bytes) -> list[dict[str, Any]]:
    parsed = feedparser.parse(content)
    out: list[dict[str, Any]] = []
    for entry in getattr(parsed, "entries", []) or []:
        title = (entry.get("title") or "").strip()
        link = (entry.get("link") or "").strip()
        published = (entry.get("published") or entry.get("updated") or "").strip()
        source = ""
        if entry.get("source") and hasattr(entry["source"], "get"):
            source = str(entry["source"].get("title") or "")
        if title or link:
            out.append(
                {
                    "title": title,
                    "link": link,
                    "published": published,
                    "source": source,
                }
            )
        if len(out) >= _MAX_PER_QUERY:
            break
    return out


def _merge_headlines(
    dest: list[dict[str, Any]],
    seen_links: set[str],
    seen_titles: set[str],
    rows: list[dict[str, Any]],
) -> None:
    """Append rows that aren't already present (dedupe by link, then title)."""
    for row in rows:
        link = (row.get("link") or "").strip()
        title_key = (row.get("title") or "").strip().lower()
        if link and link in seen_links:
            continue
        if not link and title_key and title_key in seen_titles:
            continue
        dest.append(row)
        if link:
            seen_links.add(link)
        if title_key:
            seen_titles.add(title_key)


class NewsCollector(BaseCollector):
    name = "news"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        t0 = time.monotonic()
        entity_search_link = _google_web_search_url(vendor_name, gst)
        rss_urls = [_google_news_rss_url(vendor_name, terms) for terms in _GOOGLE_NEWS_TERMS]
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PaytmVRA/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }

        headlines: list[dict[str, Any]] = []
        seen_links: set[str] = set()
        seen_titles: set[str] = set()
        errors: list[str] = []

        # ── Google News #1–#3: fetch the three RSS feeds concurrently ─────────
        try:
            async with httpx.AsyncClient(
                timeout=NEWS_TIMEOUT_S, follow_redirects=True, verify=False
            ) as client:
                responses = await asyncio.gather(
                    *(client.get(u, headers=headers) for u in rss_urls),
                    return_exceptions=True,
                )
        except Exception as exc:  # pragma: no cover - client construction failure
            responses = [exc] * len(rss_urls)

        for url, resp in zip(rss_urls, responses):
            if isinstance(resp, BaseException):
                errors.append(f"RSS error ({url}): {resp}")
                logger.info("News RSS failed: %s", resp)
                continue
            if resp.status_code >= 400:
                errors.append(f"HTTP {resp.status_code} ({url})")
                continue
            rows = await asyncio.to_thread(_parse_feed_bytes, resp.content)
            _merge_headlines(headlines, seen_links, seen_titles, rows)

        # ── DuckDuckGo #1: shared Serper→DDG dispatcher ───────────────────────
        try:
            ddg_rows = await _search(_ddg_query(vendor_name), _MAX_PER_QUERY)
            mapped = [
                {
                    "title": r.get("title", ""),
                    "link": r.get("url", ""),
                    "published": r.get("date", ""),
                    "source": r.get("source", ""),
                }
                for r in ddg_rows
                if r.get("title") or r.get("url")
            ]
            _merge_headlines(headlines, seen_links, seen_titles, mapped)
        except Exception as exc:
            errors.append(f"DDG adverse query failed: {exc}")
            logger.info("News DDG query failed: %s", exc)

        headlines = headlines[:MAX_HEADLINES]
        ms = int((time.monotonic() - t0) * 1000)

        if headlines:
            status = "ok"
        elif errors:
            status = "failed"
        else:
            status = "partial"

        return CollectorResult(
            name=self.name,
            status=status,
            data={
                "headlines": headlines,
                "rss_urls": rss_urls,
                "ddg_query": _ddg_query(vendor_name),
                "entity_google_search_hyperlink": entity_search_link,
            },
            sources=rss_urls,
            duration_ms=ms,
            errors=errors if not headlines else [],
        )
