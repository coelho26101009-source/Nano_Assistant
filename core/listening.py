"""Hearing when someone starts and stops talking.

Two problems this module solves for the voice turn and the wake phrase:

1. **The fixed recording window.** A voice turn used to record for a fixed
   ``wake_command_timeout_seconds`` (7 s), whatever was said. "Que horas são?"
   takes one second, so the person then waited six more seconds of silence
   before NANO even started to think; a long request was cut off at seven.
   Now recording ends when the person stops talking (``Endpointer``).

2. **Telling speech from noise.** The energy gate (core.speech_filter) knows
   loud from quiet, not a voice from a fan, a keyboard or music. Silero VAD, a
   small neural voice detector that already ships inside faster-whisper (the
   same "Preparar a voz" pack, nothing extra to download), says "a person is
   speaking" frame by frame. Without the pack, the calibrated energy gate is
   used instead, so everything still works, just less well in noise.

Everything here is local. Nothing in this module records, keeps or sends audio
anywhere; it only decides where an utterance starts and ends.

``Endpointer`` and ``Hysteresis`` are pure (no audio library), so the timing
rules are unit-tested directly.
"""
from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass
from typing import Any, Callable

logger = logging.getLogger("nano.listening")

SAMPLE_RATE = 16000


# ---------------------------------------------------------------- endpointing

@dataclass(frozen=True)
class EndpointConfig:
    """When an utterance counts as started and as finished.

    start_timeout  how long to wait for the person to start talking
    end_silence    how long a pause ends the utterance. Long enough for a
                   breath in the middle of a sentence, short enough not to
                   make the person wait
    min_speech     speech shorter than this is a click or a cough, not a start
    max_seconds    an utterance is never recorded for longer than this
    preroll        audio kept from just before the start, so the first
                   syllable is not cut off
    """

    start_timeout: float = 5.0
    end_silence: float = 0.8
    min_speech: float = 0.2
    max_seconds: float = 15.0
    preroll: float = 0.3

    def bounded(self) -> "EndpointConfig":
        return EndpointConfig(
            start_timeout=max(1.0, min(15.0, float(self.start_timeout))),
            end_silence=max(0.3, min(2.5, float(self.end_silence))),
            min_speech=max(0.05, min(1.0, float(self.min_speech))),
            max_seconds=max(2.0, min(30.0, float(self.max_seconds))),
            preroll=max(0.0, min(1.0, float(self.preroll))),
        )


class Endpointer:
    """Feed it one frame decision at a time; it says when to stop.

    ``feed(speech, seconds)`` returns None while the utterance goes on, or the
    reason it ended:

        "no_speech"   nobody started talking within start_timeout
        "end"         the person spoke and then paused for end_silence
        "max"         max_seconds of speech reached
    """

    def __init__(self, config: EndpointConfig | None = None):
        self.config = (config or EndpointConfig()).bounded()
        self.reset()

    def reset(self) -> None:
        self.elapsed = 0.0
        self.started = False
        self.started_at = 0.0
        self.speech_seconds = 0.0
        self._speech_run = 0.0
        self._silence_run = 0.0
        self.reason: str | None = None

    def feed(self, speech: bool, seconds: float) -> str | None:
        if self.reason:
            return self.reason
        cfg = self.config
        seconds = max(0.0, float(seconds))
        self.elapsed += seconds
        if not self.started:
            if speech:
                self._speech_run += seconds
                if self._speech_run >= cfg.min_speech:
                    self.started = True
                    self.started_at = max(0.0, self.elapsed - self._speech_run)
                    self.speech_seconds = self._speech_run
            else:
                self._speech_run = 0.0
            if not self.started and self.elapsed >= cfg.start_timeout:
                self.reason = "no_speech"
            return self.reason
        if speech:
            self._silence_run = 0.0
            self.speech_seconds += seconds
        else:
            self._silence_run += seconds
            if self._silence_run >= cfg.end_silence:
                self.reason = "end"
                return self.reason
        if self.elapsed - self.started_at >= cfg.max_seconds:
            self.reason = "max"
        return self.reason


