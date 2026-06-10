"""Unit tests for open-search link/query formatting in NewsCollector."""

from __future__ import annotations

from app.core.collectors.news_collector import (
    _GOOGLE_NEWS_TERMS,
    _ddg_query,
    _google_news_rss_url,
    _google_web_search_url,
)


def test_google_web_search_url_matches_requested_open_search_format() -> None:
    url = _google_web_search_url("M/s. Saradha Constructions Company Pvt. Ltd.", "")
    assert "google.com/search" in url
    assert "%22M%2Fs.+Saradha+Constructions+Company+Pvt.+Ltd.%22" in url
    assert "AND+%28fraud+OR+%22adverse+news%22+OR+legal+OR+investigation%29" in url


def test_google_web_search_url_includes_gst_when_available() -> None:
    url = _google_web_search_url("Acme Industries", "27AAAAA0000A1Z5")
    assert "%22Acme+Industries+27AAAAA0000A1Z5%22" in url


def test_google_news_fires_three_spec_queries() -> None:
    """The news brain fires exactly the 3 Google News term groups from the spec."""
    assert len(_GOOGLE_NEWS_TERMS) == 3
    urls = [_google_news_rss_url("Some Vendor", terms) for terms in _GOOGLE_NEWS_TERMS]
    joined = " ".join(urls)
    # Vendor name is quoted in every query.
    for url in urls:
        assert "%22Some+Vendor%22" in url
        assert "news.google.com/rss/search" in url
    # Spec keyword groups are present across the three queries.
    assert "CBI" in joined and "SEBI" in joined and "cheating" in joined
    assert "money+laundering" in joined and "insolvency" in joined and "arrested" in joined
    assert "PMLA" in joined and "SFIO" in joined and "NCB" in joined and "chargesheet" in joined


def test_ddg_query_matches_spec() -> None:
    assert _ddg_query("Some Vendor") == (
        '"Some Vendor" fraud OR scam OR SEBI OR ED OR arrested India'
    )
