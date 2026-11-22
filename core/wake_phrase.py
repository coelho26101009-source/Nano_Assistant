"""Local, offline "Ei Nano" wake-phrase detection built on the existing STT stack.

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

import io
import logging
import re
import threading
import time
import wave
from enum import Enum
from typing import Any, Callable

from core import listening, speech_filter

logger = logging.getLogger("nano.wake_phrase")

# Portuguese on purpose. The detector transcribes with faster-whisper-tiny
# forced to Portuguese, and that model renders the English "Hey" unreliably:
# real user recordings of "Hey Nano" came back as "Ei, nano!", "Ei, nanos!",
# "Ei, não.", "E ai, no.", "ai na no" and "NÃO!" -- zero wake matches. "Ei" is
# a native Portuguese interjection, so the transcriber has a word to land on.
DEFAULT_WAKE_PHRASE = "ei nano"
DEFAULT_COOLDOWN_SECONDS = 3.0
DEFAULT_CHUNK_SECONDS = 2.5
MIN_CHUNK_SECONDS = 1.0
MAX_CHUNK_SECONDS = 6.0
MIN_COOLDOWN_SECONDS = 0.5

# THE UTTERANCE LISTENER (the default when the microphone can stream).
#
# The old loop transcribed a 2.5 s window every 1.5 s whenever the room was
# loud enough, with the full speech model: heavy, and still easy to cut a
# phrase in half. Now a voice detector (core/listening.py) waits for someone
# to say something, the utterance is cut where they pause, and only its first
# seconds are transcribed with a quick pass. Nothing is transcribed while
# nobody talks, so the PC does almost no work at rest.
#: The start of an utterance that is checked for the phrase.
SPOT_HEAD_SECONDS = 2.6
#: A pause this long ends an utterance (shorter than a command's pause: the
#: phrase is short, and a quick answer matters here).
WAKE_END_SILENCE = 0.6
#: An utterance longer than this is a conversation or the TV, not a call.
WAKE_MAX_UTTERANCE = 12.0
#: Shorter than this is a cough, a knock or a word, not "Ei Nano".
MIN_UTTERANCE_SPEECH = 0.35
#: How the transcriber hears the greeting of the phrase. "Hey Nano" comes back
#: from the Portuguese model as "Ei, nano"; "Ei Nano" from the English one as
#: "Hey Nano" or "Hi Nano". Only the GREETING varies: the name stays "nano"
#: exactly, so "Ei, não" or "Nanos" never count.
GREETING_VARIANTS = frozenset({"ei", "hey", "hei", "ey", "hi", "ola", "olá", "oi", "ok", "okay"})
#: At most this many words may come before the phrase ("Olha, ei Nano...").
#: Further in, it is somebody talking ABOUT Nano, not calling it.
MAX_WORDS_BEFORE_PHRASE = 1

_PUNCTUATION_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")
_NANO_ONLY_PATTERN = re.compile(r"\bnano\b")


def _join_wav(first: bytes, second: bytes) -> bytes:
    """Concatenate two 16-bit mono WAV payloads into one.

    Used to overlap analysis windows: the previous slice is prepended to the
    new one so a phrase spoken across a boundary is still transcribed whole.
    Falls back to the newer slice if either payload is unreadable, because a
    missed window is recoverable but a crash in the wake loop is not.
    """
    try:
        with wave.open(io.BytesIO(first)) as handle:
            params = handle.getparams()
            frames_a = handle.readframes(handle.getnframes())
        with wave.open(io.BytesIO(second)) as handle:
            frames_b = handle.readframes(handle.getnframes())
    except Exception:
        return second

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(frames_a + frames_b)
    return buffer.getvalue()


def normalize_transcript(text: str | None) -> str:
    """Lowercase, trim and strip simple punctuation from a raw STT transcript.

    "Ei, Nano!" and "ei   nano" and "  Ei Nano." all normalize to "ei nano".
    """
    if not text:
        return ""
    lowered = str(text).strip().lower()
    stripped = _PUNCTUATION_PATTERN.sub(" ", lowered)
    return _WHITESPACE_PATTERN.sub(" ", stripped).strip()


def _calling_pattern(phrase: str) -> re.Pattern[str]:
    """The phrase at the START of what was said, with the request after it.

    Matched on the raw transcript (punctuation and case included) so the
    request keeps its own words: "Ei, Nano! Abre o Spotify." gives
    "Abre o Spotify.".
    """
    words = normalize_transcript(phrase).split() or DEFAULT_WAKE_PHRASE.split()
    parts = []
    for index, word in enumerate(words):
        if index == 0 and word in GREETING_VARIANTS and len(words) > 1:
            parts.append("(?:" + "|".join(re.escape(w) for w in sorted(GREETING_VARIANTS)) + ")")
        else:
            parts.append(re.escape(word))
    body = r"[\W_]+".join(parts)
    before = r"(?:\w+[\W_]+){0,%d}" % MAX_WORDS_BEFORE_PHRASE
    return re.compile(rf"^[\W_]*{before}?{body}(?![\w])[\W_]*(?P<command>.*)$",
                      re.IGNORECASE | re.UNICODE | re.DOTALL)


def _phrase_pattern(phrase: str) -> re.Pattern[str]:
    """Build a word-boundary regex for the configured phrase.

    Word boundaries are what stop "nano" from matching inside "nanotechnology"
    or "nanosecond" — there is no boundary between "nano" and the letters that
    follow it in either word, so \\bnano\\b cannot match there. The same rule
    is what makes the transcriber's "Ei, nanos!" a non-match: the trailing "s"
    is a word character, so \\bnano\\b cannot end there either.

    Matching is exact-after-normalisation on purpose. Broad fuzzy matching
    would happily accept "Ei, não." and "e aí", which are exactly the things
    Whisper produces when nobody is calling Nano at all.
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
    # The thread is alive and chunks are arriving, but they carry no energy.
    # Reporting LISTENING here is a lie: the wake phrase can never fire.
    MIC_SILENT = "MIC_SILENT"
    ERROR = "ERROR"