class Hysteresis:
    """Turn a speech probability into a steady yes/no.

    Above ``on`` is speech, below ``off`` is silence, and in between the last
    answer stands, so a word that dips for one frame does not end the
    utterance.
    """

    def __init__(self, on: float = 0.5, off: float = 0.35):
        self.on = float(on)
        self.off = float(min(on, off))
        self.state = False

    def __call__(self, probability: float) -> bool:
        if probability >= self.on:
            self.state = True
        elif probability < self.off:
            self.state = False
        return self.state

    def reset(self) -> None:
        self.state = False


# ---------------------------------------------------------------- detectors

def pcm_rms(pcm: bytes) -> float:
    """Root-mean-square of 16-bit little-endian mono samples."""
    count = len(pcm) // 2
    if count <= 0:
        return 0.0
    samples = struct.unpack(f"<{count}h", pcm[:count * 2])
    return math.sqrt(sum(s * s for s in samples) / count)


class EnergyDetector:
    """Speech = louder than the microphone's calibrated speech threshold.

    The fallback when Silero is not installed. It reads the threshold from the
    microphone's shared AdaptiveGate on every frame, and teaches the gate from
    the quiet frames, so it follows the room like every other trigger does.
    """

    name = "energy"

    def __init__(self, gate: Any):
        self.gate = gate

    def reset(self) -> None:
        pass

    def is_speech(self, pcm: bytes) -> bool:
        rms = pcm_rms(pcm)
        threshold = float(getattr(self.gate, "threshold", 220.0))
        speaking = rms >= threshold
        if not speaking:
            observe = getattr(self.gate, "observe", None)
            if callable(observe):
                observe(rms)
        return speaking


