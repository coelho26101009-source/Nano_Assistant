"""Master volume, through the Windows audio endpoint API.

No PowerShell. The existing `god_mode.system_volume` tool shells out to build a
script string, and its non-nircmd fallback allocates a buffer, copies bytes
into it and returns success WITHOUT CHANGING THE VOLUME AT ALL -- a tool that
reports a result it did not produce. This module talks to
IAudioEndpointVolume directly (see winapi.AudioEndpoint) and every operation
re-reads the level afterwards, so what is reported is what the device is
actually set to.

NUMERIC CONTRACT, stated once and tested:

* Levels are integers 0-100. NaN, infinity and non-numeric input are REJECTED
  (``invalid_input``) rather than coerced -- silently turning NaN into 0 would
  mute the machine on a malformed argument.
* Deltas are rejected outside -100..100, then the RESULT is clamped to 0-100.
  "Aumenta 10" at 95 lands on 100; it is not an error.
* The default step, when the user gives no number, is 10 points.
"""
from __future__ import annotations

import logging
import math

from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.audio")

#: The step used when the user says "baixa o volume" with no number.
DEFAULT_STEP = 10

MIN_LEVEL = 0
MAX_LEVEL = 100
MAX_DELTA = 100


def _coerce_number(value, field: str) -> float:
    """Accept a real, finite number. Reject everything else, loudly."""
    if isinstance(value, bool) or value is None:
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número.") from None
    if math.isnan(number) or math.isinf(number):
        # Never coerced to a bound: NaN reaching a volume setter would be an
        # arbitrary, unintended change to the user's audio.
        raise PCControlError("invalid_input", f"O valor de '{field}' não é um número válido.")
    return number


def parse_level(value) -> int:
    number = _coerce_number(value, "level")
    if not (MIN_LEVEL <= number <= MAX_LEVEL):
        raise PCControlError(
            "invalid_input",
            f"O volume tem de estar entre {MIN_LEVEL} e {MAX_LEVEL} (recebido {number:g}).")
    return int(round(number))


def parse_delta(value) -> int:
    if value is None:
        return DEFAULT_STEP
    number = _coerce_number(value, "delta")
    if abs(number) > MAX_DELTA:
        raise PCControlError(
            "invalid_input",
            f"A variação de volume tem de estar entre -{MAX_DELTA} e {MAX_DELTA}.")
    return int(round(number))


def _endpoint():
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "O controlo de volume só funciona no Windows.")
    try:
        return winapi.AudioEndpoint()
    except Exception as exc:
        logger.warning("audio endpoint unavailable: %s", exc)
        raise PCControlError("audio_unavailable",
                             "Não consegui aceder ao dispositivo de áudio.") from exc


def get_state() -> dict:
    try:
        with _endpoint() as endpoint:
            return {"level": int(round(endpoint.get_level() * 100)), "muted": endpoint.get_mute()}
    except PCControlError:
        raise
    except Exception as exc:
        logger.warning("could not read volume: %s", exc)
        raise PCControlError("audio_unavailable", "Não consegui ler o volume actual.") from exc


def set_level(level: int) -> dict:
    """Set the master volume and report the level the device ended up at."""
    wanted = parse_level(level)
    try:
        with _endpoint() as endpoint:
            previous = int(round(endpoint.get_level() * 100))
            endpoint.set_level(wanted / 100.0)
            # Re-read: the device is the authority on its own level, not us.
            actual = int(round(endpoint.get_level() * 100))
            return {"level": actual, "previous_level": previous,
                    "requested": wanted, "muted": endpoint.get_mute()}
    except PCControlError:
        raise
    except Exception as exc:
        logger.warning("could not set volume: %s", exc)
        raise PCControlError("audio_failed", "Não consegui alterar o volume.") from exc


def change_level(delta) -> dict:
    """Move the volume by ``delta`` points, clamped to 0-100 at the end."""
    step = parse_delta(delta)
    try:
        with _endpoint() as endpoint:
            previous = int(round(endpoint.get_level() * 100))
            target = max(MIN_LEVEL, min(MAX_LEVEL, previous + step))
            endpoint.set_level(target / 100.0)
            actual = int(round(endpoint.get_level() * 100))
            return {"level": actual, "previous_level": previous, "delta": step,
                    "clamped": target != previous + step, "muted": endpoint.get_mute()}
    except PCControlError:
        raise
    except Exception as exc:
        logger.warning("could not change volume: %s", exc)
        raise PCControlError("audio_failed", "Não consegui alterar o volume.") from exc


def set_mute(muted: bool) -> dict:
    try:
        with _endpoint() as endpoint:
            was_muted = endpoint.get_mute()
            endpoint.set_mute(bool(muted))
            return {"muted": endpoint.get_mute(), "was_muted": was_muted,
                    "level": int(round(endpoint.get_level() * 100))}
    except PCControlError:
        raise
    except Exception as exc:
        logger.warning("could not change mute: %s", exc)
        raise PCControlError("audio_failed", "Não consegui alterar o silêncio.") from exc


__all__ = ["DEFAULT_STEP", "MAX_DELTA", "MAX_LEVEL", "MIN_LEVEL", "change_level",
           "get_state", "parse_delta", "parse_level", "set_level", "set_mute"]
