"""The pure-Python RMS used when ``audioop`` is missing (Python 3.13+).

``core.speech_filter`` gates every microphone chunk on its energy. It used to
``import audioop`` unconditionally, so on an interpreter without it the module
-- and the voice pipeline that imports it -- failed to load at all.
"""
from __future__ import annotations

import io
import random
import struct
import wave
import warnings

import pytest

from core import speech_filter


def _reference_rms(fragment: bytes, width: int) -> int:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        audioop = pytest.importorskip("audioop")
    return audioop.rms(fragment, width)


@pytest.mark.parametrize("width", [1, 2, 4])
def test_fallback_matches_audioop(width):
    rng = random.Random(width)
    limit = 2 ** (8 * width - 1)
    fmt = {1: "b", 2: "h", 4: "i"}[width]
    for length in (1, 7, 480, 4096):
        values = [rng.randrange(-limit, limit) for _ in range(length)]
        fragment = struct.pack(f"<{length}{fmt}", *values)
        assert speech_filter._rms_pure(fragment, width) == _reference_rms(fragment, width)


def test_fallback_handles_empty_and_rejects_unknown_widths():
    assert speech_filter._rms_pure(b"", 2) == 0
    assert speech_filter._rms_pure(b"\x01", 2) == 0
    with pytest.raises(ValueError):
        speech_filter._rms_pure(b"\x00\x00\x00", 3)


def test_energy_gate_works_without_audioop(monkeypatch):
    monkeypatch.setattr(speech_filter, "_audioop", None)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(struct.pack("<16000h", *([3000, -3000] * 8000)))
    loud = buffer.getvalue()
    assert speech_filter.rms_of_wav(loud) == pytest.approx(3000.0)
    assert speech_filter.has_speech_energy(loud) is True
