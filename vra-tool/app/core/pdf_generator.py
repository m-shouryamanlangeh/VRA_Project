"""Render the VRA adverse-media screening result as an A4 PDF (ReportLab).

The report answers one question for vendor onboarding: does this vendor have
negative news / adverse findings online, and how serious are they? It shows an
overall severity and a single table of negative findings (HIGH / MEDIUM / LOW)
with sources — nothing else. The reduction from the full structured report is
done once in :mod:`app.core.screening` and shared with the JSON API.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.config import BASE_DIR
from app.core.screening import screening_summary
from app.core.timeutil import utcnow
from app.schemas import VRAReport

logger = logging.getLogger(__name__)

# ── brand colours ──────────────────────────────────────────────────────────────
PAYTM_BLUE = colors.HexColor("#00BAF2")
PAYTM_DARK = colors.HexColor("#002970")
SEV_HIGH = colors.HexColor("#991b1b")
SEV_MED = colors.HexColor("#854d0e")
SEV_LOW = colors.HexColor("#166534")
SEV_NONE = colors.HexColor("#166534")
BADGE_RED_BG = colors.HexColor("#fee2e2")
BADGE_AMB_BG = colors.HexColor("#fef9c3")
BADGE_GRN_BG = colors.HexColor("#dcfce7")
MUTED = colors.HexColor("#64748b")


def slugify_vendor(name: str) -> str:
    raw = re.sub(r"[^\w\s\-]", "", name, flags=re.UNICODE)
    raw = re.sub(r"[\s\-]+", "_", raw.strip()).strip("_")
    return (raw[:80] or "vendor").lower()


def _sev_color(sev: str) -> colors.Color:
    return {
        "HIGH": SEV_HIGH,
        "MEDIUM": SEV_MED,
        "LOW": SEV_LOW,
        "NONE": SEV_NONE,
    }.get((sev or "").upper(), SEV_LOW)


def _color_to_html_hex(c: colors.Color) -> str:
    """``Color.hexval()`` is ``0xRRGGBB``; ReportLab ``<font color>`` needs ``#RRGGBB``."""
    return "#" + c.hexval()[2:]


def _xml_text(value: str) -> str:
    """Safe text for ReportLab ``Paragraph`` (subset of HTML).

    Also normalises the Rupee sign which ReportLab's default fonts cannot render.
    """
    from xml.sax.saxutils import escape

    v = (value or "").replace("₹", "Rs.")  # ₹ — ReportLab default fonts can't render it
    return escape(v, entities={'"': "&quot;", "'": "&#39;"})


def _overall_badge_colors(overall: str) -> tuple[colors.Color, colors.Color]:
    """(bg, fg) for the overall-severity badge."""
    u = (overall or "").upper()
    if u == "HIGH":
        return BADGE_RED_BG, SEV_HIGH
    if u == "MEDIUM":
        return BADGE_AMB_BG, SEV_MED
    return BADGE_GRN_BG, SEV_LOW  # LOW and NONE both read as the calm/green band


def _page_frame(canvas, doc, *, report_date: str, generated_at: str) -> None:
    """Confidential header + generated-by footer on every page."""
    page_w, page_h = A4
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawCentredString(
        page_w / 2,
        page_h - 10 * mm,
        f"CONFIDENTIAL – Vendor Adverse-Media Screening  |  Date of search: {report_date}",
    )
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(PAYTM_DARK)
    canvas.drawCentredString(
        page_w / 2,
        12 * mm,
        f"Generated via Paytm VRA Tool  |  {generated_at}  |  Page {doc.page}",
    )
    canvas.restoreState()


