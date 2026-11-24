"""Is the person at the PC, and are they busy? Read from Windows, locally.

Two readings, both used by the notice gate (core/notice_gate.py) so NANO does
not talk to an empty room or over a game:

* :func:`idle_seconds` — how long since the last key press or mouse move
  (``GetLastInputInfo``). NANO never sees WHICH key; Windows only says when.
* :func:`busy_state` — what Windows itself says about notifications right now
  (``SHQueryUserNotificationState``): a full-screen game or video, a
  presentation, or Windows' own quiet time. The same signal Windows uses to
  hold its own notifications back.

Both return ``None`` when they cannot be told (another OS, or an error), and
every caller treats ``None`` as "present and not busy": when in doubt, NANO
behaves exactly as it did before this module existed.
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger("nano.presence")

#: SHQueryUserNotificationState values (shellapi.h).
QUNS_NOT_PRESENT = 1
QUNS_BUSY = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
QUNS_PRESENTATION_MODE = 4
QUNS_ACCEPTS_NOTIFICATIONS = 5
QUNS_QUIET_TIME = 6
QUNS_APP = 7

#: What each busy value means, as a short reason the gate can log.
_BUSY = {
    QUNS_BUSY: "fullscreen",
    QUNS_RUNNING_D3D_FULL_SCREEN: "game",
    QUNS_PRESENTATION_MODE: "presentation",
}


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        now = ctypes.windll.kernel32.GetTickCount() & 0xFFFFFFFF
        # Both are 32-bit millisecond counters that wrap every 49.7 days.
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0
    except Exception:
        logger.debug("GetLastInputInfo failed", exc_info=True)
        return None


def busy_state() -> str | None:
    """"game", "fullscreen" or "presentation" while Windows would hold its own
    notifications back for one of those; "" when not busy; None if unknown."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) != 0:
            return None
        return _BUSY.get(int(state.value), "")
    except Exception:
        logger.debug("SHQueryUserNotificationState failed", exc_info=True)
        return None


__all__ = ["busy_state", "idle_seconds"]
