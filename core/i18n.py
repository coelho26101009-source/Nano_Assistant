"""Interface language: which one is in force, and translating backend text.

WHY THE PORTUGUESE TEXT IS THE KEY
----------------------------------
NANO was written in European Portuguese, and every user-facing sentence in the
backend is still written in Portuguese at its call site. Rather than replacing
each sentence with an opaque id ("route.local_only") that has to be looked up to
be read, the Portuguese sentence itself is the key: ``tr("Modo Local: apenas o
Ollama é usado.")`` reads as what it says, and English is a lookup in
``core.locales.en``. A sentence with no English entry is shown as written, so a
missing translation degrades to Portuguese instead of to an id, and
``tests/test_i18n.py`` fails on any ``tr()`` literal that has no entry.

WHICH LANGUAGE
--------------
``user_settings["language"]`` is ``"system"`` (the default), ``"en"`` or
``"pt-PT"``. "system" follows the operating system's UI language: Portuguese
for a Portuguese (or Brazilian) Windows, English for everything else, because
English is the language with the widest reach when NANO does not speak the
user's own yet. The frontend uses the resolved value this module reports, so
the window and the backend's sentences always agree.
"""
from __future__ import annotations

import locale as _locale
import logging
import os
import sys
from typing import Any

logger = logging.getLogger("nano.i18n")

#: Languages the interface is translated into. The first is the fallback.
SUPPORTED: tuple[str, ...] = ("en", "pt-PT")
DEFAULT = "en"
SETTING_VALUES: frozenset[str] = frozenset({"system", *SUPPORTED})

_override: str | None = None


def _normalise(tag: str | None) -> str | None:
    """Map an OS / browser language tag onto a supported language, or None."""
    value = str(tag or "").strip().replace("_", "-").lower()
    if not value or value in {"c", "posix"}:
        return None
    if value.startswith("pt"):
        return "pt-PT"
    if value.startswith("en"):
        return "en"
    return None


def _windows_ui_language() -> str | None:
    """The Windows display language, e.g. 'pt-PT'. None anywhere else."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes  # noqa: PLC0415 - Windows only

        lang_id = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        return _locale.windows_locale.get(lang_id)
    except Exception:
        logger.debug("Could not read the Windows UI language", exc_info=True)
        return None


def system_language() -> str:
    """The operating system's language, mapped onto a supported one."""
    candidates = [
        os.environ.get("NANO_LANGUAGE"),
        _windows_ui_language(),
        os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG"),
    ]
    try:
        candidates.append(_locale.getlocale()[0])
    except Exception:
        pass
    for candidate in candidates:
        resolved = _normalise(candidate)
        if resolved:
            return resolved
    return DEFAULT


def setting() -> str:
    """What the user chose: 'system', 'en' or 'pt-PT'."""
    try:
        from core import user_settings  # noqa: PLC0415 - avoid an import cycle at load

        value = user_settings.get("language", "system")
    except Exception:
        value = "system"
    return value if value in SETTING_VALUES else "system"


def current() -> str:
    """The language in force right now."""
    if _override:
        return _override
    chosen = setting()
    return system_language() if chosen == "system" else chosen


def set_override(language: str | None) -> None:
    """Force a language for this process. Tests use it; nothing else should."""
    global _override
    _override = language if language in SUPPORTED else None


def describe() -> dict[str, str]:
    """The payload the UI needs: the choice, what it resolved to, and the OS's."""
    return {"setting": setting(), "resolved": current(), "system": system_language()}


def tr(text: str, /, **values: Any) -> str:
    """Translate one Portuguese sentence into the language in force.

    ``values`` fill ``{name}`` placeholders AFTER translation, so the English
    sentence can put them wherever English grammar wants them.
    """
    out = text
    if current() == "en":
        from core.locales.en import EN  # noqa: PLC0415 - loaded on first use

        out = EN.get(text, text)
    if values:
        try:
            out = out.format(**values)
        except (KeyError, IndexError, ValueError):
            logger.debug("Placeholder mismatch translating %r", text)
    return out


def reply_language_instruction() -> str:
    """The system-prompt line that tells the model which language to answer in.

    The model answers in the language the user WRITES in. The interface
    language is only the fallback for a message whose language is unclear
    ("ok", "?", a single emoji): a Portuguese speaker on an English Windows
    still gets Portuguese answers to Portuguese questions.
    """
    fallback = "European Portuguese (português de Portugal)" if current() == "pt-PT" else "English"
    return (
        "Always reply in the same language the user writes in. "
        f"If the user's language is unclear, reply in {fallback}."
    )


__all__ = [
    "DEFAULT", "SETTING_VALUES", "SUPPORTED", "current", "describe",
    "reply_language_instruction", "set_override", "setting", "system_language", "tr",
]
