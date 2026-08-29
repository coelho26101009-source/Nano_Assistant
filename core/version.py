"""The one place Nano's product version is written down.

THE PROBLEM THIS SOLVES. Before this module there were four version numbers in
three languages and they did not agree: the UI lockup said "v1.0" from a
constant in `frontend/pages/index.tsx`, `core/main.py` said "8.1.0" in two
separate hardcoded payloads, and both `package.json` files said "8.1.0". A user
reading the About panel and a user reading the installer saw different products.

`version.json` at the repository root is now the source. Python reads it here,
the frontend imports it at build time, and the Electron main process requires
it. A version bump is one edit, and nothing can drift from it silently -- the
UI contract tests assert that no display path reintroduces a literal.

TWO NUMBERS, ON PURPOSE. `product` is the semantic version other software
consumes; `display` is what a person sees in the interface. They are allowed to
differ in FORM ("1.0.0" against "v1.0") but they are derived from one record, so
they can never disagree about WHICH release this is.

NOT IN SCOPE HERE: the `version` field of either package.json. electron-builder
reads that to stamp the installer, so changing it is a packaging decision and
belongs to a packaging pass, not to a settings pass. Until then it stays at its
legacy 8.1.0 and this module is the authority for everything a user reads.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("nano.version")

_VERSION_FILE = Path(__file__).resolve().parent.parent / "version.json"

# Used only when version.json cannot be read (a partial checkout, a packaging
# layout that did not copy it). Reporting an honest "unknown" beats inventing a
# number that would be wrong somewhere.
_FALLBACK = {
    "product": "0.0.0",
    "display": "desconhecida",
    "name": "Nano Assistant",
    "channel": "unknown",
}


@lru_cache(maxsize=1)
def info() -> dict[str, str]:
    """The whole version record. Cached: the file cannot change at runtime."""
    try:
        parsed = json.loads(_VERSION_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("version.json unreadable (%s); reporting unknown.", exc)
        return dict(_FALLBACK)
    if not isinstance(parsed, dict):
        return dict(_FALLBACK)
    return {key: str(parsed.get(key, _FALLBACK[key])) for key in _FALLBACK}


def product() -> str:
    """The semantic version, e.g. "1.0.0"."""
    return info()["product"]


def display() -> str:
    """What a person sees, e.g. "v1.0"."""
    return info()["display"]


def name() -> str:
    return info()["name"]


__all__ = ["display", "info", "name", "product"]
