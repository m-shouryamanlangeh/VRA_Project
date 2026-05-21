"""Render VRA report as A4 PDF using ReportLab (no system libs required)."""

from __future__ import annotations

import datetime as dt
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import WRITABLE_DIR
from app.schemas import VRAReport

logger = logging.getLogger(__name__)

# ── brand colours ──────────────────────────────────────────────────────────────
PAYTM_BLUE = colors.HexColor("#00BAF2")
PAYTM_DARK = colors.HexColor("#002970")
BG_LIGHT = colors.HexColor("#F8FAFC")
SEV_HIGH = colors.HexColor("#991b1b")
SEV_MED = colors.HexColor("#854d0e")
SEV_LOW = colors.HexColor("#166534")
SEV_INFO = colors.HexColor("#475569")
BADGE_RED_BG = colors.HexColor("#fee2e2")
BADGE_AMB_BG = colors.HexColor("#fef9c3")
BADGE_GRN_BG = colors.HexColor("#dcfce7")


# Indian state code → state name (for GSTIN forensic decode).
_GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu & Kashmir", "02": "Himachal Pradesh", "03": "Punjab",
    "04": "Chandigarh", "05": "Uttarakhand", "06": "Haryana", "07": "Delhi",
    "08": "Rajasthan", "09": "Uttar Pradesh", "10": "Bihar", "11": "Sikkim",
    "12": "Arunachal Pradesh", "13": "Nagaland", "14": "Manipur", "15": "Mizoram",
    "16": "Tripura", "17": "Meghalaya", "18": "Assam", "19": "West Bengal",
    "20": "Jharkhand", "21": "Odisha", "22": "Chhattisgarh", "23": "Madhya Pradesh",
    "24": "Gujarat", "25": "Daman & Diu", "26": "Dadra & Nagar Haveli",
    "27": "Maharashtra", "28": "Andhra Pradesh (old)", "29": "Karnataka",
    "30": "Goa", "31": "Lakshadweep", "32": "Kerala", "33": "Tamil Nadu",
    "34": "Puducherry", "35": "Andaman & Nicobar", "36": "Telangana",
    "37": "Andhra Pradesh", "38": "Ladakh", "97": "Other Territory", "99": "Centre",
}

# 4th char of embedded PAN (= GSTIN position 6) → entity type.
_PAN_ENTITY_TYPES: dict[str, str] = {
    "C": "Company",
    "P": "Individual / Proprietor",
    "H": "Hindu Undivided Family (HUF)",
    "F": "Partnership Firm / LLP",
    "A": "Association of Persons (AOP)",
    "T": "Trust",
    "B": "Body of Individuals (BOI)",
    "L": "Local Authority",
    "J": "Artificial Juridical Person",
    "G": "Government",
}


def _decode_gstin(gst: str) -> dict[str, str]:
    """Forensic decode of a 15-char GSTIN — pure local computation."""
    g = (gst or "").strip().upper()
    if len(g) != 15:
        return {}
    state_code = g[:2]
    pan = g[2:12]
    entity_letter = pan[3] if len(pan) >= 4 else ""
    return {
        "gstin": g,
        "state_code": state_code,
        "state_name": _GST_STATE_CODES.get(state_code, "Unknown / unallocated"),
        "pan": pan,
        "entity_letter": entity_letter,
        "entity_type": _PAN_ENTITY_TYPES.get(entity_letter, "Unknown"),
        "registration_seq": g[12],
        "default_z": g[13],
        "checksum": g[14],
    }


def slugify_vendor(name: str) -> str:
    raw = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    raw = re.sub(r"[\s\-]+", "_", raw.strip()).strip("_")
    return (raw[:80] or "vendor").lower()


def _sev_color(sev: str) -> colors.Color:
    return {
        "HIGH": SEV_HIGH,
        "MEDIUM": SEV_MED,
        "LOW": SEV_LOW,
        "INFO": SEV_INFO,
    }.get(sev.upper(), SEV_INFO)


def _color_to_html_hex(c: colors.Color) -> str:
    """``Color.hexval()`` is ``0xRRGGBB``; ReportLab ``<font color>`` needs ``#RRGGBB``."""
    return "#" + c.hexval()[2:]


