"""GSTIN lookup via GSTN public services API."""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from app.core.collectors.base import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)

GST_TIMEOUT_S = 5.0
# Public read-only taxpayer lookup (no API key). Response shape may change.
GST_TAXPAYER_URL = "https://services.gst.gov.in/services/api/search/taxpayerDetails"


class GstLookup(BaseCollector):
    name = "gst"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        t0 = time.monotonic()
        gstin = (gst or "").strip().upper()
        if not gstin:
            return CollectorResult(
                name=self.name,
                status="skipped",
                data={},
                errors=[],
                duration_ms=0,
            )
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; PaytmVRA/1.0; +https://paytm.com)",
            "Accept": "application/json, text/plain, */*",
        }
        try:
            async with httpx.AsyncClient(timeout=GST_TIMEOUT_S, follow_redirects=True) as client:
                resp = await client.get(
                    GST_TAXPAYER_URL,
                    params={"gstin": gstin},
                    headers=headers,
                )
        except Exception as exc:
            ms = int((time.monotonic() - t0) * 1000)
            logger.info("GST lookup network error: %s", exc)
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[str(exc)],
                duration_ms=ms,
            )

        ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code >= 400:
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[f"HTTP {resp.status_code}"],
                duration_ms=ms,
            )

        try:
            payload = resp.json()
        except Exception as exc:
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[f"Invalid JSON: {exc}"],
                duration_ms=ms,
            )

        if not isinstance(payload, dict):
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=["Unexpected response type"],
                duration_ms=ms,
            )

        err = str(payload.get("errorCode") or payload.get("error_code") or "")
        if err and err not in ("0", "null", ""):
            msg = payload.get("message") or payload.get("errorMessage") or "GST API error"
            return CollectorResult(
                name=self.name,
                status="failed",
                errors=[str(msg)],
                duration_ms=ms,
                sources=[GST_TAXPAYER_URL],
            )

        det = payload.get("gstinDetl") or payload.get("gstinDetails") or payload.get("result")
        normalized = _normalize_gst_payload(det if isinstance(det, dict) else payload)

        if not normalized:
            return CollectorResult(
                name=self.name,
                status="partial",
                data={"raw_keys": list(payload.keys())[:40]},
                errors=["Could not parse taxpayer fields from response"],
                duration_ms=ms,
                sources=[GST_TAXPAYER_URL],
            )

        normalized["gstin"] = gstin
        return CollectorResult(
            name=self.name,
            status="ok",
            data=normalized,
            sources=[GST_TAXPAYER_URL],
            duration_ms=ms,
        )


def _normalize_gst_payload(raw: dict[str, Any]) -> dict[str, Any]:
    """Map varying GST JSON keys to a stable evidence dict."""
    if not raw:
        return {}

    def pick(*keys: str) -> Any:
        for k in keys:
            if k in raw and raw[k] not in (None, "", []):
                return raw[k]
        return None

    legal = pick("lgnm", "legalName", "legal_name", "tradeNam", "tradeName")
    trade = pick("tradeNam", "tradeName", "trade_name")
    reg = pick("rgdt", "registrationDate", "registration_date", "regDate")
    state_jur = pick("stj", "stateJurisdiction", "state_jurisdiction", "stjCd")
    status = pick("sts", "status", "gstinStatus")
    btype = pick("ctb", "businessType", "business_type", "nba", "constitutionOfBusiness")
    addr = pick("pradr", "address", "addr")
    if isinstance(addr, dict):
        addr_out = addr.get("addr") or addr.get("fullAddress") or addr
    else:
        addr_out = addr

    out: dict[str, Any] = {}
    if legal:
        out["legal_name"] = str(legal)
    if trade:
        out["trade_name"] = str(trade)
    if reg:
        out["registration_date"] = str(reg)
    if state_jur:
        out["state_jurisdiction"] = str(state_jur)
    if status:
        out["gst_status"] = str(status)
    if btype:
        out["business_type"] = str(btype)
    if addr_out:
        out["address"] = str(addr_out)
    return out
