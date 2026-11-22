"""Read the text on screen, locally, with Windows' own OCR -- and only when allowed.

WHY THIS IS SEPARATE FROM SCREENSHOTS
-------------------------------------
A screenshot never enters the model's context: the tool returns a path. Reading
the text on screen is the opposite -- the TEXT is the result, and it goes to the
model. That is a bigger disclosure, so it carries its own controls, all
enforced here and in the policy, not left to the caller:

1. **Off by default.** Nothing is read until the user turns on
   Definições -> Privacidade -> "Ler texto do ecrã" (``screen_text_enabled``).
2. **Always asks.** Capability ``pc.screen.read_text`` is HIGH risk and on the
   always-confirm list; the card names the window, monitor or region.
3. **Never the sensitive apps.** If the target window -- or, for a monitor or
   region, any visible window on that area -- belongs to a password manager or
   authenticator, the read is refused before any pixel is captured.
4. **Secrets masked.** API keys, tokens, labelled passwords, card numbers and
   IBANs are replaced with markers before the text leaves this module.
5. **Bounded.** At most ``MAX_TEXT_CHARS`` characters.
6. **Nothing kept.** The pixels live in memory only for the OCR call; no image
   is written to disk and nothing is uploaded. The text is marked as untrusted
   external content, so anything on screen that looks like an instruction is
   fenced as data.
7. **Local engine only.** ``Windows.Media.Ocr`` through the optional WinRT
   packages (requirements-optional.txt). No cloud OCR exists in Nano. Without
   the packages, or without an OCR language installed, the tool says so.
"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable

from core.memory_safety import mask_secrets
from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.ocr")

MAX_TEXT_CHARS = 4000
OCR_MODES = ("active_window", "window", "monitor", "region")
DEFAULT_OCR_MODE = "active_window"

#: Processes whose windows are never read. Matched on the process name,
#: case-insensitive, with or without ".exe".
BLOCKED_PROCESSES = frozenset({
    "1password", "keepass", "keepassxc", "bitwarden", "lastpass", "dashlane",
    "enpass", "nordpass", "roboform", "keeper", "protonpass", "proton pass",
    "authy", "authy desktop", "winauth", "credentialuibroker", "consent",
    "logonui", "lockapp",
})

_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?:[ ]?[A-Z0-9]{4}){3,7}(?:[ ]?[A-Z0-9]{1,4})?\b")


def _process_key(name: Any) -> str:
    key = str(name or "").strip().casefold()
    return key[:-4] if key.endswith(".exe") else key


def is_blocked_process(name: Any) -> bool:
    return _process_key(name) in BLOCKED_PROCESSES


def _luhn_ok(digits: str) -> bool:
    total, double = 0, False
    for char in reversed(digits):
        value = int(char)
        if double:
            value = value * 2 - 9 if value > 4 else value * 2
        total += value
        double = not double
    return total % 10 == 0


def mask_sensitive(text: str) -> tuple[str, int]:
    """Mask credentials, card numbers (Luhn-valid) and IBANs. Returns (text, count)."""
    value, count = mask_secrets(text)

    def card(match: re.Match) -> str:
        nonlocal count
        digits = re.sub(r"\D", "", match.group(0))
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            count += 1
            return "[cartão oculto]"
        return match.group(0)

    value = _CARD.sub(card, value)
    value, iban_count = _IBAN.subn("[IBAN oculto]", value)
    return value, count + iban_count


def _overlaps(a: tuple, b: tuple) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def blocked_window_in(rect: tuple, windows: list[dict]) -> dict | None:
    """The first visible, non-minimised window of a blocked app on ``rect``."""
    for window in windows:
        if not is_blocked_process(window.get("process")):
            continue
        if not window.get("visible", True) or window.get("state") == "minimized":
            continue
        bounds = window.get("rect")
        if bounds is None or _overlaps(rect, tuple(bounds)):
            return window
    return None


def _bgra(rows: list[bytes], width: int) -> bytes:
    """PNG-ready RGB rows back to the BGRA buffer a SoftwareBitmap wants."""
    out = bytearray(width * len(rows) * 4)
    index = 0
    for row in rows:
        for x in range(0, width * 3, 3):
            out[index] = row[x + 2]
            out[index + 1] = row[x + 1]
            out[index + 2] = row[x]
            out[index + 3] = 255
            index += 4
    return bytes(out)


# ── the engine ────────────────────────────────────────────────────────────

def engine_status() -> dict:
    """Whether local OCR can run here, and if not, why -- in the user's terms."""
    if not winapi.IS_WINDOWS:
        return {"available": False, "reason": "unsupported_platform",
                "message": "Ler texto do ecrã só funciona no Windows."}
    try:
        # Before any winrt import: see core.now_playing._load_onnxruntime_first.
        from core.now_playing import _load_onnxruntime_first

        _load_onnxruntime_first()
        from winrt.windows.media.ocr import OcrEngine  # type: ignore
    except Exception:
        return {"available": False, "reason": "missing_dependency",
                "message": ("Falta o motor de OCR do Windows para Python. Instala as "
                            "dependências opcionais (requirements/optional.lock).")}
    try:
        engine = OcrEngine.try_create_from_user_profile_languages()
    except Exception:
        engine = None
    if engine is None:
        return {"available": False, "reason": "no_language",
                "message": ("O Windows não tem nenhum idioma de OCR instalado. Adiciona um em "
                            "Definições do Windows → Hora e idioma → Idioma.")}
    return {"available": True, "reason": "", "message": "OCR local do Windows disponível."}


