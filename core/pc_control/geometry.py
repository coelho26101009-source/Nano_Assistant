"""Where a window is allowed to end up, and how it gets there.

THE RULE THIS MODULE ENFORCES: a coordinate from the model is a REQUEST, not
an address.

Every number that arrives here is finite-checked, then clamped against the real
work area of a real monitor read from ``EnumDisplayMonitors``. Nothing is
written to the screen at a position that was not first proved to intersect a
display, so a window can never be parked entirely off-screen where the user
cannot reach it -- which on Windows is effectively losing it, because a window
with no visible titlebar cannot be dragged back.

Snapping and centring do not take coordinates at all. They take an enum and a
monitor, and the geometry is computed here from the work area, so the ordinary
"put Discord on the left" path never involves a model-chosen pixel.

The work area, not the monitor rect: the difference is the taskbar, and a
window snapped to the monitor rect sits underneath it.
"""
from __future__ import annotations

import logging
import math
import time

from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.geometry")

#: A window smaller than this is not usable with a mouse.
MIN_WINDOW_WIDTH = 200
MIN_WINDOW_HEIGHT = 120

#: How much of a moved window must remain inside some display. A window may
#: legitimately hang off an edge; it may not vanish.
MIN_VISIBLE_WIDTH = 120
MIN_VISIBLE_HEIGHT = 80

#: Coordinates outside this are not a display position, they are a mistake.
COORDINATE_LIMIT = 32_000

SNAP_MODES = (
    "left", "right", "top", "bottom",
    "top_left", "top_right", "bottom_left", "bottom_right",
)


def _finite(value, field: str) -> float:
    """Accept a real, finite number. NaN and infinity are rejected, not coerced.

    Clamping NaN would silently become "0", i.e. the top-left corner, for a
    malformed argument. A malformed argument is an error and is reported as one.
    """
    if isinstance(value, bool) or value is None:
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input",
                             f"O valor de '{field}' não é um número.") from None
    if math.isnan(number) or math.isinf(number):
        raise PCControlError("invalid_input",
                             f"O valor de '{field}' não é um número válido.")
    if abs(number) > COORDINATE_LIMIT:
        raise PCControlError(
            "invalid_input",
            f"O valor de '{field}' está fora do ecrã (limite ±{COORDINATE_LIMIT}).")
    return number


def coordinate(value, field: str) -> int:
    return int(round(_finite(value, field)))


def monitors() -> list[dict]:
    """Every display, numbered from 1 in left-to-right, top-to-bottom order.

    The number is what a person says out loud ("no monitor 2"). It is derived
    from position rather than from the OS enumeration order, because the OS
    order is arbitrary and would make "monitor 2" mean different screens on
    different days.
    """
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "A geometria de janelas só funciona no Windows.")
    found = winapi.enum_monitors()
    if not found:
        raise PCControlError("unsupported", "Não consegui enumerar os monitores.")
    found.sort(key=lambda m: (m["bounds"][1], m["bounds"][0]))
    described = []
    for index, monitor in enumerate(found, start=1):
        left, top, right, bottom = monitor["bounds"]
        work = monitor["work_area"]
        described.append({
            "number": index,
            "handle": monitor["handle"],
            "primary": monitor["primary"],
            "width": right - left,
            "height": bottom - top,
            "bounds": list(monitor["bounds"]),
            "work_area": list(work),
            "work_width": work[2] - work[0],
            "work_height": work[3] - work[1],
        })
    return described


def resolve_monitor(number: int | None = None, *, handle: int | None = None) -> dict:
    """One monitor, by the number a person would say, or by handle."""
    available = monitors()
    if handle is not None:
        for monitor in available:
            if monitor["handle"] == int(handle):
                return monitor
    if number is None:
        return next((m for m in available if m["primary"]), available[0])
    try:
        wanted = int(number)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input", "O número do monitor não é válido.") from None
    for monitor in available:
        if monitor["number"] == wanted:
            return monitor
    raise PCControlError(
        "not_found",
        f"Só há {len(available)} monitor(es); não existe o monitor {wanted}.",
        monitors=[{"number": m["number"], "width": m["width"], "height": m["height"],
                   "primary": m["primary"]} for m in available])


def monitor_for_window(hwnd: int) -> dict:
    handle = winapi.monitor_handle_for_window(hwnd)
    available = monitors()
    for monitor in available:
        if monitor["handle"] == handle:
            return monitor
    return next((m for m in available if m["primary"]), available[0])


