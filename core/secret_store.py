"""OS-backed secret storage for Nano.

API keys must never live in settings.yaml, in Git, in the frontend bundle, or
in browser storage. They also must not force the user to hand-edit .env.

On Windows this uses DPAPI (CryptProtectData/CryptUnprotectData) through
ctypes: the ciphertext can only be decrypted by the same Windows user account
on the same machine, and it needs no third-party dependency. Elsewhere it
falls back to a 0600 file, which is weaker but keeps the same interface.

Secrets only ever travel backend -> backend. The only thing the UI receives is
a masked hint (`sk-••••••••1234`) and a boolean.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from core.app_paths import DATA_DIR

logger = logging.getLogger("nano.secrets")

_STORE_PATH = DATA_DIR / "secrets.dat"

# Environment variables checked as a read-only fallback, so an existing .env
# keeps working. Writing always goes to the encrypted store, never back to .env.
_ENV_FALLBACK: dict[str, tuple[str, ...]] = {
    "groq_api_key": ("NANO_API_KEY", "HELIOS_API_KEY", "GROQ_API_KEY"),
    # Google/Gemini. NANO_GEMINI_API_KEY is the name Nano documents; the two
    # vendor names are accepted because a machine that already has one should
    # not need a second copy. Each provider has its OWN entry -- one key must
    # never satisfy or overwrite another's, so removing Groq cannot silently
    # disable Google and vice versa.
    "google_api_key": ("NANO_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    # Mistral. NANO_MISTRAL_API_KEY is the name Nano documents; the vendor name
    # is accepted so a machine that already has one does not need a second copy.
    # Same rule as above: its OWN entry, so it can never satisfy or be satisfied
    # by another provider's credential.
    "mistral_api_key": ("NANO_MISTRAL_API_KEY", "MISTRAL_API_KEY"),
}


# --------------------------------------------------------------------------
# Windows DPAPI
# --------------------------------------------------------------------------

def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi(encrypt: bool, payload: bytes) -> bytes | None:
    """Call CryptProtectData / CryptUnprotectData. Returns None on failure."""
    if not _dpapi_available():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _Blob(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        def _to_blob(data: bytes) -> _Blob:
            buffer = ctypes.create_string_buffer(data, len(data))
            return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

        source = _to_blob(payload)
        result = _Blob()
        crypt32 = ctypes.windll.crypt32

        # CRYPTPROTECT_UI_FORBIDDEN: never prompt; Nano runs unattended.
        ok = (
            crypt32.CryptProtectData(
                ctypes.byref(source), "nano", None, None, None, 0x01, ctypes.byref(result)
            )
            if encrypt
            else crypt32.CryptUnprotectData(
                ctypes.byref(source), None, None, None, None, 0x01, ctypes.byref(result)
            )
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(result.pbData, result.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(result.pbData)
    except Exception as exc:
        logger.warning("DPAPI %s failed: %s", "encrypt" if encrypt else "decrypt", exc)
        return None


# --------------------------------------------------------------------------
# Store
# --------------------------------------------------------------------------

def _read_store() -> dict[str, str]:
    if not _STORE_PATH.exists():
        return {}
    try:
        raw = _STORE_PATH.read_bytes()
    except OSError:
        return {}
    if not raw:
        return {}

    decrypted = _dpapi(False, raw)
    if decrypted is None:
        # Written without DPAPI (non-Windows, or DPAPI unavailable at write time).
        decrypted = raw
    try:
        data = json.loads(decrypted.decode("utf-8"))
        return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:
        logger.warning("Secret store unreadable; ignoring it.")
        return {}


def _write_store(values: dict[str, str]) -> bool:
    payload = json.dumps(values, ensure_ascii=False).encode("utf-8")
    encrypted = _dpapi(True, payload)
    blob = encrypted if encrypted is not None else payload

    try:
        _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STORE_PATH.write_bytes(blob)
        if encrypted is None and os.name != "nt":
            os.chmod(_STORE_PATH, 0o600)
        return True
    except OSError as exc:
        logger.error("Could not write the secret store: %s", exc)
        return False


def is_encrypted() -> bool:
    """True when secrets are protected by the OS rather than only file perms."""
    return _dpapi_available()


def get_secret(name: str) -> str:
    """Return a secret, preferring the encrypted store over the environment."""
    stored = _read_store().get(name)
    if stored:
        return stored
    for env_name in _ENV_FALLBACK.get(name, ()):
        value = os.getenv(env_name)
        if value:
            return value.strip()
    return ""


def set_secret(name: str, value: str) -> bool:
    values = _read_store()
    cleaned = (value or "").strip()
    if not cleaned:
        return delete_secret(name)
    values[name] = cleaned
    return _write_store(values)


def delete_secret(name: str) -> bool:
    values = _read_store()
    if name not in values:
        return True
    del values[name]
    return _write_store(values)


def has_secret(name: str) -> bool:
    return bool(get_secret(name))


def mask(value: str) -> str:
    """A hint the UI may show. Never enough to reconstruct the key."""
    if not value:
        return ""
    if len(value) <= 8:
        return "•" * len(value)
    return f"{value[:3]}{'•' * 8}{value[-4:]}"


def describe(name: str) -> dict[str, Any]:
    """Safe metadata for the UI: never the secret itself."""
    value = get_secret(name)
    source = "none"
    if _read_store().get(name):
        source = "encrypted_store"
    elif value:
        source = "environment"
    return {
        "configured": bool(value),
        "masked": mask(value),
        "source": source,
        "encrypted": is_encrypted() and source == "encrypted_store",
    }


__all__ = [
    "delete_secret",
    "describe",
    "get_secret",
    "has_secret",
    "is_encrypted",
    "mask",
    "set_secret",
]
