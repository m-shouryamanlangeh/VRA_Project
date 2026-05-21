"""Collector unit tests with mocked HTTP where needed."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.collectors.blacklist_registry import (
    BLACKLISTS,
    _match_against_entries,
    check_blacklists,
)
from app.core.collectors.gst_lookup import GstLookup
from app.core.collectors.mca_collector import McaCollector
from app.core.collectors.news_collector import NewsCollector, _google_news_rss_url


@pytest.mark.asyncio
async def test_gst_lookup_maps_response() -> None:
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json = lambda: {
        "errorCode": "0",
        "gstinDetl": {
            "lgnm": "SHARP PENCIL PRODUCTIONS",
            "tradeNam": "SHARP PENCIL",
            "sts": "Active",
            "rgdt": "01/01/2020",
        },
    }
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.collectors.gst_lookup.httpx.AsyncClient", return_value=mock_client):
        r = await GstLookup().collect("SHARP PENCIL PRODUCTIONS", "27ADKFS8129B1ZY", "Partnership")

    assert r.status == "ok"
    assert r.data.get("legal_name") == "SHARP PENCIL PRODUCTIONS"
    assert r.data.get("gst_status") == "Active"


@pytest.mark.asyncio
async def test_gst_lookup_skips_when_no_gstin() -> None:
    r = await GstLookup().collect("ACME", "", "LLP")
    assert r.status == "skipped"


def test_google_news_rss_adds_india_when_name_only() -> None:
    u = _google_news_rss_url("Some Vendor", name_only_osint=True)
    assert "India" in u
    u2 = _google_news_rss_url("Some Vendor", name_only_osint=False)
    assert "India" not in u2


@pytest.mark.asyncio
async def test_mca_collector_skipped() -> None:
    r = await McaCollector().collect("X", "27ADKFS8129B1ZY", "Partnership")
    assert r.status == "skipped"


@pytest.mark.asyncio
async def test_news_collector_parses_feed() -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss><channel>
      <item><title>Test fraud case</title><link>https://example.com/a</link>
      <pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item>
    </channel></rss>"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = xml
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.core.collectors.news_collector.httpx.AsyncClient", return_value=mock_client):
        r = await NewsCollector().collect("ACME LTD", "27ADKFS8129B1ZY", "Private Limited")

    assert r.status == "ok"
    assert r.data["headlines"][0]["title"] == "Test fraud case"


def test_fuzzy_match_vendor_not_substring_of_other() -> None:
    entries = ["Sharper Pencils Pvt Ltd", "Other Holdings"]
    assert _match_against_entries("SHARP PENCIL PRODUCTIONS", "27ADKFS8129B1ZY", [], entries) is False


def test_fuzzy_match_close_name() -> None:
    entries = ["Sharp Pencil Productions"]
    assert _match_against_entries("SHARP PENCIL PRODUCTIONS", "27ADKFS8129B1ZY", [], entries) is True


@pytest.mark.asyncio
async def test_check_blacklists_returns_twelve_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.collectors import blacklist_registry as br

    monkeypatch.setattr(br, "BASE_DIR", tmp_path)
    (tmp_path / "data" / "blacklists").mkdir(parents=True)
    for meta in BLACKLISTS:
        p = tmp_path / meta["local_file"]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text('{"names": ["Sharp Pencil Productions"]}', encoding="utf-8")

    rows = await check_blacklists("SHARP PENCIL PRODUCTIONS", "27ADKFS8129B1ZY", [])
    assert len(rows) == 12
    assert any(r["status"] == "YES" for r in rows)