def clamp_geometry(x: int, y: int, width: int, height: int,
                   *, monitor: dict | None = None) -> dict:
    """Force a requested rectangle to be a rectangle the user can still use.

    Two independent guarantees, in this order:

    1. The window is at least usable in size -- a 4x4 window is not a window.
    2. Enough of it overlaps SOME display that it can be grabbed and moved.
       This is checked against the union of every monitor's work area, so a
       perfectly legitimate "put it on the second screen" is not clamped back
       to the first one.

    Returns the geometry that will actually be applied plus whether anything
    had to change, so the caller can say so instead of silently doing something
    other than what was asked.
    """
    available = monitors()
    target = monitor or available[0]

    requested = (int(x), int(y), int(width), int(height))

    width = max(MIN_WINDOW_WIDTH, int(width))
    height = max(MIN_WINDOW_HEIGHT, int(height))
    # Never larger than the monitor it is being placed on.
    width = min(width, max(MIN_WINDOW_WIDTH, target["work_width"]))
    height = min(height, max(MIN_WINDOW_HEIGHT, target["work_height"]))

    x, y = int(x), int(y)
    if not _intersects_any(x, y, width, height, available):
        # Pull it back onto the target monitor's work area rather than
        # refusing: the intent ("move it there") is clear, the coordinates are
        # simply out of range.
        left, top, right, bottom = target["work_area"]
        x = min(max(x, left), max(left, right - width))
        y = min(max(y, top), max(top, bottom - height))

    applied = (x, y, width, height)
    return {
        "x": x, "y": y, "width": width, "height": height,
        "clamped": applied != requested,
        "requested": {"x": requested[0], "y": requested[1],
                      "width": requested[2], "height": requested[3]},
    }


def _intersects_any(x: int, y: int, width: int, height: int,
                    available: list[dict]) -> bool:
    """Whether enough of this rectangle lands on a display to remain reachable."""
    for monitor in available:
        left, top, right, bottom = monitor["work_area"]
        overlap_w = min(x + width, right) - max(x, left)
        overlap_h = min(y + height, bottom) - max(y, top)
        if (overlap_w >= min(MIN_VISIBLE_WIDTH, width)
                and overlap_h >= min(MIN_VISIBLE_HEIGHT, height)):
            return True
    return False


def snap_rect(mode: str, monitor: dict) -> tuple[int, int, int, int]:
    """The rectangle for a named half or quarter of one monitor's work area."""
    key = str(mode or "").strip().lower()
    if key not in SNAP_MODES:
        raise PCControlError(
            "invalid_input",
            f"'{mode}' não é uma posição conhecida.", allowed=list(SNAP_MODES))

    left, top, right, bottom = monitor["work_area"]
    half_w = (right - left) // 2
    half_h = (bottom - top) // 2
    full_w = right - left
    full_h = bottom - top

    layout = {
        "left":         (left,          top,          half_w, full_h),
        "right":        (left + half_w, top,          full_w - half_w, full_h),
        "top":          (left,          top,          full_w, half_h),
        "bottom":       (left,          top + half_h, full_w, full_h - half_h),
        "top_left":     (left,          top,          half_w, half_h),
        "top_right":    (left + half_w, top,          full_w - half_w, half_h),
        "bottom_left":  (left,          top + half_h, half_w, full_h - half_h),
        "bottom_right": (left + half_w, top + half_h, full_w - half_w, full_h - half_h),
    }
    return layout[key]


def current_rect(hwnd: int) -> dict:
    left, top, right, bottom = winapi.window_rect(hwnd)
    return {"x": left, "y": top, "width": right - left, "height": bottom - top}


def apply_geometry(hwnd: int, x: int, y: int, width: int, height: int,
                   *, monitor: dict | None = None) -> dict:
    """Place a window, then RE-READ where it ended up.

    Windows is entitled to ignore or adjust a request: a window with a minimum
    size, a maximised window, an application that repositions itself. The
    reported result is the rectangle the OS actually has, never the one that
    was asked for, and ``moved`` says whether anything changed at all.
    """
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "A geometria de janelas só funciona no Windows.")

    plan = clamp_geometry(x, y, width, height, monitor=monitor)
    before = current_rect(hwnd)

    # A maximised window ignores SetWindowPos geometry; restore it first so the
    # move is real rather than a no-op reported as success.
    if winapi.window_placement_state(hwnd) in {"maximized", "minimized"}:
        winapi.show_window(hwnd, winapi.SW_RESTORE)
        time.sleep(0.06)

    winapi.set_window_position(hwnd, plan["x"], plan["y"], plan["width"], plan["height"])
    time.sleep(0.06)
    after = current_rect(hwnd)

    return {
        "before": before,
        "after": after,
        "requested": plan["requested"],
        "clamped": plan["clamped"],
        "moved": after != before,
        "exact": (after["x"] == plan["x"] and after["y"] == plan["y"]
                  and after["width"] == plan["width"]
                  and after["height"] == plan["height"]),
        "monitor": (monitor or monitor_for_window(hwnd))["number"],
    }


def set_topmost(hwnd: int, topmost: bool) -> dict:
    """Pin or unpin a window, and read the style back to check it took."""
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Só funciona no Windows.")
    was = winapi.is_window_topmost(hwnd)
    winapi.set_window_topmost(hwnd, bool(topmost))
    time.sleep(0.05)
    now = winapi.is_window_topmost(hwnd)
    return {"topmost": now, "was_topmost": was, "changed": now != was,
            "applied": now == bool(topmost)}


__all__ = [
    "COORDINATE_LIMIT",
    "MIN_VISIBLE_HEIGHT",
    "MIN_VISIBLE_WIDTH",
    "MIN_WINDOW_HEIGHT",
    "MIN_WINDOW_WIDTH",
    "SNAP_MODES",
    "apply_geometry",
    "clamp_geometry",
    "coordinate",
    "current_rect",
    "monitor_for_window",
    "monitors",
    "resolve_monitor",
    "set_topmost",
    "snap_rect",
]
