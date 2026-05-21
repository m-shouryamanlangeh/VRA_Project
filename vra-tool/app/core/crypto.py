"""Fernet encryption for API keys at rest."""

from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

from app.config import BASE_DIR


class CryptoError(Exception):
    """Raised when encryption configuration or data is invalid."""


def _fernet_secret_raw() -> str:
    """
    Read ``FERNET_KEY`` from the process environment.

    Reloads ``vra-tool/.env`` each time so a key added while the server runs
    is picked up without relying on a stale :mod:`app.config` snapshot.
    """
    load_dotenv(BASE_DIR / ".env", override=False)
    return (os.getenv("FERNET_KEY") or "").strip()


def get_fernet() -> Fernet:
    """
    Build a Fernet instance from ``FERNET_KEY``.

    Raises:
        CryptoError: If the key is missing or not a valid Fernet secret.
    """
    raw = _fernet_secret_raw()
    if not raw:
        raise CryptoError(
            "FERNET_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(raw.encode("utf-8"))
    except ValueError as exc:
        raise CryptoError("FERNET_KEY is not a valid Fernet key") from exc


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a UTF-8 string; return ASCII token string."""
    if not plaintext:
        raise CryptoError("Refusing to encrypt an empty secret")
    f = get_fernet()
    return f.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str) -> str:
    """Decrypt a token produced by :func:`encrypt_secret`."""
    if not token:
        raise CryptoError("Cannot decrypt an empty value")
    f = get_fernet()
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CryptoError("Could not decrypt API key (wrong FERNET_KEY?)") from exc


def mask_secret(plaintext: str, visible: int = 4) -> str:
    """Mask all but the last ``visible`` characters (for UI display)."""
    if len(plaintext) <= visible:
        return "•" * max(4, len(plaintext))
    return "•" * max(8, len(plaintext) - visible) + plaintext[-visible:]