# How much each analysis window overlaps the previous one. Without overlap a
# phrase spoken across a window boundary is split -- "hey" in one window,
# "nano" in the next -- and neither matches. One second of overlap is longer
# than the phrase itself, so every utterance lands whole in some window.
OVERLAP_SECONDS = 1.0

# Ambient sampling at startup, bounded so a broken microphone cannot stall the
# engine: a handful of short reads is enough to estimate a noise floor.
CALIBRATION_WINDOWS = 6
CALIBRATION_WINDOW_SECONDS = 0.25


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
        self._calling = _calling_pattern(self.phrase)
        self._last_trigger: float | None = None

    def set_phrase(self, phrase: str) -> None:
        self.phrase = normalize_transcript(phrase) or DEFAULT_WAKE_PHRASE
        self._primary_pattern = _phrase_pattern(self.phrase)
        self._calling = _calling_pattern(self.phrase)

    def split_command(self, text: str) -> tuple[bool, str]:
        """Is this utterance calling NANO? And what was asked after the phrase?

        Stricter than matches(): the phrase has to OPEN the utterance (at most
        one word before it), because that is how people call someone, and
        "o nano é um assistente" in a conversation is not a call. A bare
        "Nano" counts only when allow_nano_only is on.
        """
        raw = str(text or "").strip()
        if not raw:
            return False, ""
        found = self._calling.match(raw)
        if found is None and self.allow_nano_only:
            found = re.match(r"^[\W_]*nano(?![\w])[\W_]*(?P<command>.*)$", raw,
                             re.IGNORECASE | re.UNICODE | re.DOTALL)
        if found is None:
            return False, ""
        command = found.group("command").strip()
        return True, command

    def allow(self, *, now: float | None = None) -> bool:
        """The cooldown alone: True (and start a new cooldown) if a wake may fire."""
        current = time.monotonic() if now is None else float(now)
        if self._last_trigger is not None and (current - self._last_trigger) < self.cooldown_seconds:
            return False
        self._last_trigger = current
        return True

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
        # Listen by utterance (voice detector) when the microphone can stream
        # block by block; the old fixed windows otherwise.
        self.segmented = bool(cfg.get("wake_phrase_segmented", True))
        self.detector_name: str | None = None
        # A request heard in the same breath as the phrase, for the caller.
        self._pending_command = ""

        self.state = WakePhraseState.IDLE
        self.last_error: str | None = None
        self.last_transcript: str | None = None
        # Counters so the UI/diagnostic can distinguish "mic never delivered a
        # chunk" from "chunks arrive but STT finds no speech" from "speech is
        # transcribed but never matches the phrase".
        self.chunks_captured = 0
        self.transcripts_seen = 0
        self.silent_chunks = 0
        self.speech_chunks = 0
        self.wake_matches = 0
        # The last few things the transcriber actually heard, matched or not.
        # Without this a non-matching wake is invisible: the user says
        # "Hey Nano", nothing happens, and there is no way to see whether the
        # phrase was misheard or never reached the transcriber at all.
        self.recent_transcripts: list[str] = []

        # The energy gate is the MICROPHONE's, not this engine's. Owning it
        # here meant the calibration only existed while the wake detector ran,
        # so switching the wake phrase off left the hotkey and UI turns with a
        # fixed 220 RMS floor that a normal speaking voice never reached. The
        # provider owns it now and every trigger shares one number; this stays
        # as an alias so the detector, its diagnostics and its status payload
        # are unchanged.

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        # Held for the duration of every microphone read this engine performs.
        # pause() only sets a flag that the loop checks at the TOP of an
        # iteration, so a caller that paused and then immediately captured was
        # racing a read already in flight -- two threads calling read() on one
        # PortAudio stream. pause_and_wait() below waits on this lock instead.
        self._read_lock = threading.Lock()

    # ------------------------------------------------------------- lifecycle

    def _stt_available(self) -> bool:
        return bool(self._stt) and bool(getattr(self._stt, "online", False))

    def _audio_available(self) -> bool:
        return bool(self._audio) and bool(getattr(self._audio, "_available", False))

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    @property
    def gate(self) -> "speech_filter.AdaptiveGate":
        """The microphone's shared speech gate.

        A property, not an attribute, so this engine and every other trigger
        are guaranteed to be reading and calibrating the SAME object. See
        AudioInputProvider.gate for why ownership moved.
        """
        return self._audio.gate

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

    def pause_and_wait(self, timeout: float = 5.0) -> bool:
        """Pause AND block until any in-flight microphone read has finished.

        This is the only safe way for another thread to take the microphone.
        pause() alone returns immediately while the loop may still be inside a
        multi-second read: the caller then opens its own read on the same
        stream, and two concurrent readers on one PortAudio stream is
        undefined behaviour in the library that already crashed this process
        once with an access violation.

        Returns False if the read did not finish within ``timeout``, so the
        caller can refuse to take the microphone rather than take it anyway.
        """
        self.pause()
        if not self.running:
            return True
        if self._read_lock.acquire(timeout=timeout):
            self._read_lock.release()
            return True
        logger.warning("[WakePhrase] in-flight capture did not finish within %.1fs", timeout)
        return False

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
        # Honesty rule: a live thread is not the same as a working microphone.
        # If chunks are arriving with no energy at all, say so instead of
        # cheerfully reporting "A ouvir" while nothing can ever be heard.
        if self.gate.looks_dead():
            return WakePhraseReadiness.MIC_SILENT
        if self.running and not self._paused.is_set():
            return WakePhraseReadiness.LISTENING
        return WakePhraseReadiness.READY

    def explain(self) -> str:
        """A sentence the UI can show the user, in Portuguese."""
        readiness = self.readiness()
        if readiness == WakePhraseReadiness.MIC_SILENT:
            return ("O Nano não está a receber áudio suficiente do microfone. "
                    "Verifica o dispositivo de entrada e o nível de captura no Windows.")
        if readiness == WakePhraseReadiness.MIC_UNAVAILABLE:
            return "Nenhum microfone disponível para o Nano."
        if readiness == WakePhraseReadiness.STT_UNAVAILABLE:
            return "O motor local de transcrição não está disponível."
        if readiness == WakePhraseReadiness.DISABLED:
            return f"A deteção de \"{self.phrase}\" está desligada."
        if readiness == WakePhraseReadiness.ERROR:
            return str(self.last_error or "Erro na deteção de wake.")
        if readiness == WakePhraseReadiness.LISTENING:
            return f"\"{self.phrase}\" — A ouvir"
        return "Pronto."

    def set_phrase(self, phrase: str) -> None:
        """Switch the wake phrase while running (the interface language changed)."""
        self.phrase = normalize_transcript(phrase) or DEFAULT_WAKE_PHRASE
        self.detector.set_phrase(self.phrase)

    def take_command(self) -> str:
        """The request heard with the last wake, once (then it is cleared)."""
        command, self._pending_command = self._pending_command, ""
        return command

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
            "explain": self.explain(),
            "error": self.last_error,
            "last_transcript": self.last_transcript,
            "recent_transcripts": list(self.recent_transcripts),
            # Counters make a silent failure diagnosable at a glance.
            "chunks_captured": self.chunks_captured,
            "transcripts_seen": self.transcripts_seen,
            "silent_chunks": self.silent_chunks,
            "speech_chunks": self.speech_chunks,
            "wake_matches": self.wake_matches,
            "mode": "utterance" if self._segmenting() else "windows",
            "detector": self.detector_name,
            # Live microphone characteristics for Voice diagnostics.
            "audio": self.gate.stats(),
        }

    # ------------------------------------------------------------------ loop

    def _calibrate(self) -> None:
        """Sample ambient noise briefly so the gate matches this microphone."""
        samples: list[float] = []
        for _ in range(CALIBRATION_WINDOWS):
            if self._stop.is_set():
                break
            try:
                with self._read_lock:
                    chunk = self._capture(CALIBRATION_WINDOW_SECONDS)
            except Exception as exc:
                logger.warning("[WakePhrase] calibration capture failed: %s", exc)
                break
            if chunk:
                samples.append(speech_filter.rms_of_wav(chunk))
        if samples:
            threshold = self.gate.calibrate(samples)
            logger.info(
                "[WakePhrase] calibrated | noise_floor=%.1f threshold=%.1f (from %d samples)",
                self.gate.noise_floor, threshold, len(samples),
            )
        else:
            logger.warning("[WakePhrase] could not calibrate; using default threshold %.1f",
                           self.gate.threshold)

    def _capture(self, seconds: float) -> bytes | None:
        """Read from the persistent stream when there is one."""
        reader = getattr(self._audio, "read_stream", None)
        if reader is not None and getattr(self._audio, "stream_open", False):
            return reader(seconds)
        return self._audio.capture(seconds)

    # ------------------------------------------------------- utterance loop

    def _segmenting(self) -> bool:
        return self.segmented and callable(getattr(self._audio, "read_block", None)) \
            and bool(getattr(self._audio, "stream_open", False))

    def _wav(self, pcm: bytes, rate: int) -> bytes:
        to_wav = getattr(self._audio, "_to_wav", None)
        if callable(to_wav):
            return to_wav(pcm, rate)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(rate)
            out.writeframes(pcm)
        return buffer.getvalue()

    def _transcribe(self, audio: bytes, *, quick: bool) -> str:
        method = getattr(self._stt, "transcribe_quick", None) if quick else None
        try:
            result = method(audio) if callable(method) else self._stt.transcribe(audio)
        except Exception as exc:
            self.last_error = str(exc)
            logger.warning("[WakePhrase] transcription failed: %s", exc)
            return ""
        if result is None or not getattr(result, "ok", False):
            return ""
        text = str(getattr(result, "text", "") or "").strip()
        if not text or speech_filter.is_hallucination(text):
            return ""
        return text

    def _loop_utterances(self) -> None:
        """Wait for someone to speak; check the start of what they said."""
        rate = int(getattr(self._audio, "_stream_rate", None) or listening.SAMPLE_RATE)
        detector = listening.make_detector(self.gate, prefer_silero=rate == listening.SAMPLE_RATE)
        self.detector_name = getattr(detector, "name", None)
        config = listening.EndpointConfig(
            start_timeout=15.0, end_silence=WAKE_END_SILENCE, min_speech=0.25,
            max_seconds=WAKE_MAX_UTTERANCE, preroll=0.3,
        )
        logger.info("[WakePhrase] listening by utterance (detector=%s)", self.detector_name)
        # The microphone's gate keeps learning (and diagnostics keep their
        # live numbers) from about a second of audio at a time.
        meter: list[bytes] = []

        def interrupted() -> bool:
            return self._stop.is_set() or self._paused.is_set()

        def read() -> bytes | None:
            if interrupted():
                return None
            with self._read_lock:
                if interrupted():
                    return None
                block = self._audio.read_block()
            if block:
                meter.append(block)
                if len(meter) >= 16:
                    try:
                        self.gate.has_speech(self._wav(b"".join(meter), rate))
                    except Exception:
                        logger.debug("[WakePhrase] gate update failed", exc_info=True)
                    meter.clear()
            return block

        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                detector.reset()
                continue
            self.state = WakePhraseState.WAKE_LISTENING
            try:
                utterance = listening.record_utterance(read, detector, config, sample_rate=rate,
                                                       should_stop=interrupted)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("[WakePhrase] listening failed: %s", exc)
                time.sleep(1.0)
                continue
            if interrupted():
                continue
            if utterance is None:
                # The stream gave nothing back: give the device a moment.
                time.sleep(0.2)
                continue
            self.chunks_captured += 1
            if not utterance.heard_speech or utterance.speech_seconds < MIN_UTTERANCE_SPEECH:
                self.silent_chunks += 1
                continue
            self.speech_chunks += 1
            self._consider(utterance, rate)
            detector.reset()

    def _consider(self, utterance: "listening.Utterance", rate: int) -> bool:
        """Is this utterance calling NANO? If so, wake it, with the request."""
        head_pcm = listening.head(utterance.pcm, SPOT_HEAD_SECONDS, rate)
        text = self._transcribe(self._wav(head_pcm, rate), quick=True)
        if not text:
            return False
        self.transcripts_seen += 1
        self.last_transcript = text
        matched, command = self.detector.split_command(text)
        self.recent_transcripts.append(f"{text[:80]}{'' if matched else '  (sem correspondência)'}")
        del self.recent_transcripts[:-5]
        logger.debug("[WakePhrase] utterance head=%r matched=%s", text, matched)
        if not matched:
            return False
        if not self.detector.allow():
            logger.debug("[WakePhrase] match suppressed by %.1fs cooldown", self.cooldown_seconds)
            return False
        full_text = text
        if len(utterance.pcm) > len(head_pcm) + int(0.3 * rate) * 2:
            # The request went on past the quick check: hear all of it properly.
            full_text = self._transcribe(self._wav(utterance.pcm, rate), quick=False) or text
            again, full_command = self.detector.split_command(full_text)
            if again:
                command = full_command
        self.state = WakePhraseState.WAKE_DETECTED
        self.wake_matches += 1
        logger.info("[WakePhrase] DETECTED (request in the same breath: %s) -> waking Nano",
                    "yes" if command else "no")
        self._pending_command = command if speech_filter.is_usable_command(command) else ""
        self.pause()
        try:
            self.on_wake(full_text)
        except Exception:
            logger.exception("[WakePhrase] callback failed")
        finally:
            self._pending_command = ""
            self.state = WakePhraseState.IDLE
            if not self._stop.is_set():
                self.resume()
        return True

    # ---------------------------------------------------------- window loop

    def _loop_windows(self) -> None:
        """The original fixed-window loop, for microphones that cannot stream."""
        # The rolling tail carries the end of the previous window into the next
        # one, so every utterance appears whole in at least one analysed window.
        tail: bytes | None = None
        step = max(0.5, self.chunk_seconds - OVERLAP_SECONDS)

        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.2)
                continue

            self.state = WakePhraseState.WAKE_LISTENING
            try:
                # Held across the read so pause_and_wait() can tell the
                # difference between "paused" and "paused, and the microphone
                # is genuinely free now".
                with self._read_lock:
                    fresh = self._capture(step)
            except Exception as exc:
                self.last_error = str(exc)
                logger.warning("[WakePhrase] capture failed: %s", exc)
                time.sleep(1.0)
                continue

            if self._stop.is_set() or self._paused.is_set():
                continue
            if not fresh:
                logger.debug("[WakePhrase] captured empty chunk")
                time.sleep(0.05)
                continue

            audio = _join_wav(tail, fresh) if tail else fresh
            tail = fresh

            self.chunks_captured += 1
            logger.debug("[WakePhrase] window #%d (%d bytes)", self.chunks_captured, len(audio))

            # Silence never reaches the transcriber. Whisper does not return an
            # empty string for silence -- it invents filler, and a hallucinated
            # fragment containing "nano" was tripping the wake with nobody
            # speaking. Gating on energy removes that at the source.
            if not self.gate.has_speech(audio):
                self.silent_chunks += 1
                logger.debug("[WakePhrase] window #%d below threshold %.1f (rms=%.1f), skipped",
                             self.chunks_captured, self.gate.threshold, self.gate.last_rms)
                time.sleep(0.02)
                continue
            self.speech_chunks += 1

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

            if speech_filter.is_hallucination(text):
                logger.debug("[WakePhrase] discarded hallucinated transcript %r", text)
                time.sleep(0.02)
                continue

            self.transcripts_seen += 1
            self.last_transcript = text
            normalized = normalize_transcript(text)
            matched = self.detector.matches(text)
            # Kept whether or not it matched, so a mishearing is visible.
            self.recent_transcripts.append(f"{text}{'' if matched else '  (sem correspondência)'}")
            del self.recent_transcripts[:-5]
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
            self.wake_matches += 1
            logger.info("[WakePhrase] DETECTED %r -> waking Nano", normalized)
            self.pause()
            try:
                self.on_wake(text)
                logger.debug("[WakePhrase] callback fired")
            except Exception:
                logger.exception("[WakePhrase] callback failed")
            finally:
                self.state = WakePhraseState.IDLE
                # The turn consumed the microphone; the stale tail belongs to
                # the wake utterance and must not be re-analysed afterwards.
                tail = None
                if not self._stop.is_set():
                    self.resume()

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
        self.silent_chunks = 0
        self.speech_chunks = 0

        # One long-lived stream instead of reopening PyAudio every chunk. That
        # removed 78-125 ms of measured dead time per iteration and, with the
        # overlap below, stops a phrase being cut in half at a chunk boundary.
        opener = getattr(self._audio, "open_stream", None)
        if opener is not None:
            try:
                opener()
            except Exception as exc:
                logger.warning("[WakePhrase] persistent stream unavailable (%s); "
                               "falling back to per-chunk capture.", exc)

        self._calibrate()

        if self._segmenting():
            self._loop_utterances()
        else:
            self._loop_windows()

        closer = getattr(self._audio, "close_stream", None)
        if closer is not None:
            try:
                closer()
            except Exception:
                logger.debug("[WakePhrase] could not close the capture stream", exc_info=True)

        logger.info("[WakePhrase] engine stopped (chunks=%d transcripts=%d speech=%d wakes=%d)",
                    getattr(self, "chunks_captured", 0), getattr(self, "transcripts_seen", 0),
                    getattr(self, "speech_chunks", 0), getattr(self, "wake_matches", 0))


__all__ = [
    "CALIBRATION_WINDOWS",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_WAKE_PHRASE",
    "OVERLAP_SECONDS",
    "WakePhraseDetector",
    "WakePhraseEngine",
    "WakePhraseReadiness",
    "WakePhraseState",
    "normalize_transcript",
]
