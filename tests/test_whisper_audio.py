"""core/whisper_audio: recorded WAV goes to faster-whisper as samples.

Regression for 2026-11-24: faster-whisper 1.2.1 opens a file path with
``av.open(..., metadata_errors=...)``, which av 19 rejects, so every
transcription failed with a TypeError. NANO now decodes its own WAV.
"""
from __future__ import annotations

import io
import math
import struct
import wave

import pytest

from core import whisper_audio

np = pytest.importorskip("numpy")


def _wav(rate: int, channels: int = 1, seconds: float = 0.5, width: int = 2) -> bytes:
    frames = int(rate * seconds)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(width)
        out.setframerate(rate)
        data = bytearray()
        for n in range(frames):
            value = math.sin(2 * math.pi * 220 * n / rate) * 0.5
            for _ in range(channels):
                if width == 2:
                    data += struct.pack("<h", int(value * 32767))
                else:
                    data += struct.pack("B", int(value * 127 + 128))
        out.writeframes(bytes(data))
    return buffer.getvalue()


def test_16k_mono_wav_becomes_float32_samples_in_range():
    samples = whisper_audio.wav_samples(_wav(16000))
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert samples.size == 8000
    assert 0.45 < float(np.abs(samples).max()) <= 0.5


@pytest.mark.parametrize("rate", [48000, 44100, 22050, 8000])
def test_other_rates_are_resampled_to_16k(rate):
    samples = whisper_audio.wav_samples(_wav(rate))
    assert abs(samples.size - 8000) <= 2
    assert samples.dtype == np.float32


def test_stereo_is_mixed_to_mono():
    samples = whisper_audio.wav_samples(_wav(16000, channels=2))
    assert samples.ndim == 1 and samples.size == 8000


def test_8_bit_wav_is_centred():
    samples = whisper_audio.wav_samples(_wav(16000, width=1))
    assert abs(float(samples.mean())) < 0.05


def test_not_a_wav_returns_none_so_the_caller_keeps_the_path_route():
    assert whisper_audio.wav_samples(b"not audio at all") is None
    assert whisper_audio.wav_file_samples("Z:/no/such/file.wav") is None


def test_provider_passes_samples_not_a_path(monkeypatch):
    from core import voice

    seen = {}

    class FakeModel:
        def transcribe(self, source, **options):
            seen["source"] = source
            return iter([type("S", (), {"text": " olá "})()]), None

    provider = voice.LocalSTTProvider({"model": "tiny"})
    provider.online = True
    monkeypatch.setattr(provider, "_ensure_model", lambda: FakeModel())
    result = provider.transcribe(_wav(48000))
    assert result.ok and result.text == "olá"
    assert isinstance(seen["source"], np.ndarray)
    assert seen["source"].size == 8000
