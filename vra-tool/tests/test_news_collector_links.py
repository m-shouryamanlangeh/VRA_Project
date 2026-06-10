"""Unit tests for open-search link/query formatting in NewsCollector."""

from __future__ import annotations

from app.core.collectors.news_collector import (
    _GOOGLE_NEWS_TERMS,
    _ddg_query,
    _google_news_rss_url,
    _google_web_search_url,
    _the420_query,
)


def test_google_web_search_url_matches_requested_open_search_format() -> None:
    url = _google_web_search_url("M/s. Saradha Constructions Company Pvt. Ltd.", "")
    assert "google.com/search" in url
    assert "%22M%2Fs.+Saradha+Constructions+Company+Pvt.+Ltd.%22" in url
    assert "AND+%28fraud+OR+%22adverse+news%22+OR+legal+OR+investigation%29" in url


def test_google_web_search_url_includes_gst_when_available() -> None:
    url = _google_web_search_url("Acme Industries", "27AAAAA0000A1Z5")
    assert "%22Acme+Industries+27AAAAA0000A1Z5%22" in url


def test_google_news_fires_full_adverse_battery() -> None:
    """The news brain fires the full Google News adverse-media battery.

    Covers criminal, money-laundering, central-agency, regulatory, and
    financial-distress/litigation angles so the report finds all negative news.
    """
    assert len(_GOOGLE_NEWS_TERMS) == 5
    urls = [_google_news_rss_url("Some Vendor", terms) for terms in _GOOGLE_NEWS_TERMS]
    joined = " ".join(urls)
    # Vendor name is quoted in every query.
    for url in urls:
        assert "%22Some+Vendor%22" in url
        assert "news.google.com/rss/search" in url
    # Original three groups.
    assert "CBI" in joined and "SEBI" in joined and "cheating" in joined
    assert "money+laundering" in joined and "insolvency" in joined and "arrested" in joined
    assert "PMLA" in joined and "SFIO" in joined and "NCB" in joined and "chargesheet" in joined
    # Regulatory group.
    assert "RBI+action" in joined and "SEBI+order" in joined and "debarred" in joined
    # Financial-distress / litigation group.
    assert "NPA" in joined and "SARFAESI" in joined and "NCLT" in joined
    assert "wilful+defaulter" in joined and "liquidation" in joined and "litigation" in joined


def test_ddg_query_matches_spec() -> None:
    assert _ddg_query("Some Vendor") == (
        '"Some Vendor" fraud OR scam OR SEBI OR ED OR arrested India'
    )


def test_the420_query_is_site_targeted() -> None:
    assert _the420_query("Some Vendor") == '"Some Vendor" site:the420.in'
