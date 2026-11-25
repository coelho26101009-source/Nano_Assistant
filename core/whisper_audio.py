"""Hand recorded WAV audio to faster-whisper as samples, not as a file path.

WHY
---
Given a file path, faster-whisper 1.2.1 decodes it with PyAV and calls
``av.open(..., metadata_errors="ignore")``. The PyAV in our lock (av 19) no
longer has that argument, so EVERY transcription of a path failed with
``TypeError: open() got an unexpected keyword argument 'metadata_errors'``:
NANO heard you and then understood nothing. Found on Simão's PC on
2026-11-24 (tests/test_speech_benchmark.py, which only runs where the speech
pack is installed).

NANO always records 16-bit PCM WAV itself, so it can decode that with the
standard ``wave`` module and pass faster-whisper what it accepts natively: a
float32 mono numpy array at 16 kHz. PyAV is then not involved at all.
Anything that is not such a WAV returns None and the caller keeps the old
path-based route.
"""
from __future__ import annotations

import io
import wave
from typing import Any

#: faster-whisper's own sampling rate for numpy input.
WHISPER_RATE = 16000


def _resample(samples: Any, rate: int) -> Any:
    import numpy as np

    if rate == WHISPER_RATE or samples.size == 0:
        return samples
    try:  # Best quality when scipy is there (it comes with openWakeWord).
        from math import gcd

        from scipy.signal import resample_poly  # type: ignore

        g = gcd(WHISPER_RATE, rate)
        return resample_poly(samples, WHISPER_RATE // g, rate // g).astype(np.float32)
    except Exception:
        pass
    if rate > WHISPER_RATE:
        # Cheap low-pass before dropping samples, so high frequencies do not
        # fold back into the speech band.
        width = max(1, int(round(rate / WHISPER_RATE)))
        if width > 1:
            samples = np.convolve(samples, np.ones(width, dtype=np.float32) / width, mode="same")
    count = max(1, int(round(samples.size * WHISPER_RATE / rate)))
    positions = np.linspace(0, samples.size - 1, count)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def wav_samples(audio: bytes) -> Any | None:
    """16 kHz mono float32 samples for faster-whisper, or None if not a PCM WAV."""
    try:
        import numpy as np

        with wave.open(io.BytesIO(audio)) as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            raw = handle.readframes(handle.getnframes())
    except Exception:
        return None
    if width == 2:
        samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        samples = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        return None
    if channels > 1:
        usable = samples.size - samples.size % channels
        samples = samples[:usable].reshape(-1, channels).mean(axis=1)
    if rate <= 0:
        return None
    return np.ascontiguousarray(_resample(samples, rate), dtype=np.float32)


def wav_file_samples(path: str) -> Any | None:
    """Same as wav_samples, for a WAV on disk."""
    try:
        with open(path, "rb") as handle:
            return wav_samples(handle.read())
    except OSError:
        return None
