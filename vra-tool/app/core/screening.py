"""Adverse-media screening view over a ``VRAReport``.

The full structured report carries a lot of scaffolding (per-dimension
scorecards, NOT-ASSESSED placeholders, confidence drivers, narrative …). For
vendor onboarding the only question is: **does this vendor have negative news /
adverse findings online, and how serious are they?**

This module reduces a report to exactly that — a flat list of *negative*
findings (HIGH / MEDIUM / LOW) with their source, plus one overall severity.
A finding is negative when it carries a real severity and is not a
"nothing-retrieved" placeholder:

    severity ∈ {HIGH, MEDIUM, LOW}  AND  fact_type != NOT_ASSESSED

Both the PDF renderer and the JSON API response are built from the single
``screening_summary`` function so the two surfaces can never drift.
"""

from __future__ import annotations

from urllib.parse import urlparse

from app.schemas import VRAReport

NEGATIVE_SEVERITIES = ("HIGH", "MEDIUM", "LOW")
_SEV_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

# Dimension attribute on VRAReport → display category for the finding.
_DIMENSIONS: list[tuple[str, str]] = [
    ("company_profile", "Company Profile"),
    ("management", "Management"),
    ("credit_ratings", "Credit Ratings"),
    ("financial_soundness", "Financial Soundness"),
    ("borrowings", "Borrowings"),
    ("funds_raised", "Funds Raised"),
    ("mca_filings", "MCA Filings"),
    ("defaults", "Defaults"),
    ("litigations", "Litigations"),
    ("statutory_compliance", "Statutory Compliance"),
]

# Adverse-finding lists (entity/summary shape) → display category.
_ADVERSE: list[tuple[str, str]] = [
    ("adverse_media", "Adverse Media"),
    ("fraud_aml", "Fraud / AML"),
]


def source_label(url: str) -> str:
    """Short, readable domain label for a citation URL (``""`` if none)."""
    if not url:
        return ""
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    if host == "google.com" and "search" in url.lower():
        return "google search"
    return host


def _is_negative(severity: str | None, fact_type: str | None) -> bool:
    return (
        (severity or "").upper() in NEGATIVE_SEVERITIES
        and (fact_type or "").upper() != "NOT_ASSESSED"
    )


def extract_negative_findings(report: VRAReport) -> list[dict]:
    """Flatten every report list into negative findings, sorted by severity."""
    out: list[dict] = []

    for attr, category in _DIMENSIONS:
        for f in getattr(report, attr, None) or []:
            if _is_negative(f.severity, f.fact_type):
                out.append(
                    {
                        "severity": f.severity.upper(),
                        "category": category,
                        "title": (f.point or "").strip(),
                        "source": f.source or "",
                    }
                )

    for attr, category in _ADVERSE:
        for a in getattr(report, attr, None) or []:
            if _is_negative(a.severity, a.fact_type):
                out.append(
                    {
                        "severity": a.severity.upper(),
                        "category": category,
                        "title": (a.summary or "").strip(),
                        "source": (a.source or a.search_hyperlink or ""),
                    }
                )

    out.sort(key=lambda r: _SEV_RANK.get(r["severity"], 0), reverse=True)
    for r in out:
        r["source_label"] = source_label(r["source"])
    return out


def screening_summary(report: VRAReport) -> dict:
    """Compact adverse-media screening result for one vendor."""
    findings = extract_negative_findings(report)
    counts = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["severity"]] += 1

    if counts["HIGH"]:
        overall = "HIGH"
    elif counts["MEDIUM"]:
        overall = "MEDIUM"
    elif counts["LOW"]:
        overall = "LOW"
    else:
        overall = "NONE"

    return {
        "vendor_name": str((report.vendor or {}).get("name") or "").strip(),
        "date_of_search": report.date_of_search,
        "overall": overall,
        "counts": counts,
        "total": len(findings),
        "findings": findings,
    }
