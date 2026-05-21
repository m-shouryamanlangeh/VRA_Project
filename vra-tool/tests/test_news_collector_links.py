"""Unit tests for open-search link/query formatting in NewsCollector."""

from __future__ import annotations

from app.core.collectors.news_collector import _google_news_rss_url, _google_web_search_url


def test_google_web_search_url_matches_requested_open_search_format() -> None:
    url = _google_web_search_url("M/s. Saradha Constructions Company Pvt. Ltd.", "")
    assert "google.com/search" in url
    assert "%22M%2Fs.+Saradha+Constructions+Company+Pvt.+Ltd.%22" in url
    assert "AND+%28fraud+OR+%22adverse+news%22+OR+legal+OR+investigation%29" in url


def test_google_web_search_url_includes_gst_when_available() -> None:
    url = _google_web_search_url("Acme Industries", "27AAAAA0000A1Z5")
    assert "%22Acme+Industries+27AAAAA0000A1Z5%22" in url


def test_google_news_rss_query_contains_extended_risk_keywords() -> None:
    url = _google_news_rss_url("Some Vendor", name_only_osint=False)
    assert "scandal" in url
    assert "default" in url
    assert "debarred" in url
    assert "money+laundering" in url
    assert "adverse+media" in url
