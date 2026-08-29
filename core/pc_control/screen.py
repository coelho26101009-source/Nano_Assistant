"""Desktop screenshots: capture to a managed local file, and nothing else.

THIS IS THE MOST PRIVACY-SENSITIVE TOOL IN PC CONTROL. A screenshot can
contain an open password manager, a bank page, a private message. Three rules
follow, and they are enforced here rather than left to the caller:

* The image NEVER enters the model's context. The tool returns a path, a size
  and dimensions -- no base64, no pixels. A model that wants to know what is on
  screen cannot obtain it through this tool.
* The file stays local, in Nano's own data directory, under a unique name.
  Nothing uploads it.
* Old captures are deleted on every run, so a forgotten screenshot does not sit
  on disk indefinitely.

V2 adds two narrower modes -- the active window, and one named window -- and
they are narrower in the privacy sense too: a capture of the Calculator is a
much smaller disclosure than a capture of everything on the desktop. The rules
above are unchanged and apply to all three.

The capture itself is BitBlt through ctypes plus a small PNG writer built on
stdlib zlib. Pillow and mss are not installed, and adding an imaging dependency
to save one file would be a poor trade.
"""
from __future__ import annotations

import ctypes
import logging
import struct
import time
import uuid
import zlib
from ctypes import wintypes
from pathlib import Path

from core.app_paths import DATA_DIR
from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.screen")

SCREENSHOT_DIR = Path(DATA_DIR) / "screenshots"

#: Captures older than this are removed at the start of the next capture.
RETENTION_SECONDS = 60 * 60          # one hour
#: Never keep more than this many, however recent.
MAX_RETAINED = 10
#: The long edge is scaled down to this. Full 4K PNGs are megabytes for no gain.
MAX_DIMENSION = 1920

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", _BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def _write_png(path: Path, width: int, height: int, rows: list[bytes]) -> None:
    """Minimal PNG encoder. Truecolour, 8-bit, filter type 0.

    zlib is in the standard library, so a correct PNG costs about thirty lines
    and no dependency.
    """
    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def cleanup(*, retention_seconds: float = RETENTION_SECONDS,
            max_retained: int = MAX_RETAINED) -> int:
    """Delete stale captures. Returns how many were removed."""
    if not SCREENSHOT_DIR.exists():
        return 0
    try:
        files = sorted(SCREENSHOT_DIR.glob("screenshot-*.png"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return 0

    now = time.time()
    removed = 0
    for index, candidate in enumerate(files):
        try:
            too_old = (now - candidate.stat().st_mtime) > retention_seconds
            too_many = index >= max_retained
            if too_old or too_many:
                candidate.unlink()
                removed += 1
        except OSError:
            logger.debug("could not remove old screenshot %s", candidate, exc_info=True)
    return removed


CAPTURE_MODES = ("desktop", "active_window", "window")


def _scaled(width: int, height: int) -> tuple[int, int, float]:
    scale = min(1.0, MAX_DIMENSION / max(width, height))
    return max(1, int(width * scale)), max(1, int(height * scale)), scale


def _rows_from_bitmap(gdi32, memory_dc, bitmap, width: int, height: int) -> list[bytes]:
    """Read a device-independent bitmap back as PNG-ready RGB rows."""
    info = _BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    # Negative height requests a top-down DIB, so row order matches PNG.
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0

    buffer = ctypes.create_string_buffer(width * height * 4)
    if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer,
                           ctypes.byref(info), DIB_RGB_COLORS):
        raise PCControlError("failed", "Não consegui ler os pixéis capturados.")

    raw = buffer.raw
    stride = width * 4
    rows = []
    for y in range(height):
        line = raw[y * stride:(y + 1) * stride]
        # Windows hands back BGRA; PNG truecolour wants RGB.
        rows.append(bytes(b for x in range(0, stride, 4)
                          for b in (line[x + 2], line[x + 1], line[x])))
    return rows


def _capture_screen_region(x: int, y: int, width: int, height: int) -> tuple[list[bytes], int, int, bool]:
    """Copy a rectangle off the screen, scaled down if it is very large."""
    out_width, out_height, scale = _scaled(width, height)
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    screen_dc = memory_dc = bitmap = None
    try:
        screen_dc = user32.GetDC(0)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, out_width, out_height)
        gdi32.SelectObject(memory_dc, bitmap)

        if scale < 1.0:
            gdi32.SetStretchBltMode(memory_dc, 4)      # HALFTONE
            copied = gdi32.StretchBlt(memory_dc, 0, 0, out_width, out_height,
                                      screen_dc, x, y, width, height, SRCCOPY)
        else:
            copied = gdi32.BitBlt(memory_dc, 0, 0, out_width, out_height,
                                  screen_dc, x, y, SRCCOPY)
        if not copied:
            raise PCControlError("failed", "O Windows não permitiu capturar o ecrã.")
        rows = _rows_from_bitmap(gdi32, memory_dc, bitmap, out_width, out_height)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(0, screen_dc)
    return rows, out_width, out_height, scale < 1.0


