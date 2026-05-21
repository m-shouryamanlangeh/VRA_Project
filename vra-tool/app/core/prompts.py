"""Load and format stakeholder prompt templates under ``app/prompts``."""

from __future__ import annotations

from app.config import BASE_DIR
from app.core.collectors.orchestrator import EvidencePack
from app.core.hybrid_report import compact_evidence_json

PROMPTS_DIR = BASE_DIR / "app" / "prompts"

# Appended in code (not in stakeholder ``vra_full.txt``): search-grounded calls cannot
# use API JSON schema, so models often invent alternate root keys unless reminded.
_VRA_REPORT_JSON_ROOT_CONTRACT = """

CRITICAL — ROOT JSON STRUCTURE:
Return one JSON object whose top-level keys use EXACTLY these names (do not wrap under alternate roots like "vendor_assessment"):

- "vendor": object with at least "name", "gst", "org_type"
- "date_of_search": string (use the Date of Search from the prompt)
- "executive_summary": object (e.g. risk_level or risk_rating, short narrative fields)
- "company_profile", "management", "credit_ratings", "financial_soundness", "borrowings",
  "funds_raised", "mca_filings", "defaults", "litigations", "statutory_compliance":
  each an ARRAY of finding objects with "point", "source" (valid HTTPS URL), "severity" (HIGH|MEDIUM|LOW|INFO)
- "adverse_media", "fraud_aml": ARRAY of objects with "entity", "search_hyperlink" (HTTPS URL), "summary", "severity"
- "connected_entities": ARRAY of objects
- "recommendation": exactly one string: PROCEED | CONDITIONAL | REJECT

Use [] for empty sections. Do not use a root object whose only key is "vendor_assessment".

CRITICAL — ENTITY IDENTITY:
Do not associate the vendor with unrelated scandals, groups, or enforcement actions based on name similarity alone.
Never invent CIN, directors, addresses, dates, or investigations. Each "point" must be directly supportable from your search results with the `source` URL pointing to that material (not a generic ministry home page).
If OSINT is ambiguous, use cautious wording and recommend manual MCA/GST/court verification.
"""


def load_prompt_template(filename: str) -> str:
    """Load a UTF-8 prompt file from ``app/prompts``."""
    path = PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def gst_for_prompt(gst: str) -> str:
    """GSTIN as given, or a clear hint that OSINT uses vendor name / web search only."""
    g = (gst or "").strip().upper()
    if g:
        return g
    return "not provided — use vendor name and public web/news evidence only"


def _fill_vendor_fields(
    template: str,
    vendor_name: str,
    gst: str,
    org_type: str,
    date: str,
) -> str:
    """
    Substitute only the four vendor fields.

    Uses sequential ``.replace`` instead of ``str.format`` so literals such as
    ``{name}`` in ``adverse_media.txt`` (meant for the LLM, not Python) are
    left unchanged.
    """
    return (
        template.replace("{vendor_name}", vendor_name)
        .replace("{gst}", gst)
        .replace("{org_type}", org_type)
        .replace("{date}", date)
    )


def format_vra_full_prompt(vendor_name: str, gst: str, org_type: str, date: str) -> str:
    """Fill ``vra_full.txt`` placeholders plus a strict root-key contract for JSON output."""
    tpl = load_prompt_template("vra_full.txt")
    return (
        _fill_vendor_fields(tpl, vendor_name, gst_for_prompt(gst), org_type, date)
        + _VRA_REPORT_JSON_ROOT_CONTRACT
    )


def format_adverse_media_prompt(vendor_name: str, gst: str, org_type: str, date: str) -> str:
    """Fill ``adverse_media.txt`` placeholders."""
    tpl = load_prompt_template("adverse_media.txt")
    return _fill_vendor_fields(tpl, vendor_name, gst_for_prompt(gst), org_type, date)


def format_synthesis_prompt(
    vendor_name: str,
    gst: str,
    org_type: str,
    evidence: EvidencePack,
) -> str:
    """Fill ``synthesis.txt`` with a compact JSON evidence pack."""
    tpl = load_prompt_template("synthesis.txt")
    evidence_json = compact_evidence_json(evidence)
    return (
        _fill_vendor_fields(tpl, vendor_name, gst_for_prompt(gst), org_type, "")
        .replace("{evidence_json}", evidence_json)
    )
