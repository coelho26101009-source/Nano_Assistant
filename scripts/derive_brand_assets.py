"""Derive transparent-background PNGs from the supplied brand artwork.

    python scripts/derive_brand_assets.py [frontend/public/branding]

WHY THIS EXISTS
The brand files are colour-type 2 -- truecolour with NO alpha channel -- so the
artwork is burned onto a pure-black square. Dropped onto a glass panel or a
rounded avatar, that renders as a black tile with hard corners.

The alpha is RECOVERED, not guessed. Bright artwork on pure black is already
premultiplied against black, so alpha = max(R, G, B) and the straight colour is
rgb / alpha -- an exact inverse of the compositing that produced the file. The
result is a clean anti-aliased edge, which a threshold key could never give.

The originals are never modified: this writes `<name>-alpha.png` beside them,
cropped to the artwork and scaled down to a size the UI actually uses. Re-run it
if the artwork is ever replaced.

Pillow is not a dependency of this project, so the PNG is decoded and
re-encoded here with zlib and numpy alone.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np


def read_png(path: Path) -> np.ndarray:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path} is not a PNG"
    pos, idat, ihdr = 8, [], None
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        kind = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            ihdr = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length

    width, height, depth, colour, comp, filt, interlace = ihdr
    assert depth == 8 and comp == 0 and filt == 0 and interlace == 0, "unsupported PNG variant"
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[colour]
    raw = zlib.decompress(b"".join(idat))

    stride = width * channels
    out = np.zeros((height, stride), dtype=np.uint8)
    prior = np.zeros(stride, dtype=np.uint8)
    offset = 0
    for y in range(height):
        method = raw[offset]
        line = np.frombuffer(raw[offset + 1:offset + 1 + stride], dtype=np.uint8).copy()
        offset += 1 + stride
        if method == 0:
            cur = line
        elif method == 1:                                   # Sub
            cur = line
            for x in range(channels, stride):
                cur[x] = (int(cur[x]) + int(cur[x - channels])) & 0xFF
        elif method == 2:                                   # Up
            cur = (line.astype(np.uint16) + prior.astype(np.uint16)).astype(np.uint8)
        elif method == 3:                                   # Average
            cur = line
            for x in range(stride):
                left = int(cur[x - channels]) if x >= channels else 0
                cur[x] = (int(cur[x]) + ((left + int(prior[x])) >> 1)) & 0xFF
        elif method == 4:                                   # Paeth
            cur = line
            for x in range(stride):
                a = int(cur[x - channels]) if x >= channels else 0
                b = int(prior[x])
                c = int(prior[x - channels]) if x >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                cur[x] = (int(cur[x]) + pred) & 0xFF
        else:
            raise ValueError(f"unknown filter {method}")
        out[y] = cur
        prior = cur

    return out.reshape(height, width, channels)


def write_png(path: Path, rgba: np.ndarray) -> None:
    height, width, _ = rgba.shape
    # Filter 1 (Sub) on every scanline: these are large flat areas, so it
    # compresses far better than storing raw bytes.
    body = bytearray()
    for y in range(height):
        line = rgba[y].reshape(-1).astype(np.int16)
        sub = np.empty_like(line)
        sub[:4] = line[:4]
        sub[4:] = (line[4:] - line[:-4]) & 0xFF
        body.append(1)
        body.extend(sub.astype(np.uint8).tobytes())

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + kind + payload
                + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(body), 9))
        + chunk(b"IEND", b"")
    )


def key_out_black(rgb: np.ndarray) -> np.ndarray:
    """Recover straight-alpha RGBA from artwork composited over black."""
    colour = rgb[:, :, :3].astype(np.float32)
    alpha = colour.max(axis=2)
    safe = np.maximum(alpha, 1.0)
    straight = np.clip(colour / safe[:, :, None] * 255.0, 0, 255)
    rgba = np.zeros(rgb.shape[:2] + (4,), dtype=np.uint8)
    rgba[:, :, :3] = straight.astype(np.uint8)
    rgba[:, :, 3] = alpha.astype(np.uint8)
    return rgba


def crop_to_content(rgba: np.ndarray, pad_ratio: float = 0.02) -> np.ndarray:
    """Trim the empty margin so the mark fills the box it is given."""
    mask = rgba[:, :, 3] > 6
    rows, cols = np.any(mask, axis=1), np.any(mask, axis=0)
    y0, y1 = np.argmax(rows), len(rows) - np.argmax(rows[::-1])
    x0, x1 = np.argmax(cols), len(cols) - np.argmax(cols[::-1])
    pad = int(round(max(y1 - y0, x1 - x0) * pad_ratio))
    y0, x0 = max(0, y0 - pad), max(0, x0 - pad)
    y1, x1 = min(rgba.shape[0], y1 + pad), min(rgba.shape[1], x1 + pad)
    return rgba[y0:y1, x0:x1]


def resize_area(rgba: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Area-average downscale, done on premultiplied colour.

    Averaging straight colour across a transparent edge drags the background
    colour into the fringe; premultiplying first is what keeps the edges clean.
    """
    src = rgba.astype(np.float32)
    alpha = src[:, :, 3:4] / 255.0
    pre = np.concatenate([src[:, :, :3] * alpha, src[:, :, 3:4]], axis=2)

    def axis_average(block: np.ndarray, axis: int, target: int) -> np.ndarray:
        length = block.shape[axis]
        edges = np.linspace(0, length, target + 1)
        pieces = []
        for i in range(target):
            lo, hi = int(np.floor(edges[i])), int(np.ceil(edges[i + 1]))
            hi = max(hi, lo + 1)
            piece = np.take(block, range(lo, min(hi, length)), axis=axis)
            pieces.append(piece.mean(axis=axis))
        return np.stack(pieces, axis=axis)

    pre = axis_average(pre, 0, out_h)
    pre = axis_average(pre, 1, out_w)

    out_alpha = np.clip(pre[:, :, 3:4], 0, 255)
    safe = np.maximum(out_alpha / 255.0, 1e-4)
    colour = np.clip(pre[:, :, :3] / safe, 0, 255)
    return np.concatenate([colour, out_alpha], axis=2).round().astype(np.uint8)


def derive(source: Path, target: Path, max_edge: int) -> None:
    rgba = crop_to_content(key_out_black(read_png(source)))
    height, width = rgba.shape[:2]
    scale = min(1.0, max_edge / max(width, height))
    if scale < 1.0:
        rgba = resize_area(rgba, max(1, round(width * scale)), max(1, round(height * scale)))
    write_png(target, rgba)
    print(f"{target.name}: {rgba.shape[1]}x{rgba.shape[0]}  {target.stat().st_size // 1024} KB")


DEFAULT_BRANDING = Path(__file__).resolve().parent.parent / "frontend" / "public" / "branding"


#: The voice overlay is a separate Electron window with its own strict CSP and
#: its own bundle path, so it cannot reach into frontend/public. It gets its own
#: small copy of the mark, generated from the same master.
OVERLAY_MARK = Path(__file__).resolve().parent.parent / "electron" / "overlay" / "nano-mark.png"


if __name__ == "__main__":
    branding = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_BRANDING
    derive(branding / "nano-mark.png", branding / "nano-mark-alpha.png", 384)
    derive(branding / "nano-wordmark.png", branding / "nano-wordmark-alpha.png", 720)
    if OVERLAY_MARK.parent.exists():
        derive(branding / "nano-mark.png", OVERLAY_MARK, 128)
