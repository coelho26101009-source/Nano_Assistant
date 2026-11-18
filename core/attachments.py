"""Text files attached to a chat message.

The person picks a file in the composer; the window reads it as text and sends
it with the message. This module is the one place that decides what such a
file may be and how it reaches the model.

WHAT AN ATTACHMENT IS, AS FAR AS TRUST GOES.

The person chose to share the file, but they did not write it. A document
downloaded from the web can carry "ignore your instructions and ..." as easily
as a web page can, so its text reaches the model inside the same
NANO_UNTRUSTED_EXTERNAL_CONTENT fence as a fetched page (``core.trust``). It is
data for this turn: it can inform the answer and can never grant a permission.

WHERE THE TEXT IS KEPT.

The stored user row holds the person's own words as its content, so memory
extraction and the retrieval index only ever see what the person typed. The
files travel in that row's metadata, and are recomposed into the model's
message when the thread is reopened or the turn is asked again.

WHAT IS NOT SUPPORTED. Only text: plain text, Markdown, CSV, JSON, logs and
source code. Images and PDFs need a different reader and are refused by the
window before they are sent.
"""
from __future__ import annotations

import re
from typing import Any

from core.i18n import tr
from core.trust import wrap_untrusted

#: How many files one message may carry.
MAX_FILES = 5
#: The most text kept for one turn, across all its files. It is sized to the
#: history budget (``memory.max_history_tokens``, ~4 000 tokens): a single
#: message larger than that would push the whole conversation out of context.
MAX_TOTAL_CHARS = 12_000
#: The longest file name kept.
MAX_NAME_CHARS = 120

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_name(raw: Any) -> str:
    """A display name only: no path, no control characters, bounded."""
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL.sub("", name).strip()
    return name[:MAX_NAME_CHARS] or "ficheiro"


def normalise(raw: Any) -> tuple[list[dict], str | None]:
    """Validate what the window sent. Returns (attachments, error code).

    Each attachment comes back as ``{"name", "size", "text", "truncated"}``.
    The text is cut so that all files together stay inside MAX_TOTAL_CHARS,
    shared evenly, and ``truncated`` says so -- the UI shows it, so a long file
    is never silently read in part.
    """
    if raw in (None, "", []):
        return [], None
    if not isinstance(raw, list):
        return [], "bad_attachments"
    if len(raw) > MAX_FILES:
        return [], "too_many_attachments"
    files: list[dict] = []
    for item in raw:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str):
            return [], "bad_attachments"
        text = item["text"]
        if "\x00" in text:
            return [], "binary_attachment"
        files.append({"name": _clean_name(item.get("name")), "size": len(text), "text": text})
    if not files:
        return [], None
    share = MAX_TOTAL_CHARS // len(files)
    for entry in files:
        entry["truncated"] = len(entry["text"]) > share
        entry["text"] = entry["text"][:share]
    return files, None


def compose(user_text: str, attachments: list[dict] | None, *, max_chars: int | None = None) -> str:
    """The model's view of one user turn: the words, then each file, fenced.

    ``max_chars`` bounds the files' text further. A reopened thread passes the
    history's per-message budget, so an old turn costs what any other does.
    """
    if not attachments:
        return user_text
    share = None if max_chars is None else max(200, max_chars // len(attachments))
    blocks = []
    for entry in attachments:
        body = str(entry.get("text") or "")
        cut = bool(entry.get("truncated"))
        if share is not None and len(body) > share:
            body, cut = body[:share], True
        note = tr("(ficheiro cortado: só o início foi lido)") if cut else ""
        blocks.append(f"{tr('Ficheiro anexado')}: {entry.get('name')} {note}".rstrip()
                      + "\n" + wrap_untrusted(body, source=f"attachment:{entry.get('name')}",
                                              max_chars=len(body) + 1))
    return user_text + "\n\n" + "\n\n".join(blocks)


def summary(attachments: list[dict] | None) -> list[dict]:
    """What the window shows for each file: the name, the size, whether it was cut."""
    return [{"name": entry.get("name"), "size": entry.get("size"),
             "truncated": bool(entry.get("truncated"))}
            for entry in (attachments or [])]