def _build_styles() -> dict[str, ParagraphStyle]:
    getSampleStyleSheet()  # ensure default fonts registered
    return {
        "band_left": ParagraphStyle(
            "band_left", fontSize=15, textColor=colors.white, alignment=TA_LEFT,
            fontName="Helvetica-Bold", leading=18,
        ),
        "band_right": ParagraphStyle(
            "band_right", fontSize=10.5, textColor=colors.white, alignment=TA_RIGHT,
            fontName="Helvetica", leading=18,
        ),
        "vendor_name": ParagraphStyle(
            "vendor_name", fontSize=19, textColor=PAYTM_DARK, alignment=TA_CENTER,
            fontName="Helvetica-Bold", leading=23,
        ),
        "meta": ParagraphStyle(
            "meta", fontSize=9.5, textColor=MUTED, alignment=TA_CENTER, leading=13,
        ),
        "counts": ParagraphStyle(
            "counts", fontSize=10.5, textColor=PAYTM_DARK, alignment=TA_CENTER, leading=14,
        ),
        "h2": ParagraphStyle(
            "h2", fontSize=12.5, textColor=PAYTM_DARK, spaceAfter=3,
            fontName="Helvetica-Bold", leading=15,
        ),
        "body": ParagraphStyle(
            "body", fontSize=8.5, textColor=MUTED, spaceAfter=2, leading=12,
        ),
        "table_cell": ParagraphStyle(
            "table_cell", fontSize=8.5, textColor=PAYTM_DARK, leading=12,
        ),
        "sev_cell": ParagraphStyle(
            "sev_cell", fontSize=9, alignment=TA_CENTER, fontName="Helvetica-Bold", leading=12,
        ),
        "confidential": ParagraphStyle(
            "confidential", fontSize=7.5, textColor=MUTED,
            alignment=TA_CENTER, spaceBefore=12,
        ),
        "empty": ParagraphStyle(
            "empty", fontSize=11.5, textColor=colors.HexColor("#166534"),
            alignment=TA_CENTER, spaceBefore=8, leading=16,
        ),
    }


