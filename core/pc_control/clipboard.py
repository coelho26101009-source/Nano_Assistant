"""The clipboard: text in, text out, and nothing kept.

READING THE CLIPBOARD IS A PRIVACY EVENT. Whatever the user last copied may be
a password out of a manager, a private message, a bank reference. So three
things are true here and are enforced rather than assumed:

* Only text is ever touched. `CF_UNICODETEXT` and no other format -- an image
  or a file list on the clipboard is reported as "not text" and is not
  described, converted or read.
* Nothing is remembered. There is no history, no cache, no background watcher,
  no timer. A read returns the current contents to the caller and this module
  keeps no copy.
* Nothing is logged. The content never reaches a log line, an audit entry or a
  permission target -- the target for a clipboard grant is the WORD
  "clipboard", not what is on it. The one place the text is deliberately shown
  is the confirmation card, because approving a clipboard read without seeing
  what it will expose is not informed consent.

Writing is bounded the same way and is destructive in one narrow sense: it
replaces whatever the user had copied, which Windows cannot undo. That is why
it is approval-gated too.
"""
from __future__ import annotations

import logging

from core.pc_control import winapi
from core.pc_control.results import PCControlError

# The logger exists for FAILURES ONLY. No function in this module may pass
# clipboard content to it.
logger = logging.getLogger("nano.pc_control.clipboard")

#: Bounds on both directions. The clipboard can hold megabytes; a tool result
#: that size is useless to the model and expensive to everybody.
MAX_READ_CHARS = 4000
MAX_WRITE_CHARS = 4000


def _require_windows() -> None:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "A área de transferência só funciona no Windows.")


def read_text() -> dict:
    """The clipboard's current text, bounded, or a clear "it is not text"."""
    _require_windows()
    try:
        content = winapi.clipboard_read_text(MAX_READ_CHARS + 1)
    except OSError as exc:
        logger.warning("clipboard read failed: %s", exc)
        raise PCControlError("failed",
                             "Não consegui aceder à área de transferência.") from exc

    if content is None:
        raise PCControlError(
            "unsupported",
            "A área de transferência não tem texto neste momento (pode ter uma "
            "imagem ou ficheiros).")
    if content == "":
        return {"text": "", "characters": 0, "truncated": False, "empty": True}

    truncated = len(content) > MAX_READ_CHARS
    return {
        "text": content[:MAX_READ_CHARS],
        "characters": min(len(content), MAX_READ_CHARS),
        "truncated": truncated,
        "empty": False,
    }


def write_text(value) -> dict:
    """Replace the clipboard's contents with ``value`` and read it back."""
    _require_windows()
    if not isinstance(value, str):
        raise PCControlError("invalid_input", "O conteúdo a copiar tem de ser texto.")
    if not value:
        raise PCControlError("invalid_input", "Não há texto para copiar.")
    if len(value) > MAX_WRITE_CHARS:
        raise PCControlError(
            "invalid_input",
            f"O texto é demasiado longo ({len(value)} caracteres; o máximo é "
            f"{MAX_WRITE_CHARS}).")

    try:
        written = winapi.clipboard_write_text(value)
    except OSError as exc:
        logger.warning("clipboard write failed: %s", exc)
        raise PCControlError("failed",
                             "Não consegui escrever na área de transferência.") from exc
    if not written:
        raise PCControlError("failed", "O Windows recusou a escrita na área de transferência.")

    # Read back: the clipboard is a shared resource and another application is
    # free to take it in the same millisecond. Reporting "copied" without
    # checking would be reporting an intention.
    confirmed = winapi.clipboard_read_text(MAX_WRITE_CHARS + 1)
    return {"characters": len(value), "verified": confirmed == value}


def clear() -> dict:
    """Empty the clipboard, then verify it is actually empty."""
    _require_windows()
    try:
        winapi.clipboard_clear()
    except OSError as exc:
        logger.warning("clipboard clear failed: %s", exc)
        raise PCControlError("failed",
                             "Não consegui limpar a área de transferência.") from exc
    remaining = winapi.clipboard_read_text(16)
    return {"cleared": not remaining}


__all__ = ["MAX_READ_CHARS", "MAX_WRITE_CHARS", "clear", "read_text", "write_text"]
