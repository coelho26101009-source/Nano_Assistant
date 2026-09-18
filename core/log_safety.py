"""Sanitize log output without opening credential stores from a formatter."""
from __future__ import annotations

import re
import threading

_LOCK = threading.RLock()
_KNOWN: set[str] = set()
_TOKEN = re.compile(r"\b(?:gsk_[A-Za-z0-9_-]{8,}|sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{12,})")
_BEARER = re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/-]+=*")
_LABELED = re.compile(
    r"(?i)((?:[\w-]*(?:api[_-]?key|access[_-]?token|password|secret)|authorization)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s\"',;&}\]]+)"
)


def register_secret(value: str) -> None:
    if isinstance(value, str) and len(value) >= 4:
        with _LOCK:
            _KNOWN.add(value)


def redact_log_text(text: str) -> str:
    with _LOCK:
        known = sorted(_KNOWN, key=len, reverse=True)
    for secret in known:
        text = text.replace(secret, "[REDACTED]")
    text = _TOKEN.sub("[REDACTED]", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    return _LABELED.sub(r"\1[REDACTED]", text)
