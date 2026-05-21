"""MCA company / director lookup.

The MCA portal master-data endpoint is protected by CAPTCHA for bulk access.
A production integration should use MCA21 v3 APIs or a licensed data provider.

This collector is intentionally a stub: it returns ``skipped`` until a supported
integration is wired in.
"""

from __future__ import annotations

import time

from app.core.collectors.base import BaseCollector, CollectorResult

MCA_MASTER_DATA_URL = "https://www.mca.gov.in/mcafoportal/companyLLPMasterData.do"


class McaCollector(BaseCollector):
    name = "mca"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        t0 = time.monotonic()
        ms = int((time.monotonic() - t0) * 1000)
        return CollectorResult(
            name=self.name,
            status="skipped",
            data={},
            errors=[
                "MCA portal requires CAPTCHA / MCA21 v3 or paid CIN lookup — not implemented. "
                f"Reference: {MCA_MASTER_DATA_URL}",
            ],
            duration_ms=ms,
            sources=[MCA_MASTER_DATA_URL],
        )
