"""The "butler" style for NANO's offline English voice: calm, full, a touch of room.

WHY
---
Simão wanted NANO to sound like the film butler-AI: a calm, elegant British
voice. The real film voice belongs to an actor, so NANO does not copy it and
never trains on it. Instead it starts from a free voice it already ships
(Kokoro "George", British, Apache-2.0) and gives it a style:

* a little slower (``SPEED``), so it sounds unhurried;
* an equaliser: less boom under 200 Hz, more body between 500 Hz and 2 kHz,
  a little presence at 3.5 kHz, softer top. Measured on 2026-11-26 against the
  voice Simão liked, this moved George from 0.47 to 0.53 average speaker
  similarity (two embedding models), the closest of 28 free candidates, and
  Whisper still transcribed every word;
* three faint early reflections (11, 23, 37 ms): the sound of a voice in a
  room, not an echo.

HOW
---
Pure Python on purpose: the offline voice pack installs only sherpa-onnx, so
NumPy is not guaranteed on the PC. Five biquad filters (Robert Bristow-Johnson's
audio EQ cookbook) run sample by sample; about 0.02 s of work per second of
speech (measured in the cloud container), done in the same worker thread as the synthesis.

The input is the float samples (-1…1) sherpa-onnx returns; the output is a new
list of floats with the same loudness (RMS) as the input, so the butler is as
loud as plain George and a short "Yes, sir." is not made louder than the
sentence before it. If that pushes a peak above ``PEAK`` the piece is turned
down to it, so nothing clips. Nothing here
reads files, the network or settings: samples in, samples out.
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

#: Speed factor for the style (multiplies the person's own speed setting).
SPEED = 0.93
#: Loudest sample after the style.
PEAK = 0.9
#: (delay in milliseconds, gain) of each early reflection.
REFLECTIONS: tuple[tuple[int, float], ...] = ((11, 0.10), (23, 0.06), (37, 0.035))
#: Tail kept after the last sample so the reflections are not cut off.
TAIL_SECONDS = 0.06

Coefficients = tuple[float, float, float, float, float]


def _normalised(b0: float, b1: float, b2: float, a0: float, a1: float, a2: float) -> Coefficients:
    return (b0 / a0, b1 / a0, b2 / a0, a1 / a0, a2 / a0)


def highpass(freq: float, q: float, rate: int) -> Coefficients:
    w = 2 * math.pi * freq / rate
    alpha = math.sin(w) / (2 * q)
    cos = math.cos(w)
    return _normalised((1 + cos) / 2, -(1 + cos), (1 + cos) / 2, 1 + alpha, -2 * cos, 1 - alpha)


def peaking(freq: float, gain_db: float, q: float, rate: int) -> Coefficients:
    a = 10 ** (gain_db / 40)
    w = 2 * math.pi * freq / rate
    alpha = math.sin(w) / (2 * q)
    cos = math.cos(w)
    return _normalised(1 + alpha * a, -2 * cos, 1 - alpha * a, 1 + alpha / a, -2 * cos, 1 - alpha / a)


def highshelf(freq: float, gain_db: float, rate: int) -> Coefficients:
    a = 10 ** (gain_db / 40)
    w = 2 * math.pi * freq / rate
    cos = math.cos(w)
    alpha = math.sin(w) / 2 * math.sqrt(2)  # shelf slope 1
    root = 2 * math.sqrt(a) * alpha
    return _normalised(a * ((a + 1) + (a - 1) * cos + root),
                       -2 * a * ((a - 1) + (a + 1) * cos),
                       a * ((a + 1) + (a - 1) * cos - root),
                       (a + 1) - (a - 1) * cos + root,
                       2 * ((a - 1) - (a + 1) * cos),
                       (a + 1) - (a - 1) * cos - root)


def butler_filters(rate: int) -> list[Coefficients]:
    """The equaliser, for one sample rate. Frequencies above Nyquist are skipped."""
    nyquist = rate / 2
    filters = [highpass(80.0, 0.707, rate),
               peaking(180.0, -3.0, 1.0, rate),
               peaking(1200.0, 4.0, 0.7, rate),
               peaking(3500.0, 2.0, 1.0, rate)]
    if 9000.0 < nyquist * 0.9:
        filters.append(highshelf(9000.0, -3.0, rate))
    return filters


def _filter(samples: Iterable[float], coefficients: Coefficients) -> list[float]:
    b0, b1, b2, a1, a2 = coefficients
    x1 = x2 = y1 = y2 = 0.0
    out: list[float] = []
    append = out.append
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, x
        y2, y1 = y1, y
        append(y)
    return out


def butler(samples: Sequence[float], rate: int) -> list[float]:
    """The butler style applied to ``samples`` (floats, -1…1) at ``rate`` Hz."""
    rate = int(rate)
    if rate <= 0 or len(samples) == 0:
        return [float(x) for x in samples]
    shaped: list[float] = [float(x) for x in samples]
    for coefficients in butler_filters(rate):
        shaped = _filter(shaped, coefficients)
    delays = [(int(rate * delay_ms / 1000), gain) for delay_ms, gain in REFLECTIONS]
    tail = max([int(rate * TAIL_SECONDS)] + [delay for delay, _gain in delays])
    out = shaped + [0.0] * tail
    for delay, gain in delays:
        for index, value in enumerate(shaped):
            out[index + delay] += gain * value
    # Same loudness as the voice that came in: cutting the boom must not make
    # the butler quieter than plain George (measured: 30 % lower RMS without).
    energy_in = math.fsum(float(x) * float(x) for x in samples)
    energy_out = math.fsum(x * x for x in out)
    if energy_out > 0.0 and math.isfinite(energy_in) and math.isfinite(energy_out):
        makeup = math.sqrt(energy_in / energy_out) * math.sqrt(len(out) / len(samples))
        out = [x * makeup for x in out]
    loudest = 0.0
    for value in out:
        size = abs(value)
        if not size <= 1e9:  # NaN and infinity both fail this test: never play them
            return [0.0] * len(out)
        if size > loudest:
            loudest = size
    if loudest <= PEAK:
        return out
    scale = PEAK / loudest
    return [x * scale for x in out]


STYLES = {"butler": butler}

__all__ = ["PEAK", "REFLECTIONS", "SPEED", "STYLES", "butler", "butler_filters"]
