"""Audit log HTML + JSON API + CSV export."""

from __future__ import annotations

import csv
import io
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.orm import Session

from app.core.kv_store import get_value
from app.core.timeutil import utcnow
from app.database import get_db
from app.models import AuditLog
from app.schemas import AuditListResponse

router = APIRouter(tags=["audit"])


def _audit_conditions(
    vendor: str | None,
    date_from: str | None,
    date_to: str | None,
) -> list:
    conds: list = []
    if vendor:
        conds.append(AuditLog.vendor_name.ilike(f"%{vendor}%"))
    if date_from:
        try:
            df = datetime.fromisoformat(date_from)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date_from must be ISO date") from exc
        conds.append(AuditLog.timestamp >= df)
    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date_to must be ISO date") from exc
        conds.append(AuditLog.timestamp <= dt_to)
    return conds


@router.get("/api/audit", response_model=AuditListResponse)
def api_audit_list(
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    vendor: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> AuditListResponse:
    conds = _audit_conditions(vendor, date_from, date_to)
    count_stmt = select(func.count()).select_from(AuditLog)
    for c in conds:
        count_stmt = count_stmt.where(c)
    total = db.execute(count_stmt).scalar_one()
    list_stmt = select(AuditLog)
    for c in conds:
        list_stmt = list_stmt.where(c)
    list_stmt = (
        list_stmt.order_by(AuditLog.timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = list(db.execute(list_stmt).scalars().all())
    items = [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "vendor_name": r.vendor_name,
            "gst": r.gst,
            "org_type": r.org_type,
            "request_type": r.request_type,
            "status": r.status,
            "provider_used": r.provider_used,
            "key_label_used": r.key_label_used,
            "pdf_path": r.pdf_path,
            "tokens_used": r.tokens_used,
            "error_message": r.error_message,
        }
        for r in rows
    ]
    return AuditListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/api/usage")
def api_usage(db: Session = Depends(get_db)) -> dict:
    """Token + generation usage recorded by this portal.

    IMPORTANT: Google's Gemini API does not expose a key's remaining quota or
    token balance — there is no endpoint for it; limits surface only as 429s.
    So this reports *consumption the app has logged* (from the audit trail),
    plus the configurable free-tier daily request budget for context — not a
    live balance fetched from Google.
    """
    now = utcnow()
    start_today = datetime(now.year, now.month, now.day)

    def _agg(since: datetime | None) -> tuple[int, int]:
        stmt = select(
            func.count(AuditLog.id),
            func.coalesce(func.sum(AuditLog.tokens_used), 0),
        )
        if since is not None:
            stmt = stmt.where(AuditLog.timestamp >= since)
        gens, toks = db.execute(stmt).one()
        return int(gens or 0), int(toks or 0)

    gens_today, tokens_today = _agg(start_today)
    gens_all, tokens_all = _agg(None)
    daily_limit = int(get_value(db, "daily_quota_limit", "1500"))
    return {
        "today": {
            "date": start_today.date().isoformat(),
            "generations": gens_today,
            "tokens": tokens_today,
        },
        "all_time": {"generations": gens_all, "tokens": tokens_all},
        "daily_limit": daily_limit,
        "remaining_estimate": max(0, daily_limit - gens_today),
    }


@router.get("/api/audit/export.csv")
def api_audit_export_csv(
    db: Session = Depends(get_db),
    vendor: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> Response:
    conds = _audit_conditions(vendor, date_from, date_to)
    q = select(AuditLog).order_by(AuditLog.timestamp.desc())
    for c in conds:
        q = q.where(c)
    rows = list(db.execute(q).scalars().all())
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "id",
            "timestamp",
            "vendor_name",
            "gst",
            "org_type",
            "request_type",
            "status",
            "provider_used",
            "key_label_used",
            "tokens_used",
            "pdf_path",
            "error_message",
        ]
    )
    for r in rows:
        writer.writerow(
            [
                r.id,
                r.timestamp.isoformat(),
                r.vendor_name,
                r.gst,
                r.org_type,
                r.request_type,
                r.status,
                r.provider_used or "",
                r.key_label_used or "",
                r.tokens_used or "",
                r.pdf_path or "",
                (r.error_message or "").replace("\n", " "),
            ]
        )
    return Response(
        content=buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="vra_audit.csv"'},
    )


# Repo root (…/vra-tool): routes/ → app/ → vra-tool/. PDFs live under output/.
_OUTPUT_DIR = (Path(__file__).resolve().parents[2] / "output").resolve()


def _remove_pdf(pdf_path: str | None) -> None:
    """Delete the PDF artifact for a history entry, if it sits under output/.

    Guarded against path traversal: only files resolving inside ``output/`` are
    unlinked. Missing files and any OS error are ignored — clearing history must
    never fail because a stale PDF is already gone.
    """
    if not pdf_path:
        return
    try:
        target = (_OUTPUT_DIR / Path(pdf_path).name).resolve()
        if target.parent == _OUTPUT_DIR and target.is_file():
            target.unlink()
    except OSError:
        pass


@router.delete("/api/audit/{audit_id}")
def api_audit_delete(audit_id: int, db: Session = Depends(get_db)) -> dict:
    """Delete a single search-history entry (and its PDF)."""
    row = db.get(AuditLog, audit_id)
    if row is None:
        raise HTTPException(status_code=404, detail="History entry not found")
    _remove_pdf(row.pdf_path)
    db.delete(row)
    db.commit()
    return {"ok": True, "deleted": 1}


@router.delete("/api/audit")
def api_audit_clear(
    db: Session = Depends(get_db),
    vendor: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> dict:
    """Clear search history. With no filters this wipes the whole log; the same
    vendor/date filters as the list view scope the deletion to what's shown."""
    conds = _audit_conditions(vendor, date_from, date_to)
    sel = select(AuditLog)
    for c in conds:
        sel = sel.where(c)
    rows = list(db.execute(sel).scalars().all())
    for r in rows:
        _remove_pdf(r.pdf_path)
    del_stmt = sa_delete(AuditLog)
    for c in conds:
        del_stmt = del_stmt.where(c)
    db.execute(del_stmt)
    db.commit()
    return {"ok": True, "deleted": len(rows)}
