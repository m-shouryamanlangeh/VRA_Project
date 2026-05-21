"""Adverse-media headlines via Google News RSS (no API key)."""

from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import Any

import feedparser
import httpx

from app.core.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

NEWS_TIMEOUT_S = 8.0
MAX_HEADLINES = 20

_RISK_TERMS = (
    "fraud OR scandal OR litigation OR default OR investigation OR "
    "debarred OR penalty OR \"money laundering\" OR \"adverse media\""
)


def _google_news_rss_url(vendor_name: str, *, name_only_osint: bool) -> str:
    vn = vendor_name.strip()
    risk = f"({_RISK_TERMS})"
    # Without a GSTIN, bias the RSS query toward entity resolution (name + India).
    q = (
        f'"{vn}" India {risk}'
        if name_only_osint
        else f'"{vn}" {risk}'
    )
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode(
        {"q": q, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"}
    )


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


def _parse_feed_bytes(content: bytes, base_url: str) -> list[dict[str, Any]]:
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
        if len(out) >= MAX_HEADLINES:
            break
    return out


class NewsCollector(BaseCollector):
    name = "news"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        t0 = time.monotonic()
        name_only = not (gst or "").strip()
        rss_url = _google_news_rss_url(vendor_name, name_only_osint=name_only)
        entity_search_link = _google_web_search_url(vendor_name, gst)
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PaytmVRA/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        try:
            async with httpx.AsyncClient(timeout=NEWS_TIMEOUT_S, follow_redirects=True) as client:
                resp = await client.get(rss_url, headers=headers)
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.info("News RSS failed: %s", exc)
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[str(exc)],
                duration_ms=ms,
                sources=[rss_url],
                data={"entity_google_search_hyperlink": entity_search_link},
            )

        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[f"HTTP {resp.status_code}"],
                duration_ms=ms,
                sources=[rss_url],
                data={"entity_google_search_hyperlink": entity_search_link},
            )

        headlines = await asyncio.to_thread(_parse_feed_bytes, resp.content, rss_url)
        return CollectorResult(
            name=self.name,
            status="ok" if headlines else "partial",
            data={
                "headlines": headlines,
                "rss_url": rss_url,
                "entity_google_search_hyperlink": entity_search_link,
            },
            sources=[rss_url],
            duration_ms=ms,
            errors=[] if headlines else ["No headlines parsed from feed"],
        )
