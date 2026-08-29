"""Displays, and their brightness where the hardware actually supports it.

BRIGHTNESS IS REPORTED HONESTLY OR NOT AT ALL.

Windows has no universal software brightness control. Laptop panels answer to
WMI; external monitors answer to DDC/CI over the display cable; a great many
answer to neither. So this module asks the monitor, through the documented
Monitor Configuration API, and:

* if the monitor reports a brightness range, that range is used, with the
  monitor's OWN minimum and maximum rather than an assumed 0-100;
* if it does not, the result is ``unsupported`` -- a plain "this screen does
  not let software change its brightness". Nothing is faked, and no gamma-ramp
  trick is substituted. Washing out the colours of the whole desktop is not
  dimming the backlight, and reporting it as if it were would be a lie about
  what happened.

Every write re-reads the monitor afterwards, so what is reported is the value
the panel is actually at.
"""
from __future__ import annotations

import logging
import math
import time

from core.pc_control import winapi
from core.pc_control.geometry import monitors, resolve_monitor
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.display")

MIN_PERCENT = 0
MAX_PERCENT = 100
DEFAULT_STEP = 10


def _percent(value, field: str) -> int:
    """A finite 0-100 integer. NaN and infinity are refused, never clamped."""
    if isinstance(value, bool) or value is None:
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número.") from None
    if math.isnan(number) or math.isinf(number):
        raise PCControlError("invalid_input",
                             f"O valor de '{field}' não é um número válido.")
    if not (MIN_PERCENT <= number <= MAX_PERCENT):
        raise PCControlError(
            "invalid_input",
            f"O brilho tem de estar entre {MIN_PERCENT} e {MAX_PERCENT} "
            f"(recebido {number:g}).")
    return int(round(number))


def _delta(value) -> int:
    if value is None:
        return DEFAULT_STEP
    if isinstance(value, bool):
        raise PCControlError("invalid_input", "A variação de brilho não é um número.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input", "A variação de brilho não é um número.") from None
    if math.isnan(number) or math.isinf(number):
        raise PCControlError("invalid_input", "A variação de brilho não é um número válido.")
    if abs(number) > MAX_PERCENT:
        raise PCControlError(
            "invalid_input",
            f"A variação de brilho tem de estar entre -{MAX_PERCENT} e {MAX_PERCENT}.")
    return int(round(number))


def _to_percent(raw: int, low: int, high: int) -> int:
    span = high - low
    if span <= 0:
        return 0
    return int(round((raw - low) * 100.0 / span))


def _from_percent(percent: int, low: int, high: int) -> int:
    span = high - low
    return int(round(low + (span * max(0, min(100, percent)) / 100.0)))


def brightness_of(monitor: dict) -> dict | None:
    """The monitor's brightness as a percentage, or None when unsupported."""
    reading = winapi.monitor_brightness(monitor["handle"])
    if reading is None:
        return None
    low, current, high = reading
    if high <= low:
        return None
    return {"percent": _to_percent(current, low, high),
            "raw": current, "raw_min": low, "raw_max": high}


def info() -> dict:
    """Every display, its geometry, and whether its brightness is controllable."""
    described = []
    for monitor in monitors():
        entry = {
            "number": monitor["number"],
            "primary": monitor["primary"],
            "width": monitor["width"],
            "height": monitor["height"],
            "work_width": monitor["work_width"],
            "work_height": monitor["work_height"],
            "position": {"x": monitor["bounds"][0], "y": monitor["bounds"][1]},
        }
        reading = brightness_of(monitor)
        entry["brightness_supported"] = reading is not None
        if reading is not None:
            entry["brightness_percent"] = reading["percent"]
        described.append(entry)
    return {"monitors": described, "count": len(described)}


def get_brightness(monitor_number: int | None = None) -> dict:
    monitor = resolve_monitor(monitor_number)
    reading = brightness_of(monitor)
    if reading is None:
        raise PCControlError(
            "unsupported",
            f"O monitor {monitor['number']} não permite alterar o brilho por software.",
            monitor=monitor["number"])
    return {"monitor": monitor["number"], "level": reading["percent"],
            "raw": reading["raw"], "raw_min": reading["raw_min"],
            "raw_max": reading["raw_max"]}


def _write(monitor: dict, reading: dict, percent: int) -> dict:
    raw = _from_percent(percent, reading["raw_min"], reading["raw_max"])
    if not winapi.set_monitor_brightness(monitor["handle"], raw):
        raise PCControlError(
            "failed",
            f"O monitor {monitor['number']} recusou a alteração de brilho.",
            monitor=monitor["number"])
    # DDC/CI is a slow serial channel; give the panel a moment before asking it
    # what it settled on. The re-read is what makes the reported value real.
    time.sleep(0.15)
    after = brightness_of(monitor)
    return {
        "monitor": monitor["number"],
        "level": after["percent"] if after else percent,
        "previous_level": reading["percent"],
        "requested": percent,
        "verified": after is not None,
    }


def set_brightness(level, monitor_number: int | None = None) -> dict:
    percent = _percent(level, "level")
    monitor = resolve_monitor(monitor_number)
    reading = brightness_of(monitor)
    if reading is None:
        raise PCControlError(
            "unsupported",
            f"O monitor {monitor['number']} não permite alterar o brilho por software.",
            monitor=monitor["number"])
    return _write(monitor, reading, percent)


def change_brightness(delta, monitor_number: int | None = None) -> dict:
    """Move brightness by ``delta`` points; the RESULT is clamped to 0-100."""
    step = _delta(delta)
    monitor = resolve_monitor(monitor_number)
    reading = brightness_of(monitor)
    if reading is None:
        raise PCControlError(
            "unsupported",
            f"O monitor {monitor['number']} não permite alterar o brilho por software.",
            monitor=monitor["number"])
    target = max(MIN_PERCENT, min(MAX_PERCENT, reading["percent"] + step))
    result = _write(monitor, reading, target)
    result["delta"] = step
    result["clamped"] = target != reading["percent"] + step
    return result


__all__ = [
    "DEFAULT_STEP",
    "MAX_PERCENT",
    "MIN_PERCENT",
    "brightness_of",
    "change_brightness",
    "get_brightness",
    "info",
    "set_brightness",
]
