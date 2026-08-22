"""Instant local audio feedback for the voice loop.

The wake acknowledgment has one hard requirement: it must be *immediate*. The
user says "Hey Nano" and needs to know within a moment that Nano is listening,
otherwise they talk over the gap or repeat themselves.

That rules out the normal TTS path for the acknowledgment: edge-tts is a
network round trip (typically several hundred milliseconds, and it fails
outright offline). So the acknowledgment is a locally synthesised two-note
chime — no model, no network, no dependency beyond the standard library on
Windows. Spoken acknowledgment stays available as an option for anyone who
prefers it, but the chime is the default because it is what actually feels
instant.

Playback backends, in order of preference:
  1. winsound  — Windows built-in, zero dependencies, lowest latency
  2. pygame    — already a Nano dependency, used as the cross-platform fallback

Every failure is logged, never swallowed silently: a wake that the user cannot
hear is exactly the bug this module exists to prevent.
"""
from __future__ import annotations

import logging
import math
import os
import struct
import threading
import wave
from io import BytesIO

logger = logging.getLogger("nano.audio_feedback")

# Two short rising notes: recognisable, quick, not startling.
_WAKE_CHIME = ((880.0, 90), (1320.0, 130))   # A5 -> E6
_ERROR_CHIME = ((440.0, 120), (330.0, 160))  # falling, signals a problem

_SAMPLE_RATE = 44100


def _render_wav(notes: tuple[tuple[float, int], ...], volume: float = 0.35) -> bytes:
    """Build a small 16-bit mono WAV in memory. No file, no model, no network."""
    frames = bytearray()
    for frequency, milliseconds in notes:
        count = int(_SAMPLE_RATE * (milliseconds / 1000.0))
        for index in range(count):
            # Short linear fade in/out so the note does not click.
            fade = min(1.0, index / 240.0, (count - index) / 240.0)
            sample = math.sin(2.0 * math.pi * frequency * (index / _SAMPLE_RATE))
            frames += struct.pack("<h", int(sample * fade * volume * 32767))

    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(_SAMPLE_RATE)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


_CACHE: dict[str, bytes] = {}


def _chime_bytes(kind: str) -> bytes:
    if kind not in _CACHE:
        _CACHE[kind] = _render_wav(_ERROR_CHIME if kind == "error" else _WAKE_CHIME)
    return _CACHE[kind]


def _play_winsound(payload: bytes) -> bool:
    if os.name != "nt":
        return False
    try:
        import winsound

        winsound.PlaySound(payload, winsound.SND_MEMORY | winsound.SND_NODEFAULT)
        return True
    except Exception as exc:
        logger.debug("winsound playback failed: %s", exc)
        return False


def _play_pygame(payload: bytes) -> bool:
    try:
        import pygame

        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=1)
        pygame.mixer.Sound(buffer=payload).play()
        return True
    except Exception as exc:
        logger.debug("pygame playback failed: %s", exc)
        return False


def play_chime(kind: str = "wake", *, blocking: bool = False) -> bool:
    """Play a short local chime. Returns True if a backend accepted it."""
    payload = _chime_bytes(kind)

    def _run() -> bool:
        if _play_winsound(payload):
            return True
        if _play_pygame(payload):
            return True
        # Reaching here means the user got no audible feedback at all, which is
        # a real failure of the voice loop, not a cosmetic one.
        logger.warning("Nenhum backend de áudio conseguiu reproduzir o som de '%s'.", kind)
        return False

    if blocking:
        return _run()
    threading.Thread(target=_run, name="nano-chime", daemon=True).start()
    return True


def acknowledge_wake(blocking: bool = False) -> bool:
    """The sound the user hears the instant "Hey Nano" is detected."""
    return play_chime("wake", blocking=blocking)


def signal_error(blocking: bool = False) -> bool:
    return play_chime("error", blocking=blocking)


def output_device_report() -> dict:
    """What can actually play audio right now — measured, not assumed."""
    report: dict[str, object] = {"winsound": os.name == "nt", "pygame": False, "devices": []}
    try:
        import pygame

        report["pygame"] = True
        if not pygame.mixer.get_init():
            pygame.mixer.init(frequency=_SAMPLE_RATE, size=-16, channels=1)
        report["mixer_init"] = bool(pygame.mixer.get_init())
    except Exception as exc:
        report["pygame_error"] = str(exc)

    try:
        import pyaudio

        # Shares the process-wide PortAudio lock: initialising PyAudio here
        # while the wake-phrase thread is capturing crashes the process with an
        # access violation. Imported lazily to avoid an import cycle.
        from core.voice import _PORTAUDIO_LOCK, microphone_busy

        if microphone_busy():
            # A PyAudio instance created and terminated here would tear down
            # PortAudio underneath the live capture stream. Enumeration is a
            # diagnostic nicety; never risk the running voice loop for it.
            report["devices_skipped"] = "capture stream in use"
            return report

        with _PORTAUDIO_LOCK:
            pa = pyaudio.PyAudio()
            try:
                default = pa.get_default_output_device_info()
                report["default_output"] = {"index": default["index"], "name": str(default["name"])}
                report["devices"] = [
                    {"index": i, "name": str(pa.get_device_info_by_index(i)["name"])}
                    for i in range(pa.get_device_count())
                    if int(pa.get_device_info_by_index(i).get("maxOutputChannels") or 0) > 0
                ]
            finally:
                pa.terminate()
    except Exception as exc:
        report["pyaudio_error"] = str(exc)

    return report


__all__ = ["acknowledge_wake", "output_device_report", "play_chime", "signal_error"]
