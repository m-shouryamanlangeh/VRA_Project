"""Evidence orchestration tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.core.collectors.base import CollectorResult
from app.core.collectors.orchestrator import gather_evidence


@pytest.mark.asyncio
async def test_gather_evidence_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom_collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        raise RuntimeError("network down")

    from app.core.collectors import gst_lookup as gl
    from app.core.collectors import mca_collector as mc
    from app.core.collectors import news_collector as nc

    monkeypatch.setattr(gl.GstLookup, "collect", boom_collect)
    monkeypatch.setattr(mc.McaCollector, "collect", AsyncMock(return_value=CollectorResult(name="mca", status="skipped")))
    monkeypatch.setattr(
        nc.NewsCollector,
        "collect",
        AsyncMock(
            return_value=CollectorResult(
                name="news",
                status="ok",
                data={"headlines": [], "entity_google_search_hyperlink": "https://www.google.com/search?q=x"},
            )
        ),
    )

    pack = await gather_evidence("VENDOR", "27ADKFS8129B1ZY", "Partnership")
    assert pack.collector_status["gst"] == "failed"
    assert pack.gst_data is None


@pytest.mark.asyncio
async def test_gather_evidence_skips_gst_when_blank() -> None:
    pack = await gather_evidence("ACME India", "", "LLP")
    assert pack.collector_status["gst"] == "skipped"
    assert pack.gst_data is None
    assert pack.vendor["gst"] == ""