def _xml_text(value: str) -> str:
    """Safe text for ReportLab ``Paragraph`` (subset of HTML).
    Also normalises the Rupee sign which ReportLab's default fonts cannot render.
    """
    v = (value or "").replace("\u20b9", "Rs.").replace("₹", "Rs.")
    return escape(v, entities={'"': "&quot;", "'": "&#39;"})


def _severity_rank(sev: str) -> int:
    return {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0}.get((sev or "").upper(), 0)


def _source_label(url: str) -> str:
    """Return a short, readable domain label for a citation URL.

    Examples:
      https://www.mca.gov.in/foo  → mca.gov.in
      https://google.com/search?q=… → google search
      https://suit.cibil.com/ → suit.cibil.com
    """
    if not url:
        return "src"
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return "src"
    if host.startswith("www."):
        host = host[4:]
    if not host:
        return "src"
    # Friendlier alias for the Google search fallback URL.
    if host == "google.com" and "search" in url.lower():
        return "google search"
    return host


def _extract_risk_level(raw: str) -> str:
    """Extract a clean HIGH / MEDIUM / LOW token from any risk_rating text Gemini returns."""
    u = str(raw or "").upper()
    for level in ("HIGH", "MEDIUM", "LOW"):
        if level in u:
            return level
    return "UNKNOWN"


def _report_section_map(report: VRAReport) -> list[tuple[str, list]]:
    """Returns (title_without_number, items) — numbers are added dynamically during render."""
    return [
        ("Company Profile and Business Details", report.company_profile),
        ("Management", report.management),
        ("Credit Ratings", report.credit_ratings),
        ("Financial Soundness", report.financial_soundness),
        ("Borrowings", report.borrowings),
        ("Funds Raised", report.funds_raised),
        ("MCA Filings", report.mca_filings),
        ("Defaults", report.defaults),
        ("Litigations", report.litigations),
        ("Statutory Compliance", report.statutory_compliance),
    ]


def _page_frame(canvas, doc, *, report_date: str, generated_at: str) -> None:
    """Header + footer on every page (Claude-style running header)."""
    page_w, page_h = A4
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawCentredString(
        page_w / 2,
        page_h - 10 * mm,
        f"CONFIDENTIAL – Vendor Risk Assessment  |  Date of search: {report_date}",
    )
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(PAYTM_DARK)
    canvas.drawCentredString(
        page_w / 2,
        12 * mm,
        f"Generated via Paytm VRA Tool  |  {generated_at}  |  Page {doc.page}",
    )
    canvas.restoreState()


def _risk_badge_color(rr: str) -> tuple[colors.Color, colors.Color]:
    """Return (bg, fg) for risk badge."""
    u = rr.upper()
    if "HIGH" in u or "RED" in u:
        return BADGE_RED_BG, SEV_HIGH
    if "MEDIUM" in u or "AMBER" in u:
        return BADGE_AMB_BG, SEV_MED
    return BADGE_GRN_BG, SEV_LOW


def _build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title",
            fontSize=22,
            textColor=PAYTM_BLUE,
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        ),
        "vendor_name": ParagraphStyle(
            "vendor_name",
            fontSize=18,
            textColor=PAYTM_DARK,
            spaceBefore=8,
            spaceAfter=4,
            alignment=TA_CENTER,
            fontName="Helvetica-Bold",
        ),
        "center": ParagraphStyle(
            "center",
            fontSize=10,
            textColor=PAYTM_DARK,
            alignment=TA_CENTER,
            spaceAfter=3,
        ),
        "confidential": ParagraphStyle(
            "confidential",
            fontSize=8,
            textColor=colors.HexColor("#64748b"),
            alignment=TA_CENTER,
            spaceBefore=16,
        ),
        "h2": ParagraphStyle(
            "h2",
            fontSize=13,
            textColor=PAYTM_BLUE,
            spaceBefore=10,
            spaceAfter=4,
            fontName="Helvetica-Bold",
        ),
        "h3": ParagraphStyle(
            "h3",
            fontSize=10,
            textColor=PAYTM_DARK,
            spaceBefore=6,
            spaceAfter=2,
            fontName="Helvetica-Bold",
        ),
        "body": ParagraphStyle(
            "body",
            fontSize=9.5,
            textColor=PAYTM_DARK,
            spaceAfter=2,
            leading=13,
        ),
        "finding": ParagraphStyle(
            "finding",
            fontSize=9,
            textColor=PAYTM_DARK,
            spaceAfter=2,
            leading=12,
            leftIndent=8,
        ),
        "table_cell": ParagraphStyle(
            "table_cell",
            fontSize=8.5,
            textColor=PAYTM_DARK,
            leading=11,
        ),
        "paytm_label": ParagraphStyle(
            "paytm_label",
            fontSize=11,
            textColor=PAYTM_DARK,
            alignment=TA_CENTER,
            spaceBefore=80,
            fontName="Helvetica-Bold",
        ),
    }


