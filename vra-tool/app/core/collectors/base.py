"""Collector contracts for hybrid evidence gathering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class CollectorResult:
    """Outcome of a single collector run."""

    name: str  # "gst", "mca", "news", ...
    status: Literal["ok", "partial", "failed", "skipped"]
    data: dict[str, Any] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0


class BaseCollector:
    """Async evidence collector."""

    name: str = "base"

    async def collect(self, vendor_name: str, gst: str, org_type: str) -> CollectorResult:
        raise NotImplementedError
