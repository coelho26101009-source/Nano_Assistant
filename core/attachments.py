"""Files attached to a chat message: text, PDFs and images.

The person picks a file in the composer; the window reads it and sends it with
the message (text as text, PDFs and images as base64). This module is the one
place that decides what such a file may be and how it reaches the model.

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

PDFS AND IMAGES BECOME TEXT BEFORE THE TURN STARTS.

``normalise`` only checks their shape, so the bridge answers at once.
``prepare`` then runs on a worker thread, in the turn itself: a PDF's text is
extracted locally with pypdf, and an image is described by a vision model
(``core.vision``, which follows the person's provider mode, so LOCAL keeps the
picture on the PC). The bytes are dropped there; only the extracted text or
the description is stored and reaches the model, fenced like any file. A
scanned PDF with no text layer says so instead of arriving empty.
"""
from __future__ import annotations

import base64
import binascii
import io
import logging
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
#: The largest PDF accepted, decoded. Bigger ones are mostly text we would cut.
MAX_PDF_BYTES = 15 * 1024 * 1024
#: Pages read from one PDF. The text budget runs out long before this.
MAX_PDF_PAGES = 60
KINDS = ("text", "pdf", "image")

logger = logging.getLogger("nano.attachments")

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean_name(raw: Any) -> str:
    """A display name only: no path, no control characters, bounded."""
    name = str(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL.sub("", name).strip()
    return name[:MAX_NAME_CHARS] or "ficheiro"


def _share(files: list[dict]) -> None:
    """Cut every file's text so that all of them fit MAX_TOTAL_CHARS, evenly."""
    if not files:
        return
    share = MAX_TOTAL_CHARS // len(files)
    for entry in files:
        if "data" in entry:
            continue
        entry["truncated"] = bool(entry.get("truncated")) or len(entry["text"]) > share
        entry["text"] = entry["text"][:share]


def normalise(raw: Any) -> tuple[list[dict], str | None]:
    """Validate what the window sent. Returns (attachments, error code).

    A text file comes back as ``{"name", "kind": "text", "size", "text",
    "truncated"}``. A PDF or an image keeps its base64 ``data`` (and ``mime``)
    until ``prepare`` turns it into text. The text is cut so that all files
    together stay inside MAX_TOTAL_CHARS, shared evenly, and ``truncated``
    says so -- the UI shows it, so a long file is never silently read in part.
    """
    from core import vision

    if raw in (None, "", []):
        return [], None
    if not isinstance(raw, list):
        return [], "bad_attachments"
    if len(raw) > MAX_FILES:
        return [], "too_many_attachments"
    files: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            return [], "bad_attachments"
        kind = str(item.get("kind") or "text")
        if kind not in KINDS:
            return [], "bad_attachments"
        name = _clean_name(item.get("name"))
        if kind == "text":
            if not isinstance(item.get("text"), str):
                return [], "bad_attachments"
            text = item["text"]
            if "\x00" in text:
                return [], "binary_attachment"
            files.append({"name": name, "kind": "text", "size": len(text), "text": text})
            continue
        data = item.get("data")
        if not isinstance(data, str) or not data:
            return [], "bad_attachments"
        size = len(data) * 3 // 4
        if kind == "pdf":
            if size > MAX_PDF_BYTES:
                return [], "pdf_too_large"
            files.append({"name": name, "kind": "pdf", "size": size, "data": data,
                          "mime": "application/pdf", "text": ""})
        else:
            mime = str(item.get("mime") or "").lower()
            problem = vision.check_image(data, mime)
            if problem:
                return [], problem
            files.append({"name": name, "kind": "image", "size": size, "data": data,
                          "mime": mime, "text": ""})
    _share(files)
    return files, None


def _decode(data: str) -> bytes:
    try:
        return base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        return b""


def pdf_text(raw: bytes) -> tuple[str, bool]:
    """The text layer of a PDF, read locally. Returns (text, truncated)."""
    try:
        import pypdf
    except ImportError:
        return tr("(não foi possível ler este PDF: falta o leitor de PDF)"), False
    try:
        reader = pypdf.PdfReader(io.BytesIO(raw))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return tr("(este PDF está protegido por palavra-passe e não pode ser lido)"), False
        pages = reader.pages
        parts, cut = [], len(pages) > MAX_PDF_PAGES
        for number, page in enumerate(pages[:MAX_PDF_PAGES], start=1):
            text = (page.extract_text() or "").strip()
            if text:
                parts.append(f"[{tr('página')} {number}]\n{text}")
            if sum(len(part) for part in parts) > MAX_TOTAL_CHARS:
                cut = True
                break
    except Exception as exc:
        logger.info("PDF ilegível: %s", type(exc).__name__)
        return tr("(não foi possível ler este PDF: o ficheiro parece estar danificado)"), False
    if not parts:
        return tr("(este PDF não tem texto selecionável; pode ser uma digitalização)"), False
    return "\n\n".join(parts), cut


def prepare(files: list[dict], *, question: str | None = None) -> list[dict]:
    """Turn PDFs and images into text, and drop their bytes. Blocking.

    Runs in the turn, on a worker thread (``asyncio.to_thread``). Files that
    are already text -- including those of a retried turn -- pass unchanged.
    Never raises: a file that cannot be read carries a sentence that says why,
    so the model can tell the person instead of guessing.
    """
    from core import vision

    out: list[dict] = []
    for entry in files or []:
        if "data" not in entry:
            out.append(entry)
            continue
        entry = dict(entry)
        data = entry.pop("data")
        if entry.get("kind") == "pdf":
            entry["text"], entry["truncated"] = pdf_text(_decode(data))
        else:
            result = vision.describe(data, str(entry.get("mime") or "image/png"),
                                     question=question)
            if result.get("ok"):
                where = tr("no teu PC") if result.get("local") else str(result.get("provider"))
                entry["text"] = (tr("Descrição da imagem feita por um modelo de visão ({where}):",
                                    where=where) + "\n" + result["text"])
                entry["vision"] = {"provider": result.get("provider"), "model": result.get("model")}
            else:
                entry["text"] = tr("(não foi possível ver esta imagem: {why})",
                                   why=result.get("message") or "")
                entry["vision_error"] = result.get("error") or "vision_failed"
            entry["truncated"] = False
        entry.pop("mime", None)
        out.append(entry)
    _share(out)
    return out


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
             "kind": entry.get("kind") or "text",
             "truncated": bool(entry.get("truncated"))}
            for entry in (attachments or [])]
