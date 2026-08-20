"""Local, offline "Hey Nano" wake-phrase detection built on the existing STT stack.

Unlike ``core.wake_word.WakeWordEngine`` (which needs a trained ONNX/Porcupine
keyword model before it will ever start), this module spots a plain phrase
inside ordinary local speech transcripts: it captures short audio chunks,
transcribes them with the project's existing local STT provider, and checks the
normalized text for the configured wake phrase. It requires no training, no
cloud dependency, and no ``.onnx`` model.

This module is intentionally standalone — it does not import anything from
``core.voice`` — so ``core.voice`` can import it without creating a cycle. The
audio and STT providers are injected as plain duck-typed objects, which also
keeps the detection logic (the part with real behaviour to test) free of any
hardware or model dependency.

Safety: the engine only ever calls ``on_wake(transcript)``. It never resolves a
capability, never touches the policy engine, and never executes a tool. Waking
Nano is the only thing this module is allowed to do.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("nano.wake_phrase")

DEFAULT_WAKE_PHRASE = "hey nano"
DEFAULT_COOLDOWN_SECONDS = 3.0
DEFAULT_CHUNK_SECONDS = 2.5
MIN_CHUNK_SECONDS = 1.0
MAX_CHUNK_SECONDS = 6.0
MIN_COOLDOWN_SECONDS = 0.5

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NANO_ONLY_PATTERN = re.compile(r"\bnano\b")


def normalize_transcript(text: str | None) -> str:
    """Lowercase, trim and strip simple punctuation from a raw STT transcript.

    "Hey, Nano!" and "hey   nano" and "  Hey Nano." all normalize to "hey nano".
    """
    if not text:
        return ""
    lowered = str(text).strip().lower()
    stripped = _PUNCTUATION_PATTERN.sub(" ", lowered)
    return _WHITESPACE_PATTERN.sub(" ", stripped).strip()


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Build a word-boundary regex for the configured phrase.

    Word boundaries are what stop "nano" from matching inside "nanotechnology"
    or "nanosecond" — there is no boundary between "nano" and the letters that
    follow it in either word, so \\bnano\\b cannot match there.
    """
    words = normalize_transcript(phrase).split()
    if not words:
        words = DEFAULT_WAKE_PHRASE.split()
    body = r"\s+".join(re.escape(word) for word in words)
    return re.compile(rf"\b{body}\b")


class WakePhraseReadiness(str, Enum):
    """Whether the detector can actually run right now. Never fake this."""

    READY = "READY"
    LISTENING = "LISTENING"
    DISABLED = "DISABLED"
    STT_UNAVAILABLE = "STT_UNAVAILABLE"
    MIC_UNAVAILABLE = "MIC_UNAVAILABLE"
    ERROR = "ERROR"


class WakePhraseState(str, Enum):
    """The simple per-turn state machine requested for this feature."""

    IDLE = "IDLE"
    WAKE_LISTENING = "WAKE_LISTENING"
    WAKE_DETECTED = "WAKE_DETECTED"
    COMMAND_LISTENING = "COMMAND_LISTENING"
    PROCESSING = "PROCESSING"