def _table_style_cmds() -> list:
    """Base table commands (extended per-row for severity chips)."""
    return [
        ("BACKGROUND", (0, 0), (-1, 0), PAYTM_DARK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, PAYTM_DARK),
        ("LINEBELOW", (0, 1), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]


def render_vra_pdf(report: VRAReport, seq: int, vendor_display_name: str) -> Path:
    """Render the screening report to ``output/VRA_{seq}_{slug}.pdf``."""
    out_dir = BASE_DIR / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"VRA_{seq}_{slugify_vendor(vendor_display_name)}.pdf"
    out_path = out_dir / filename

    generated_at = utcnow().strftime("%Y-%m-%d %H:%M UTC")
    s = _build_styles()
    sc = screening_summary(report)
    overall = sc["overall"]
    counts = sc["counts"]
    findings = sc["findings"]
    vendor_name = sc["vendor_name"] or vendor_display_name

    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=16 * mm,
        rightMargin=16 * mm,
        topMargin=18 * mm,
        bottomMargin=22 * mm,
        title=f"Adverse-Media Screening — {vendor_display_name}",
    )

    story: list[Any] = []

    # ── Branded header band ──────────────────────────────────────────────────
    band = Table(
        [[
            Paragraph("Paytm", s["band_left"]),
            Paragraph("Vendor Adverse-Media Screening", s["band_right"]),
        ]],
        colWidths=[doc.width * 0.4, doc.width * 0.6],
    )
    band.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), PAYTM_BLUE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 12),
        ("RIGHTPADDING", (-1, 0), (-1, 0), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 9),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
    ]))
    story.append(band)
    story.append(Spacer(1, 8 * mm))

    # ── Vendor + date ──────────────────────────────────────────────────────
    story.append(Paragraph(_xml_text(vendor_name), s["vendor_name"]))
    story.append(Spacer(1, 1.5 * mm))
    story.append(Paragraph(
        f"Date of search: {_xml_text(str(sc['date_of_search'] or ''))}", s["meta"]))
    story.append(Spacer(1, 7 * mm))

    # ── Overall badge ──────────────────────────────────────────────────────
    badge_bg, badge_fg = _overall_badge_colors(overall)
    badge_text = "NO ADVERSE FINDINGS" if overall == "NONE" else f"OVERALL RISK:  {overall}"
    badge_tbl = Table(
        [[Paragraph(
            badge_text,
            ParagraphStyle("b", fontSize=12, textColor=badge_fg, alignment=TA_CENTER,
                           fontName="Helvetica-Bold", leading=15),
        )]],
        colWidths=[78 * mm],
    )
    badge_tbl.hAlign = "CENTER"
    badge_tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), badge_bg),
        ("ROUNDEDCORNERS", [6]),
        ("LINEBELOW", (0, 0), (-1, -1), 1.2, badge_fg),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(badge_tbl)
    story.append(Spacer(1, 3 * mm))

    total = sc["total"]
    plural = "finding" if total == 1 else "findings"
    story.append(Paragraph(
        f"{total} negative {plural} &nbsp;&nbsp;|&nbsp;&nbsp; "
        f'<font color="{_color_to_html_hex(SEV_HIGH)}"><b>HIGH {counts["HIGH"]}</b></font> &nbsp;&nbsp; '
        f'<font color="{_color_to_html_hex(SEV_MED)}"><b>MEDIUM {counts["MEDIUM"]}</b></font> &nbsp;&nbsp; '
        f'<font color="{_color_to_html_hex(SEV_LOW)}"><b>LOW {counts["LOW"]}</b></font>',
        s["counts"],
    ))
    story.append(Spacer(1, 7 * mm))

    # ── Findings table (or clean message) ─────────────────────────────────
    story.append(Paragraph("Negative News &amp; Findings", s["h2"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=PAYTM_BLUE))
    story.append(Spacer(1, 3 * mm))

    if not findings:
        story.append(Paragraph(
            f"No adverse media or negative findings surfaced for "
            f"<b>{_xml_text(vendor_name)}</b> in OSINT screening on this date.",
            s["empty"],
        ))
        story.append(Spacer(1, 2 * mm))
        story.append(Paragraph(
            "<i>This reflects open-source signal only on the date of search; it is "
            "not a clearance. Re-screen periodically and on any material change.</i>",
            s["body"],
        ))
    else:
        rows: list[list[Any]] = [[
            Paragraph('<font color="#ffffff"><b>Severity</b></font>', s["table_cell"]),
            Paragraph('<font color="#ffffff"><b>Negative news / finding</b></font>', s["table_cell"]),
            Paragraph('<font color="#ffffff"><b>Source</b></font>', s["table_cell"]),
        ]]
        sev_tint = {"HIGH": BADGE_RED_BG, "MEDIUM": BADGE_AMB_BG, "LOW": BADGE_GRN_BG}
        style_cmds = _table_style_cmds()
        for ri, f in enumerate(findings, start=1):
            sev = f["severity"]
            sev_hex = _color_to_html_hex(_sev_color(sev))
            title = (f.get("title") or "")[:500]
            category = f.get("category") or ""
            cat_prefix = (
                f'<font color="#64748b"><b>{_xml_text(category)}</b></font><br/>' if category else ""
            )
            src = f.get("source") or ""
            src_label = f.get("source_label") or "link"
            src_cell = (
                f'<a href="{src}" color="#00BAF2">{_xml_text(src_label)}</a>' if src else "—"
            )
            rows.append([
                Paragraph(f'<font color="{sev_hex}">{sev}</font>', s["sev_cell"]),
                Paragraph(cat_prefix + _xml_text(title), s["table_cell"]),
                Paragraph(src_cell, s["table_cell"]),
            ])
            # Tinted severity chip for this row.
            style_cmds.append(("BACKGROUND", (0, ri), (0, ri), sev_tint.get(sev, BADGE_GRN_BG)))
            style_cmds.append(("VALIGN", (0, ri), (0, ri), "MIDDLE"))
        tbl = Table(rows, colWidths=[20 * mm, None, 28 * mm], repeatRows=1)
        tbl.setStyle(TableStyle(style_cmds))
        story.append(tbl)

    # ── Disclaimer ───────────────────────────────────────────────────────────
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        "<i>Disclaimer: Generated from open-source intelligence (news/RSS and web search). "
        "It surfaces publicly reported negative signal only and does not replace statutory "
        "filings, credit-bureau data, sanctions/PEP screening, or legal advice. "
        "CONFIDENTIAL — internal use only.</i>",
        s["body"],
    ))
    story.append(Paragraph("CONFIDENTIAL — INTERNAL USE ONLY", s["confidential"]))

    def _draw(canvas: Any, doc: Any) -> None:
        _page_frame(canvas, doc, report_date=str(sc["date_of_search"] or ""), generated_at=generated_at)

    doc.build(story, onFirstPage=_draw, onLaterPages=_draw)
    logger.info("Wrote PDF %s", out_path)
    return out_path