class SileroDetector:
    """Silero VAD run frame by frame, keeping its memory between frames.

    faster-whisper only runs the model over a whole recording. Here the same
    ONNX session is fed 512-sample windows as they arrive (each with the 64
    samples before it, the way the model was trained), carrying the network's
    state from one window to the next, so it works on a live stream.
    """

    name = "silero"
    WINDOW = 512
    CONTEXT = 64

    def __init__(self, session: Any, *, on: float = 0.5, off: float = 0.35):
        import numpy as np  # present whenever faster-whisper is

        self._np = np
        self._session = session
        self._decide = Hysteresis(on, off)
        self.last_probability = 0.0
        self.reset()

    def reset(self) -> None:
        np = self._np
        self._h = np.zeros((1, 1, 128), dtype="float32")
        self._c = np.zeros((1, 1, 128), dtype="float32")
        self._context = np.zeros(self.CONTEXT, dtype="float32")
        self._pending = np.zeros(0, dtype="float32")
        self._decide.reset()

    def probabilities(self, pcm: bytes) -> list[float]:
        np = self._np
        fresh = np.frombuffer(pcm[: len(pcm) // 2 * 2], dtype="<i2").astype("float32") / 32768.0
        audio = np.concatenate([self._pending, fresh])
        windows = len(audio) // self.WINDOW
        self._pending = audio[windows * self.WINDOW:]
        if windows == 0:
            return []
        rows = []
        context = self._context
        for index in range(windows):
            chunk = audio[index * self.WINDOW:(index + 1) * self.WINDOW]
            rows.append(np.concatenate([context, chunk]))
            context = chunk[-self.CONTEXT:]
        self._context = context
        batch = np.stack(rows).astype("float32")
        output, self._h, self._c = self._session.run(None, {"input": batch, "h": self._h, "c": self._c})
        probs = [float(p) for p in np.asarray(output).reshape(-1)]
        if probs:
            self.last_probability = probs[-1]
        return probs

    def is_speech(self, pcm: bytes) -> bool:
        probs = self.probabilities(pcm)
        if not probs:
            return self._decide.state
        # Any window in the block that clearly holds speech counts.
        return self._decide(max(probs))


_SILERO_SESSION: Any = None
_SILERO_FAILED = False


def load_silero() -> Any:
    """The Silero ONNX session from faster-whisper, built once, or None."""
    global _SILERO_SESSION, _SILERO_FAILED
    if _SILERO_SESSION is not None:
        return _SILERO_SESSION
    if _SILERO_FAILED:
        return None
    try:
        from faster_whisper.vad import get_vad_model

        _SILERO_SESSION = get_vad_model().session
        logger.info("Voice detector: Silero VAD (local)")
    except Exception as exc:  # the voice pack is optional
        _SILERO_FAILED = True
        logger.info("Voice detector: energy gate (Silero unavailable: %s)", exc.__class__.__name__)
        return None
    return _SILERO_SESSION


def make_detector(gate: Any, *, prefer_silero: bool = True) -> Any:
    """The best speech detector available on this PC."""
    if prefer_silero:
        session = load_silero()
        if session is not None:
            try:
                return SileroDetector(session)
            except Exception:
                logger.debug("Silero detector could not start", exc_info=True)
    return EnergyDetector(gate)


# ---------------------------------------------------------------- recording

@dataclass
class Utterance:
    """What was heard, and why the recording ended."""

    pcm: bytes
    reason: str
    speech_seconds: float
    seconds: float
    detector: str

    @property
    def heard_speech(self) -> bool:
        return self.reason in {"end", "max"}


def record_utterance(read_block: Callable[[], bytes | None], detector: Any,
                     config: EndpointConfig | None = None, *,
                     sample_rate: int = SAMPLE_RATE,
                     should_stop: Callable[[], bool] | None = None,
                     endpointer: Endpointer | None = None) -> Utterance | None:
    """Read blocks until the endpointer says the utterance is over.

    ``read_block()`` returns raw 16-bit mono PCM (one block) or None when the
    microphone gave up. Returns None only when no audio arrived at all.
    The PCM returned starts ``preroll`` seconds before the speech began.
    """
    ender = endpointer or Endpointer(config)
    preroll_bytes = int(ender.config.preroll * sample_rate) * 2
    before: list[bytes] = []
    before_size = 0
    kept: list[bytes] = []
    got_audio = False
    reason: str | None = None
    while reason is None:
        if should_stop is not None and should_stop():
            reason = "stopped"
            break
        block = read_block()
        if not block:
            break
        got_audio = True
        seconds = (len(block) / 2) / sample_rate
        speech = bool(detector.is_speech(block))
        was_started = ender.started
        reason = ender.feed(speech, seconds)
        if ender.started:
            if not was_started:
                kept.extend(before)
                before = []
            kept.append(block)
        else:
            before.append(block)
            before_size += len(block)
            # Keep the speech run that may be about to count as a start, plus
            # the preroll before it.
            limit = preroll_bytes + int(ender.config.min_speech * sample_rate) * 2 + len(block)
            while before and before_size - len(before[0]) >= limit:
                before_size -= len(before.pop(0))
    if not got_audio:
        return None
    pcm = b"".join(kept)
    return Utterance(
        pcm=pcm,
        reason=reason or ("end" if ender.started else "no_audio"),
        speech_seconds=round(ender.speech_seconds, 2),
        seconds=round(ender.elapsed, 2),
        detector=getattr(detector, "name", "unknown"),
    )


def head(pcm: bytes, seconds: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    """The first ``seconds`` of a PCM payload."""
    return pcm[: int(seconds * sample_rate) * 2]


__all__ = [
    "EndpointConfig", "Endpointer", "EnergyDetector", "Hysteresis", "SileroDetector",
    "Utterance", "head", "load_silero", "make_detector", "pcm_rms", "record_utterance",
]