def _table_style(header_bg=None) -> TableStyle:
    if header_bg is None:
        header_bg = colors.HexColor("#f1f5f9")
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), header_bg),
        ("TEXTCOLOR", (0, 0), (-1, 0), PAYTM_DARK),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
    ])


def render_vra_pdf(report: VRAReport, seq: int, vendor_display_name: str) -> Path:
    """Render VRAReport to ``output/VRA_{seq}_{slug}.pdf`` and return its path."""
    out_dir = WRITABLE_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"VRA_{seq}_{slugify_vendor(vendor_display_name)}.pdf"
    out_path = out_dir / filename

    generated_at = dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    s = _build_styles()

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"VRA Report — {vendor_display_name}",
    )

    story: list[Any] = []

    # ── COVER PAGE ────────────────────────────────────────────────────────────
    story.append(Paragraph("Paytm", s["paytm_label"]))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph("Vendor Risk Assessment Report", s["title"]))
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph(report.vendor.get("name", vendor_display_name), s["vendor_name"]))
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph(
        f"GST: {report.vendor.get('gst', '—')} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f"Org: {report.vendor.get('org_type', '—')}",
        s["center"],
    ))
    story.append(Paragraph(f"Date of Search: {report.date_of_search}", s["center"]))
    story.append(Paragraph(
        "Classification: CONFIDENTIAL &nbsp;|&nbsp; OSINT-based assessment (automated tool output)",
        s["center"],
    ))
    story.append(Spacer(1, 6 * mm))

    # Risk badge
    es = report.executive_summary or {}
    rr_raw = (
        es.get("risk_rating")
        or es.get("risk_level")
        or es.get("risk")
        or "UNKNOWN"
    )
    rr = _extract_risk_level(str(rr_raw))  # always HIGH / MEDIUM / LOW / UNKNOWN

    # Override: REJECT vendor must never show below HIGH; PROCEED vendor capped at MEDIUM.
    if report.recommendation == "REJECT" and rr != "HIGH":
        rr = "HIGH"
    elif report.recommendation == "PROCEED" and rr == "HIGH":
        rr = "MEDIUM"

    badge_bg, badge_fg = _risk_badge_color(rr)
    badge_tbl = Table(
        [[Paragraph(f"Risk: {rr}", ParagraphStyle("b", fontSize=10, textColor=badge_fg,
                                                   alignment=TA_CENTER, fontName="Helvetica-Bold"))]],
        colWidths=[60 * mm],
    )
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), badge_bg),
        ("ROUNDEDCORNERS", [4]),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(badge_tbl)
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("CONFIDENTIAL — INTERNAL USE ONLY", s["confidential"]))
    story.append(PageBreak())

    # ── EXECUTIVE SUMMARY (ratings + bullets only — no prose narrative) ─────
    story.append(Paragraph("Executive Summary &amp; Overall Risk Rating", s["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
    story.append(Spacer(1, 3 * mm))

    rr_display = rr
    exec_data = [
        [Paragraph("<b>Risk Rating:</b>", s["table_cell"]), Paragraph(rr_display, s["table_cell"])],
    ]
    # Quantitative risk score (0-100) — new in calibrated rubric
    _score_raw = es.get("risk_score")
    if isinstance(_score_raw, (int, float)):
        _score_int = int(round(float(_score_raw)))
        _score_int = max(0, min(100, _score_int))
        exec_data.append([
            Paragraph("<b>Risk Score:</b>", s["table_cell"]),
            Paragraph(f"{_score_int} / 100", s["table_cell"]),
        ])
    exec_data.append([
        Paragraph("<b>Recommendation:</b>", s["table_cell"]),
        Paragraph(str(report.recommendation), s["table_cell"]),
    ])
    if es.get("confidence"):
        exec_data.append([
            Paragraph("<b>Confidence:</b>", s["table_cell"]),
            Paragraph(str(es["confidence"]), s["table_cell"]),
        ])
    # Veto trigger surfaces the auto-HIGH rule that fired (sanctions / wilful default / etc.)
    if es.get("veto_triggered"):
        _vr = str(es.get("veto_reason") or "Veto rule triggered (see findings)")
        exec_data.append([
            Paragraph("<b>Veto Trigger:</b>", s["table_cell"]),
            Paragraph(_xml_text(_vr), s["table_cell"]),
        ])
    exec_tbl = Table(exec_data, colWidths=[50 * mm, None])
    exec_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f1f5f9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(exec_tbl)
    story.append(Spacer(1, 3 * mm))

    # Accept new calibrated keys (key_risk_drivers / key_mitigants) or legacy ones.
    top_findings = es.get("key_risk_drivers") or es.get("top_findings") or []
    if top_findings:
        story.append(Paragraph("Top Risk Drivers", s["h3"]))
        for item in top_findings[:3]:
            story.append(Paragraph(f"• {_xml_text(str(item))}", s["finding"]))

    top_positives = es.get("key_mitigants") or es.get("top_positives") or []
    if top_positives:
        story.append(Paragraph("Key Mitigants", s["h3"]))
        for item in top_positives[:3]:
            story.append(Paragraph(f"• {_xml_text(str(item))}", s["finding"]))

    # Analyst narrative — try every likely key the LLM might use
    _es_narrative = None
    for _nk in ("summary", "text", "narrative", "overview", "description", "assessment", "details", "content"):
        _nv = es.get(_nk)
        if isinstance(_nv, str) and _nv.strip():
            _es_narrative = _nv.strip()
            break
    if not _es_narrative:
        for _nv in es.values():
            if isinstance(_nv, str) and len(_nv.strip()) > 60:
                _es_narrative = _nv.strip()
                break
    if _es_narrative:
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(_xml_text(_es_narrative), s["body"]))

    # ── DIMENSION SCORECARD (12 weighted dimensions from calibrated rubric) ─
    _dim = es.get("dimension_scores")
    if isinstance(_dim, dict) and _dim:
        _DIM_LABELS = [
            ("defaults",              "Defaults / Wilful Default",     15),
            ("sanctions_aml_fraud",   "Sanctions / AML / Fraud",       15),
            ("litigations",           "Litigations",                   10),
            ("statutory_compliance",  "Statutory Compliance",          10),
            ("adverse_media",         "Adverse Media",                 10),
            ("management_integrity",  "Management Integrity",          10),
            ("credit_ratings",        "Credit Ratings",                 8),
            ("borrowings",            "Borrowings / Bank Stress",       7),
            ("mca_filings",           "MCA Filings / ROC",              5),
            ("financial_soundness",   "Financial Soundness",            5),
            ("funds_raised",          "Funds Raised Quality",           3),
            ("company_profile",       "Company Profile Concerns",       2),
        ]

        def _score_band(v: int) -> str:
            if v >= 75:
                return "Severe"
            if v >= 50:
                return "Material"
            if v >= 25:
                return "Minor"
            return "Clean"

        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("Risk Dimension Scorecard (weighted)", s["h3"]))
        dim_rows: list[list[Any]] = [[
            Paragraph("<b>Dimension</b>", s["table_cell"]),
            Paragraph("<b>Weight</b>", s["table_cell"]),
            Paragraph("<b>Score</b>", s["table_cell"]),
            Paragraph("<b>Band</b>", s["table_cell"]),
        ]]
        for key, label, weight in _DIM_LABELS:
            raw = _dim.get(key)
            try:
                val = int(round(float(raw))) if raw is not None else 0
            except (TypeError, ValueError):
                val = 0
            val = max(0, min(100, val))
            dim_rows.append([
                Paragraph(_xml_text(label), s["table_cell"]),
                Paragraph(f"{weight}%", s["table_cell"]),
                Paragraph(str(val), s["table_cell"]),
                Paragraph(_score_band(val), s["table_cell"]),
            ])
        dim_tbl = Table(dim_rows, colWidths=[70 * mm, 20 * mm, 20 * mm, 30 * mm])
        dim_tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(dim_tbl)

    # ── VENDOR PROFILE (basic info, structured) ───────────────────────────
    v = report.vendor or {}
    vendor_name = str(v.get("name") or "").strip() or "—"
    org_type = str(v.get("org_type") or "").strip() or "—"
    gst_val = str(v.get("gst") or "").strip().upper()
    profile_rows = [
        [Paragraph("<b>Field</b>", s["table_cell"]), Paragraph("<b>Value</b>", s["table_cell"])],
        [Paragraph("Vendor / Entity Name", s["table_cell"]), Paragraph(_xml_text(vendor_name), s["table_cell"])],
        [Paragraph("Declared Org Type", s["table_cell"]), Paragraph(_xml_text(org_type), s["table_cell"])],
        [Paragraph("GSTIN", s["table_cell"]), Paragraph(_xml_text(gst_val or "Not provided"), s["table_cell"])],
        [Paragraph("Date of Search", s["table_cell"]), Paragraph(_xml_text(str(report.date_of_search or "")), s["table_cell"])],
    ]
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("1. Vendor Profile", s["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
    story.append(Spacer(1, 2 * mm))
    profile_tbl = Table(profile_rows, colWidths=[55 * mm, None])
    profile_tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(profile_tbl)

    # ── GSTIN forensic decode (structured like long-form VRA) ──────────────
    decoded = _decode_gstin(gst_val)
    if decoded:
        story.append(Spacer(1, 4 * mm))
        story.append(Paragraph("1A. GSTIN forensic decode", s["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
        gst_rows = [
            [Paragraph("<b>Field</b>", s["table_cell"]), Paragraph("<b>Value</b>", s["table_cell"])],
            [
                Paragraph("Full GSTIN", s["table_cell"]),
                Paragraph(_xml_text(decoded["gstin"]), s["table_cell"]),
            ],
            [
                Paragraph("State code (positions 1–2)", s["table_cell"]),
                Paragraph(
                    f'{_xml_text(decoded["state_code"])} — {_xml_text(decoded["state_name"])}',
                    s["table_cell"],
                ),
            ],
            [
                Paragraph("PAN embedded (positions 3–12)", s["table_cell"]),
                Paragraph(_xml_text(decoded["pan"]), s["table_cell"]),
            ],
            [
                Paragraph("Entity type (4th char of PAN)", s["table_cell"]),
                Paragraph(
                    f'{_xml_text(decoded["entity_letter"])} — {_xml_text(decoded["entity_type"])}',
                    s["table_cell"],
                ),
            ],
            [
                Paragraph("Registration sequence (13)", s["table_cell"]),
                Paragraph(_xml_text(decoded["registration_seq"]), s["table_cell"]),
            ],
            [
                Paragraph("Default 'Z' (14) / Checksum (15)", s["table_cell"]),
                Paragraph(
                    f'{_xml_text(decoded["default_z"])} / {_xml_text(decoded["checksum"])}',
                    s["table_cell"],
                ),
            ],
        ]
        gst_tbl = Table(gst_rows, colWidths=[55 * mm, None])
        gst_tbl.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f5f9")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ]))
        story.append(Spacer(1, 2 * mm))
        story.append(gst_tbl)
        story.append(Paragraph(
            "<i>Confirm active status and filing history on "
            '<a href="https://www.gst.gov.in/" color="#00BAF2">gst.gov.in</a>.</i>',
            s["body"],
        ))

    section_map = _report_section_map(report)

    # ── Risk scorecard (right after vendor profile — gives reviewer the overview) ──
    story.append(PageBreak())
    story.append(Paragraph("2. Risk Scorecard Summary", s["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
    story.append(Spacer(1, 2 * mm))
    sc_data: list[list[Any]] = [
        [
            Paragraph("<b>Dimension</b>", s["table_cell"]),
            Paragraph("<b>Rating</b>", s["table_cell"]),
            Paragraph("<b>Key finding from open search</b>", s["table_cell"]),
        ]
    ]
    for title, items in section_map:
        if not items:
            continue
        lead = max(items, key=lambda f: _severity_rank(f.severity))
        sev_c = _sev_color(lead.severity)
        obs = (lead.point or "")[:280]   # show the HIGHEST-risk finding, not the first
        src = lead.source or ""
        src_link = (
            f' <a href="{src}" color="#00BAF2">[{_xml_text(_source_label(src))}]</a>'
            if src else ""
        )
        sc_data.append(
            [
                Paragraph(_xml_text(title[:70]), s["table_cell"]),
                Paragraph(
                    f'<font color="{_color_to_html_hex(sev_c)}"><b>{_xml_text(lead.severity)}</b></font>',
                    s["table_cell"],
                ),
                Paragraph(_xml_text(obs) + src_link, s["table_cell"]),
            ]
        )
    # Add adverse media row if present
    for adv_title, adv_items in [("Adverse Media", report.adverse_media), ("Fraud / AML", report.fraud_aml)]:
        if adv_items:
            lead_a = max(adv_items, key=lambda a: _severity_rank(a.severity))
            sev_c = _sev_color(lead_a.severity)
            link = lead_a.search_hyperlink or ""
            src_link = (
                f' <a href="{link}" color="#00BAF2">[{_xml_text(_source_label(link))}]</a>'
                if link else ""
            )
            sc_data.append([
                Paragraph(_xml_text(adv_title), s["table_cell"]),
                Paragraph(
                    f'<font color="{_color_to_html_hex(sev_c)}"><b>{_xml_text(lead_a.severity)}</b></font>',
                    s["table_cell"],
                ),
                Paragraph(_xml_text((lead_a.summary or "")[:280]) + src_link, s["table_cell"]),
            ])
    if len(sc_data) == 1:
        sc_data.append([
            Paragraph("—", s["table_cell"]),
            Paragraph("INFO", s["table_cell"]),
            Paragraph("No findings were populated for this run.", s["table_cell"]),
        ])
    sc_tbl = Table(sc_data, colWidths=[42 * mm, 22 * mm, None])
    sc_tbl.setStyle(_table_style())
    story.append(sc_tbl)

    # ── DETAILED OPEN-SEARCH SECTIONS ────────────────────────────────────────
    detail_num = 2   # starts at 3 (2 + 1 increment below)
    for title, items in section_map:
        if not items:
            continue
        detail_num += 1
        story.append(PageBreak())
        story.append(Paragraph(f"{detail_num}. {_xml_text(title)}", s["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
        story.append(Spacer(1, 2 * mm))
        for i, f in enumerate(items, 1):
            sev_color = _sev_color(f.severity)
            src = f.source or ""
            src_part = (
                f' <a href="{src}" color="#00BAF2">[{_xml_text(_source_label(src))}]</a>'
                if src else ""
            )
            if f.severity != "INFO":
                hex_c = _color_to_html_hex(sev_color)
                sev_prefix = f'<font color="{hex_c}"><b>[{f.severity}]</b></font> '
            else:
                sev_prefix = ""
            story.append(Paragraph(
                f"{sev_prefix}{i}. {_xml_text(f.point or '')}{src_part}",
                s["finding"],
            ))

    # ── ADVERSE MEDIA & FRAUD/AML ────────────────────────────────────────────
    adv_section_num = detail_num
    for section_title, items in [
        ("Adverse Media", report.adverse_media),
        ("Fraud / AML", report.fraud_aml),
    ]:
        if not items:
            continue
        adv_section_num += 1
        story.append(PageBreak())
        story.append(Paragraph(f"{adv_section_num}. {section_title}", s["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
        story.append(Spacer(1, 2 * mm))
        tdata = [[
            Paragraph("<b>Entity</b>", s["table_cell"]),
            Paragraph("<b>Search</b>", s["table_cell"]),
            Paragraph("<b>Summary of finding</b>", s["table_cell"]),
            Paragraph("<b>Severity</b>", s["table_cell"]),
        ]]
        for a in items:
            sev_color = _sev_color(a.severity)
            link = a.search_hyperlink or ""
            src = a.source or ""
            src_display = src if src else link
            tdata.append([
                Paragraph(_xml_text(a.entity or ""), s["table_cell"]),
                Paragraph(
                    f'<a href="{link}" color="#00BAF2">Search</a>' if link else "—",
                    s["table_cell"],
                ),
                Paragraph(
                    _xml_text(a.summary or "") +
                    (
                        f' <a href="{src_display}" color="#00BAF2">[{_xml_text(_source_label(src_display))}]</a>'
                        if src_display else ""
                    ),
                    s["table_cell"],
                ),
                Paragraph(
                    f'<font color="{_color_to_html_hex(sev_color)}"><b>{_xml_text(a.severity)}</b></font>',
                    s["table_cell"],
                ),
            ])
        adv_tbl = Table(tdata, colWidths=[38 * mm, 18 * mm, None, 20 * mm])
        adv_tbl.setStyle(_table_style())
        story.append(adv_tbl)

    # ── CONNECTED ENTITIES (proper table) ────────────────────────────────────
    if report.connected_entities:
        story.append(PageBreak())
        story.append(Paragraph("Connected Entities", s["h2"]))
        story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
        story.append(Spacer(1, 2 * mm))
        ce_rows: list[list[Any]] = [[
            Paragraph("<b>Entity / Person</b>", s["table_cell"]),
            Paragraph("<b>Relationship</b>", s["table_cell"]),
            Paragraph("<b>Adverse Search</b>", s["table_cell"]),
        ]]
        for c in report.connected_entities:
            if isinstance(c, dict):
                name = _xml_text(str(c.get("name") or ""))
                rel = _xml_text(str(c.get("relationship") or ""))
                link = str(c.get("search_hyperlink") or "")
            else:
                name = _xml_text(str(c))
                rel = ""
                link = ""
            ce_rows.append([
                Paragraph(name, s["table_cell"]),
                Paragraph(rel, s["table_cell"]),
                Paragraph(
                    f'<a href="{link}" color="#00BAF2">Open Search</a>' if link else "—",
                    s["table_cell"],
                ),
            ])
        ce_tbl = Table(ce_rows, colWidths=[65 * mm, 60 * mm, None])
        ce_tbl.setStyle(_table_style())
        story.append(ce_tbl)

    # ── RECOMMENDATIONS ───────────────────────────────────────────────────────
    story.append(PageBreak())
    story.append(Paragraph("Recommendations &amp; Follow-ups", s["h2"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=PAYTM_BLUE))
    story.append(Spacer(1, 2 * mm))
    rec_list = es.get("recommendations")
    if isinstance(rec_list, list) and rec_list:
        for r in rec_list:
            if isinstance(r, str) and r.strip():
                story.append(Paragraph(f"• {_xml_text(r)}", s["finding"]))
    else:
        if report.recommendation == "REJECT":
            bullets = [
                "Do not onboard. Escalate to executive risk committee for review of material findings.",
                "Obtain enhanced KYC on all partners / UBOs and re-run sanctions screening.",
                "Require legal review of contract enforceability before any engagement.",
            ]
        elif report.recommendation == "PROCEED":
            bullets = [
                "Proceed under standard monitoring; retain all OSINT evidence for audit trail.",
                "Re-screen periodically and on any material change to vendor profile.",
            ]
        else:
            bullets = [
                "Complete enhanced due diligence: obtain latest GST return, financials, and partnership deed.",
                "Verify GSTIN active status on gst.gov.in and check for non-filing notices.",
                "Run AML / sanctions screening on all named directors, partners, and UBOs.",
                "Document risk acceptance with a named approver before contracting.",
                "Set a periodic re-screening trigger (recommended: every 6 months).",
            ]
        for b in bullets:
            story.append(Paragraph(f"• {_xml_text(b)}", s["finding"]))

    story.append(Spacer(1, 8 * mm))
    story.append(
        Paragraph(
            "<i>Disclaimer: This report is generated from OSINT and tool workflows (including optional "
            "hybrid collectors). It does not replace statutory filings, credit bureau data, or legal advice. "
            "CONFIDENTIAL — internal use only.</i>",
            s["body"],
        )
    )

    def _draw(canvas: Any, doc: Any) -> None:
        _page_frame(canvas, doc, report_date=report.date_of_search, generated_at=generated_at)

    doc.build(story, onFirstPage=_draw, onLaterPages=_draw)
    logger.info("Wrote PDF %s", out_path)
    return out_path