def _recognize_with_winrt(bgra: bytes, width: int, height: int) -> str:
    from winrt.windows.graphics.imaging import (  # type: ignore
        BitmapAlphaMode, BitmapPixelFormat, SoftwareBitmap)
    from winrt.windows.media.ocr import OcrEngine  # type: ignore
    from winrt.windows.storage.streams import DataWriter  # type: ignore

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise PCControlError("unavailable", engine_status()["message"])
    writer = DataWriter()
    writer.write_bytes(bgra)
    bitmap = SoftwareBitmap.create_copy_from_buffer(
        writer.detach_buffer(), BitmapPixelFormat.BGRA8, width, height, BitmapAlphaMode.PREMULTIPLIED)

    async def run() -> str:
        result = await engine.recognize_async(bitmap)
        return "\n".join(line.text for line in result.lines)

    return asyncio.run(run())


#: Swappable in tests; production uses Windows' own engine.
RECOGNIZER: Callable[[bytes, int, int], str] = _recognize_with_winrt


def read_text(shot: dict) -> dict:
    """OCR an in-memory capture from ``screen.grab`` and return masked, bounded text."""
    status = engine_status()
    if not status["available"]:
        raise PCControlError("unavailable", status["message"], reason=status["reason"])
    rows, width, height = shot["rows"], int(shot["width"]), int(shot["height"])
    try:
        raw = RECOGNIZER(_bgra(rows, width), width, height)
    except PCControlError:
        raise
    except Exception as exc:
        logger.warning("OCR falhou: %s", type(exc).__name__)
        raise PCControlError("failed", "O OCR do Windows não conseguiu ler esta imagem.") from None
    finally:
        # The pixels are not needed past this point; drop them now.
        shot["rows"] = []
    text = "\n".join(line.rstrip() for line in str(raw or "").splitlines() if line.strip())
    text, masked = mask_sensitive(text)
    truncated = len(text) > MAX_TEXT_CHARS
    if truncated:
        text = text[: MAX_TEXT_CHARS - 1] + "…"
    return {"text": text, "chars": len(text), "masked": masked, "truncated": truncated,
            "engine": "windows.media.ocr"}


__all__ = ["BLOCKED_PROCESSES", "DEFAULT_OCR_MODE", "MAX_TEXT_CHARS", "OCR_MODES",
           "blocked_window_in", "engine_status", "is_blocked_process", "mask_sensitive",
           "read_text"]
