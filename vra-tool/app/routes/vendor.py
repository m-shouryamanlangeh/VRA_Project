"""Vendor VRA generation and batch upload."""

from __future__ import annotations

import asyncio
import io
import logging
import zipfile
from pathlib import Path
from typing import Any

import openpyxl
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.config import WRITABLE_DIR
from app.core.vra_service import generate_vra_bundle
from app.database import get_db
from app.schemas import VendorGenerateRequest, VendorGenerateResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vendor"])


def _safe_pdf_name(name: str) -> str:
    base = Path(name).name
    if not base.endswith(".pdf") or not base.startswith("VRA_"):
        raise HTTPException(status_code=400, detail="Invalid PDF name")
    return base


@router.post("/generate", response_model=VendorGenerateResponse)
async def generate_report(
    body: VendorGenerateRequest,
    db: Session = Depends(get_db),
) -> VendorGenerateResponse:
    """Run full OSINT VRA pipeline and return structured JSON + PDF link."""
    try:
        report, rel, audit = await generate_vra_bundle(
            db,
            vendor_name=body.vendor_name.strip(),
            gst=body.gst,
            org_type=(body.org_type or "Unknown").strip() or "Unknown",
            request_type="SINGLE",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Unexpected /generate failure")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    pdf_url = f"/download/pdf/{Path(rel).name}"
    return VendorGenerateResponse(report=report, pdf_url=pdf_url, audit_id=audit.id)


@router.get("/download/pdf/{filename}")
def download_pdf(filename: str) -> FileResponse:
    """Download a generated PDF from ``output/`` (name validated)."""
    safe = _safe_pdf_name(filename)
    path = WRITABLE_DIR / "output" / safe
    if not path.is_file():
        raise HTTPException(status_code=404, detail="PDF not found")
    return FileResponse(path, filename=safe, media_type="application/pdf")


@router.post("/generate/batch")
async def generate_batch(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Accept an Excel file with columns ``vendor_name``, ``org_type``; ``gst`` is optional.

    Processes rows sequentially with a 2-second delay between vendors and
    returns a ZIP of all successful PDFs.
    """
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=400, detail="Upload a .xlsx file")

    raw = await file.read()
    try:
        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.active
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid Excel file: {exc}") from exc

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows_iter)
    except StopIteration:
        raise HTTPException(status_code=400, detail="Empty spreadsheet")

    headers = [str(h).strip().lower() if h is not None else "" for h in header_row]
    col_map: dict[str, int] = {h: i for i, h in enumerate(headers) if h}

    def col(name: str) -> int | None:
        for key in (name, name.replace("_", " ")):
            if key in col_map:
                return col_map[key]
        return None

    ix_v = col("vendor_name")
    ix_g = col("gst")
    ix_o = col("org_type")
    if ix_v is None or ix_o is None:
        raise HTTPException(
            status_code=400,
            detail="Excel must include columns: vendor_name, org_type (gst optional)",
        )

    vendors: list[dict[str, str]] = []
    for row in rows_iter:
        if row is None or all(c is None or str(c).strip() == "" for c in row):
            continue
        def cell(i: int) -> str:
            if i >= len(row) or row[i] is None:
                return ""
            return str(row[i]).strip()

        vendors.append(
            {
                "vendor_name": cell(ix_v),
                "gst": cell(ix_g) if ix_g is not None else "",
                "org_type": cell(ix_o),
            }
        )

    wb.close()

    if not vendors:
        raise HTTPException(status_code=400, detail="No data rows in spreadsheet")

    zip_buf = io.BytesIO()
    errors: list[dict[str, Any]] = []
    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for i, v in enumerate(vendors):
            if not v["vendor_name"]:
                errors.append({"index": i, "error": "Incomplete row (vendor_name required)"})
                continue
            try:
                req = VendorGenerateRequest(
                    vendor_name=v["vendor_name"],
                    gst=v["gst"],
                    org_type=(v["org_type"] or "").strip() or "Unknown",
                )
            except Exception as exc:
                errors.append({"index": i, "error": str(exc)})
                continue
            try:
                _report, rel, _audit = await generate_vra_bundle(
                    db,
                    vendor_name=req.vendor_name,
                    gst=req.gst,
                    org_type=req.org_type,
                    request_type="BATCH",
                )
                pdf_path = WRITABLE_DIR / rel
                if pdf_path.is_file():
                    zf.write(pdf_path, arcname=pdf_path.name)
            except Exception as exc:
                logger.warning("Batch row %s failed: %s", i, exc)
                errors.append({"index": i, "vendor": v["vendor_name"], "error": str(exc)})
            if i < len(vendors) - 1:
                await asyncio.sleep(2.0)

    zip_buf.seek(0)
    headers = {
        "Content-Disposition": 'attachment; filename="vra_batch_reports.zip"',
        "X-VRA-Batch-Errors": str(len(errors)),
    }
    return StreamingResponse(
        zip_buf,
        media_type="application/zip",
        headers=headers,
    )

