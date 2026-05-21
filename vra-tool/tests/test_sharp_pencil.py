"""
End-to-end test with real Gemini API (slow).

Requires ``GEMINI_API_KEY`` and ``FERNET_KEY`` in the environment. If no Gemini
``ApiKey`` row exists yet, the test inserts a Primary key from ``GEMINI_API_KEY``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

pytestmark = pytest.mark.slow


def _pdf_contains_vendor(pdf_path: Path, needle: str = "SHARP PENCIL PRODUCTIONS") -> bool:
    try:
        from pypdf import PdfReader
    except ImportError:
        data = pdf_path.read_bytes()
        return needle.upper().encode() in data.upper()
    reader = PdfReader(str(pdf_path))
    text = "".join(page.extract_text() or "" for page in reader.pages)
    return needle.upper() in text.upper()


@pytest.mark.skipif(
    not (os.getenv("GEMINI_API_KEY") and os.getenv("FERNET_KEY")),
    reason="Set GEMINI_API_KEY and FERNET_KEY to run the live Sharp Pencil e2e test",
)
def test_sharp_pencil_generates_pdf_and_audit() -> None:
    from app.core.crypto import encrypt_secret
    from app.database import SessionLocal
    from app.main import app
    from app.models import ApiKey, AuditLog

    db = SessionLocal()
    try:
        existing = list(
            db.execute(
                select(ApiKey).where(
                    ApiKey.provider == "gemini",
                    ApiKey.is_active.is_(True),
                )
            )
            .scalars()
            .all()
        )
        if not existing:
            db.add(
                ApiKey(
                    provider="gemini",
                    label="Primary",
                    encrypted_key=encrypt_secret(os.environ["GEMINI_API_KEY"]),
                )
            )
            db.commit()
    finally:
        db.close()

    with TestClient(app) as client:
        r = client.post(
            "/generate",
            json={
                "vendor_name": "SHARP PENCIL PRODUCTIONS",
                "gst": "27ADKFS8129B1ZY",
                "org_type": "Partnership",
            },
        )
    assert r.status_code == 200, r.text
    data = r.json()
    fname = data["pdf_url"].rsplit("/", 1)[-1]
    pdf_path = Path(__file__).resolve().parent.parent / "output" / fname
    assert pdf_path.is_file(), f"PDF missing: {pdf_path}"
    assert _pdf_contains_vendor(pdf_path)

    db = SessionLocal()
    try:
        row = db.execute(select(AuditLog).where(AuditLog.id == data["audit_id"])).scalar_one()
        assert row.vendor_name == "SHARP PENCIL PRODUCTIONS"
        assert row.status == "SUCCESS"
    finally:
        db.close()