class WakePhraseDetector:
    """Pure phrase-matching and debounce logic. No audio, no threads, no I/O.

    Kept separate from the engine so the matching rules can be unit tested
    directly against transcript strings, without a microphone or a model.
    """

    def __init__(
        self,
        *,
        phrase: str = DEFAULT_WAKE_PHRASE,
        allow_nano_only: bool = True,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.phrase = normalize_transcript(phrase) or DEFAULT_WAKE_PHRASE
        self.allow_nano_only = bool(allow_nano_only)
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._primary_pattern = _phrase_pattern(self.phrase)
        self._last_trigger: float | None = None

    def matches(self, text: str) -> bool:
        """True if the normalized transcript contains a configured trigger."""
        normalized = normalize_transcript(text)
        if not normalized:
            return False
        if self._primary_pattern.search(normalized):
            return True
        if self.allow_nano_only and _NANO_ONLY_PATTERN.search(normalized):
            return True
        return False

    def check(self, text: str, *, now: float | None = None) -> bool:
        """Match plus debounce. Returns True only when the wake should fire.

        A match inside the cooldown window after a previous trigger is
        discarded so the same utterance (or its echo across two chunks) cannot
        fire the wake event repeatedly.
        """
        if not self.matches(text):
            return False
        current = time.monotonic() if now is None else float(now)
        if self._last_trigger is not None and (current - self._last_trigger) < self.cooldown_seconds:
            return False
        self._last_trigger = current
        return True

    def reset_cooldown(self) -> None:
        self._last_trigger = None


class WakePhraseEngine:
    """Runtime loop: capture a short chunk, transcribe it, check for the phrase.

    ``audio_provider`` needs a ``capture(duration_seconds) -> bytes | None``
    method and an ``_available`` attribute. ``stt_provider`` needs a
    ``transcribe(audio_bytes) -> VoiceResult``-shaped object (``.ok``, ``.text``)
    and an ``online`` attribute. ``core.voice.AudioInputProvider`` and
    ``core.voice.LocalSTTProvider`` already satisfy this — the caller (a
    ``VoiceEngine``) constructs and injects its own instances, so this module
    never has to import ``core.voice`` itself.

    On a confirmed match the engine pauses itself and calls
    ``on_wake(transcript)`` exactly once. It does not resolve a capability,
    check a policy, or run a tool — the caller decides what "waking Nano"
    means. This is the safety boundary requirement for this feature.
    """

    def __init__(
        self,
        config: dict | None,
        on_wake: Callable[[str], None],
        *,
        audio_provider: Any,
        stt_provider: Any,
    ):
        cfg = config or {}
        self.on_wake = on_wake
        self._audio = audio_provider
        self._stt = stt_provider

        self.enabled = bool(cfg.get("wake_phrase_enabled", False))
        self.phrase = str(cfg.get("wake_phrase") or DEFAULT_WAKE_PHRASE).strip() or DEFAULT_WAKE_PHRASE
        self.allow_nano_only = bool(cfg.get("wake_phrase_allow_nano_only", True))
        self.cooldown_seconds = max(
            MIN_COOLDOWN_SECONDS, float(cfg.get("wake_phrase_cooldown_seconds", DEFAULT_COOLDOWN_SECONDS))
        )
        self.chunk_seconds = max(
            MIN_CHUNK_SECONDS,
            min(MAX_CHUNK_SECONDS, float(cfg.get("wake_phrase_chunk_seconds", DEFAULT_CHUNK_SECONDS))),
        )

        self.detector = WakePhraseDetector(
            phrase=self.phrase,
            allow_nano_only=self.allow_nano_only,
            cooldown_seconds=self.cooldown_seconds,
        )

        self.state = WakePhraseState.IDLE
        self.last_error: str | None = None
        self.last_transcript: str | None = None
        # Counters so the UI/diagnostic can distinguish "mic never delivered a
        # chunk" from "chunks arrive but STT finds no speech" from "speech is
        # transcribed but never matches the phrase".
        self.chunks_captured = 0
        self.transcripts_seen = 0

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()

    # ------------------------------------------------------------- lifecycle

    def _stt_available(self) -> bool:
        return bool(self._stt) and bool(getattr(self._stt, "online", False))

    def _audio_available(self) -> bool:
        return bool(self._audio) and bool(getattr(self._audio, "_available", False))

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> bool:
        if not self.enabled:
            return False
        if self._thread and self._thread.is_alive():
            return True
        if not self._stt_available():
            self.last_error = "local speech-to-text runtime not available"
            return False
        if not self._audio_available():
            self.last_error = "microphone runtime not available"
            return False

        self.last_error = None
        self._stop.clear()
        self._paused.clear()
        self.state = WakePhraseState.WAKE_LISTENING
        self._thread = threading.Thread(target=self._run, name="nano-wake-phrase", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        self.state = WakePhraseState.IDLE

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    # -------------------------------------------------------- state machine

    def set_state(self, state: WakePhraseState) -> None:
        """Let the caller record COMMAND_LISTENING / PROCESSING around the
        existing voice pipeline, without this module reaching into it."""
        self.state = state

    def mark_command_listening(self) -> None:
        self.set_state(WakePhraseState.COMMAND_LISTENING)

    def mark_processing(self) -> None:
        self.set_state(WakePhraseState.PROCESSING)

    def mark_idle(self) -> None:
        self.set_state(WakePhraseState.IDLE)

    # ------------------------------------------------------------- readiness

    def readiness(self) -> WakePhraseReadiness:
        """Never report READY for something that was not actually checked."""
        if not self.enabled:
            return WakePhraseReadiness.DISABLED
        if not self._stt_available():
            return WakePhraseReadiness.STT_UNAVAILABLE
        if not self._audio_available():
            return WakePhraseReadiness.MIC_UNAVAILABLE
        if self.last_error:
            return WakePhraseReadiness.ERROR
        if self.running and not self._paused.is_set():
            return WakePhraseReadiness.LISTENING
        return WakePhraseReadiness.READY

    def status(self) -> dict:
        return {
            "enabled": self.enabled,
            "phrase": self.phrase,
            "allow_nano_only": self.allow_nano_only,
            "cooldown_seconds": self.cooldown_seconds,
            "chunk_seconds": self.chunk_seconds,
            "running": self.running,
            "paused": self._paused.is_set(),
            "state": self.state.value,
            "readiness": self.readiness().value,
            "error": self.last_error,
            "last_transcript": self.last_transcript,
            # Counters make a silent failure diagnosable at a glance.
            "chunks_captured": self.chunks_captured,
            "transcripts_seen": self.transcripts_seen,
        }

    # ------------------------------------------------------------------ loop

    def _run(self) -> None:
        # Every stage below logs at DEBUG so a silent failure can be traced with
        # `python -m core.wake_phrase_debug` or by raising the nano.wake_phrase
        # log level. Transcripts are logged (they are the wake phrase, not user
        # dictation); raw audio never is.
        logger.info(
            "[WakePhrase] engine started | phrase=%r allow_nano_only=%s chunk=%.1fs cooldown=%.1fs",
            self.phrase, self.allow_nano_only, self.chunk_seconds, self.cooldown_seconds,
        )
        self.chunks_captured = 0
        self.transcripts_seen = 0

        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue

            self.state = WakePhraseState.WAKE_LISTENING
            try:
                # Bounded by chunk_seconds: no unbounded audio ever accumulates.
                audio = self._audio.capture(self.chunk_seconds)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("[WakePhrase] capture failed: %s", exc)
                time.sleep(1.0)
                continue

            if self._stop.is_set() or self._paused.is_set():
                continue
            if not audio:
                logger.debug("[WakePhrase] captured empty chunk")
                time.sleep(0.05)
                continue

            self.chunks_captured += 1
            logger.debug("[WakePhrase] captured chunk #%d (%d bytes)", self.chunks_captured, len(audio))

            try:
                result = self._stt.transcribe(audio)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("[WakePhrase] transcription failed: %s", exc)
                continue

            text = getattr(result, "text", "") if result is not None else ""
            ok = bool(getattr(result, "ok", False)) if result is not None else False
            if not ok or not text:
                logger.debug(
                    "[WakePhrase] no speech in chunk #%d (%s)",
                    self.chunks_captured, getattr(result, "error", "no result"),
                )
                # Small pacing floor: negligible next to a real multi-second
                # capture() call, but stops a misbehaving/instant audio source
                # from spinning this thread at full CPU.
                time.sleep(0.02)
                continue

            self.transcripts_seen += 1
            self.last_transcript = text
            normalized = normalize_transcript(text)
            matched = self.detector.matches(text)
            logger.debug(
                "[WakePhrase] STT transcript=%r | normalized=%r | matched=%s",
                text, normalized, matched,
            )

            if not self.detector.check(text):
                if matched:
                    # Matched but suppressed: the cooldown is doing its job.
                    logger.debug("[WakePhrase] match suppressed by %.1fs cooldown", self.cooldown_seconds)
                time.sleep(0.02)
                continue

            self.state = WakePhraseState.WAKE_DETECTED
            logger.info("[WakePhrase] DETECTED %r -> waking Nano", normalized)
            self.pause()
            try:
                self.on_wake(text)
                logger.debug("[WakePhrase] callback fired")
            except Exception:
                logger.exception("[WakePhrase] callback failed")
            finally:
                self.state = WakePhraseState.IDLE
                if not self._stop.is_set():
                    self.resume()

        logger.info("[WakePhrase] engine stopped (chunks=%d transcripts=%d)",
                    getattr(self, "chunks_captured", 0), getattr(self, "transcripts_seen", 0))


__all__ = [
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_WAKE_PHRASE",
    "WakePhraseDetector",
    "WakePhraseEngine",
    "WakePhraseReadiness",
    "WakePhraseState",
    "normalize_transcript",
]
