"""Parallel collector orchestration → ``EvidencePack``."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from typing import Any

from app.core.collectors.base import BaseCollector, CollectorResult
from app.core.collectors.gst_lookup import GstLookup
from app.core.collectors.mca_collector import McaCollector
from app.core.collectors.news_collector import NewsCollector

logger = logging.getLogger(__name__)


@dataclass
class EvidencePack:
    """Structured evidence for synthesis (serializable to JSON)."""

    vendor: dict[str, Any]
    gst_data: dict[str, Any] | None
    mca_data: dict[str, Any] | None
    news_headlines: list[dict[str, Any]]
    news_meta: dict[str, Any] = field(default_factory=dict)
    collector_status: dict[str, str] = field(default_factory=dict)
    collector_errors: dict[str, list[str]] = field(default_factory=dict)


def _normalize_result(name: str, r: BaseException | CollectorResult) -> CollectorResult:
    if isinstance(r, CollectorResult):
        return r
    logger.warning("Collector %s raised: %s", name, r)
    return CollectorResult(
        name=name,
        status="failed",
        errors=[str(r)],
    )


async def gather_evidence(vendor_name: str, gst: str, org_type: str) -> EvidencePack:
    """
    Run all collectors concurrently.

    Individual failures are captured; they do not fail the whole pack.
    """
    collectors: list[BaseCollector] = [
        GstLookup(),
        McaCollector(),
        NewsCollector(),
    ]
    results = await asyncio.gather(
        *(c.collect(vendor_name, gst, org_type) for c in collectors),
        return_exceptions=True,
    )

    by_name: dict[str, CollectorResult] = {}
    for c, raw in zip(collectors, results):
        by_name[c.name] = _normalize_result(c.name, raw)

    gst_res = by_name["gst"]
    gst_data = (
        gst_res.data if gst_res.status in ("ok", "partial") else None
    )
    if gst_res.status == "ok" and gst_data:
        gst_data = dict(gst_data)

    mca_data = by_name["mca"].data if by_name["mca"].status == "ok" else None
    if by_name["mca"].status == "skipped":
        mca_data = None

    news_headlines: list[dict[str, Any]] = []
    news_meta: dict[str, Any] = {}
    nd = by_name["news"].data
    if isinstance(nd, dict):
        news_headlines = list(nd.get("headlines") or [])
        news_meta = {
            k: v for k, v in nd.items() if k != "headlines"
        }

    collector_status = {k: v.status for k, v in by_name.items()}
    collector_errors = {k: list(v.errors) for k, v in by_name.items() if v.errors}

    gst_norm = (gst or "").strip().upper()
    return EvidencePack(
        vendor={
            "name": vendor_name.strip(),
            "gst": gst_norm,
            "org_type": org_type.strip(),
        },
        gst_data=gst_data,
        mca_data=mca_data,
        news_headlines=news_headlines,
        news_meta=news_meta,
        collector_status=collector_status,
        collector_errors=collector_errors,
    )


def evidence_pack_as_dict(pack: EvidencePack) -> dict[str, Any]:
    """JSON-serializable dict for prompts / logging."""
    return asdict(pack)