def _capture_window_surface(hwnd: int, width: int, height: int):
    """Ask the window to draw itself, so a covered window still captures.

    Returns None when PrintWindow declines, and the caller falls back to
    copying the window's rectangle off the screen -- and SAYS which one it
    used, because a fallback capture of a covered window contains whatever was
    on top of it, which the user is entitled to know.
    """
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    screen_dc = memory_dc = bitmap = None
    try:
        screen_dc = user32.GetDC(0)
        memory_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        gdi32.SelectObject(memory_dc, bitmap)
        if not winapi.print_window(hwnd, memory_dc):
            return None
        return _rows_from_bitmap(gdi32, memory_dc, bitmap, width, height)
    finally:
        if bitmap:
            gdi32.DeleteObject(bitmap)
        if memory_dc:
            gdi32.DeleteDC(memory_dc)
        if screen_dc:
            user32.ReleaseDC(0, screen_dc)


def _save(rows: list[bytes], width: int, height: int, *, scaled: bool,
          removed: int, subject: str, method: str, extra: dict | None = None) -> dict:
    path = (SCREENSHOT_DIR
            / f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.png")
    _write_png(path, width, height, rows)
    payload = {
        "path": str(path),
        "width": width,
        "height": height,
        "size_bytes": path.stat().st_size,
        "scaled": scaled,
        "cleaned_up": removed,
        "subject": subject,
        "method": method,
        # Stated in the result so it is unambiguous to every reader, including
        # the model, that no image data is being provided here.
        "note": "A imagem ficou guardada localmente. O conteúdo não é enviado ao modelo.",
    }
    payload.update(extra or {})
    return payload


def capture(mode: str = "desktop", *, window: dict | None = None) -> dict:
    """Capture to a PNG and return its METADATA ONLY -- never any pixels.

    ``window`` is a window description from ``windows.resolve_window``; the
    caller resolves it, because target binding for the permission grant has to
    happen before this runs.
    """
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "As capturas de ecrã só funcionam no Windows.")
    key = str(mode or "desktop").strip().lower()
    if key not in CAPTURE_MODES:
        raise PCControlError("invalid_input",
                             f"'{mode}' não é um modo de captura conhecido.",
                             allowed=list(CAPTURE_MODES))

    removed = cleanup()
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    if key == "desktop":
        width, height = winapi.screen_size()
        if width <= 0 or height <= 0:
            raise PCControlError("failed", "Não consegui determinar o tamanho do ecrã.")
        rows, out_width, out_height, scaled = _capture_screen_region(0, 0, width, height)
        return _save(rows, out_width, out_height, scaled=scaled, removed=removed,
                     subject="ecrã completo", method="screen")

    if key == "active_window":
        hwnd = winapi.foreground_window()
        if not hwnd or not winapi.is_window(hwnd):
            raise PCControlError("not_found", "Não há nenhuma janela em primeiro plano.")
        from core.pc_control import windows as window_control

        window = window_control.describe(hwnd)

    if not window:
        raise PCControlError("invalid_input", "É preciso indicar a janela a capturar.")

    hwnd = int(window["window_id"])
    if not winapi.is_window(hwnd):
        raise PCControlError("not_found", "Essa janela já não existe.")
    if winapi.window_placement_state(hwnd) == "minimized":
        raise PCControlError(
            "failed",
            f"'{window['title']}' está minimizada; restaura-a primeiro para a capturar.")

    left, top, right, bottom = winapi.window_rect(hwnd)
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        raise PCControlError("failed", "Essa janela não tem tamanho para capturar.")

    out_width, out_height, scale = _scaled(width, height)
    rows = None
    method = "print_window"
    if scale >= 1.0:
        rows = _capture_window_surface(hwnd, width, height)
        out_width, out_height = width, height
    if rows is None:
        rows, out_width, out_height, _scaled_flag = _capture_screen_region(
            left, top, width, height)
        method = "screen_region"

    return _save(rows, out_width, out_height, scaled=scale < 1.0, removed=removed,
                 subject=window["title"], method=method,
                 extra={"window": {"window_id": hwnd, "title": window["title"],
                                   "process": window.get("process")}})


__all__ = ["CAPTURE_MODES", "MAX_DIMENSION", "MAX_RETAINED", "RETENTION_SECONDS",
           "SCREENSHOT_DIR", "capture", "cleanup"]
