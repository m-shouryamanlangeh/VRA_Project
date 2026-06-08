"""Hybrid-mode public-source collectors."""

from app.core.collectors.base import BaseCollector, CollectorResult
from app.core.collectors.gst_lookup import GstLookup
from app.core.collectors.mca_collector import McaCollector
from app.core.collectors.news_collector import NewsCollector
from app.core.collectors.orchestrator import EvidencePack, evidence_pack_as_dict, gather_evidence
from app.core.collectors.web_search_collector import WebSearchCollector

__all__ = [
    "BaseCollector",
    "CollectorResult",
    "EvidencePack",
    "GstLookup",
    "McaCollector",
    "NewsCollector",
    "WebSearchCollector",
    "evidence_pack_as_dict",
    "gather_evidence",
]
