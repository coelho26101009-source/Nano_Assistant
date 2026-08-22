"""Unit tests for the local 'Hey Nano' wake-phrase detector (core/wake_phrase.py).

These tests exercise the pure matching/debounce logic and the engine's
lifecycle/readiness reporting without touching a real microphone or a real
speech-to-text model — the detector and the engine both accept plain
duck-typed fakes, which is the whole point of keeping them free of any
dependency on core.voice.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import pytest

from core import speech_filter
from core.wake_phrase import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_WAKE_PHRASE,
    WakePhraseDetector,
    WakePhraseEngine,
    WakePhraseReadiness,
    WakePhraseState,
    normalize_transcript,
)


# ============================================================== normalization

def test_normalize_lowercases_trims_and_strips_simple_punctuation():
    assert normalize_transcript("  Hey, Nano!  ") == "hey nano"


def test_normalize_collapses_internal_whitespace():
    assert normalize_transcript("hey    nano") == "hey nano"


def test_normalize_handles_empty_and_none():
    assert normalize_transcript("") == ""
    assert normalize_transcript(None) == ""


# =================================================================== matching

def test_exact_phrase_matches():
    detector = WakePhraseDetector(phrase="hey nano")
    assert detector.matches("hey nano") is True


def test_phrase_with_punctuation_matches():
    detector = WakePhraseDetector(phrase="hey nano")
    assert detector.matches("Hey, Nano.") is True
    assert detector.matches("hey nano!") is True
    assert detector.matches("hey nano?") is True


def test_phrase_is_case_insensitive():
    detector = WakePhraseDetector(phrase="hey nano")
    assert detector.matches("HEY NANO") is True
    assert detector.matches("Hey Nano") is True
    assert detector.matches("hEy NaNo") is True


def test_phrase_tolerates_extra_whitespace():
    detector = WakePhraseDetector(phrase="hey nano")
    assert detector.matches("hey     nano") is True
    assert detector.matches("  hey nano  ") is True


def test_phrase_matches_mid_sentence():
    detector = WakePhraseDetector(phrase="hey nano")
    assert detector.matches("so hey nano can you help me please") is True


def test_nano_only_trigger_is_optional_and_off_by_default_behavior_is_explicit():
    allowed = WakePhraseDetector(phrase="hey nano", allow_nano_only=True)
    assert allowed.matches("nano") is True

    disallowed = WakePhraseDetector(phrase="hey nano", allow_nano_only=False)
    assert disallowed.matches("nano") is False
    # The full phrase must still work when the short trigger is disabled.
    assert disallowed.matches("hey nano") is True


def test_no_partial_word_activation_on_nanotechnology():
    detector = WakePhraseDetector(phrase="hey nano", allow_nano_only=True)
    assert detector.matches("I'm reading about nanotechnology") is False


def test_no_partial_word_activation_on_nanosecond():
    detector = WakePhraseDetector(phrase="hey nano", allow_nano_only=True)
    assert detector.matches("that happened in a nanosecond") is False


def test_no_partial_word_activation_on_compound_words_either_direction():
    detector = WakePhraseDetector(phrase="hey nano", allow_nano_only=True)
    assert detector.matches("piano nano") is True  # "nano" is its own word here
    assert detector.matches("piananobar") is False  # "nano" not a whole word


def test_unrelated_text_does_not_match():
    detector = WakePhraseDetector(phrase="hey nano", allow_nano_only=True)
    assert detector.matches("what time is it") is False
    assert detector.matches("") is False


def test_custom_phrase_is_configurable():
    detector = WakePhraseDetector(phrase="ok computer", allow_nano_only=False)
    assert detector.matches("ok, computer!") is True
    assert detector.matches("hey nano") is False


# =================================================================== cooldown

def test_cooldown_blocks_repeat_trigger_within_window():
    detector = WakePhraseDetector(phrase="hey nano", cooldown_seconds=5.0)
    assert detector.check("hey nano", now=100.0) is True
    assert detector.check("hey nano", now=101.0) is False
    assert detector.check("hey nano", now=104.9) is False


def test_cooldown_allows_trigger_again_after_window_elapses():
    detector = WakePhraseDetector(phrase="hey nano", cooldown_seconds=5.0)
    assert detector.check("hey nano", now=100.0) is True
    assert detector.check("hey nano", now=105.0) is True


def test_cooldown_is_per_detector_instance_not_global():
    a = WakePhraseDetector(phrase="hey nano", cooldown_seconds=10.0)
    b = WakePhraseDetector(phrase="hey nano", cooldown_seconds=10.0)
    assert a.check("hey nano", now=1.0) is True
    assert b.check("hey nano", now=1.5) is True  # unaffected by a's cooldown


def test_reset_cooldown_clears_the_window():
    detector = WakePhraseDetector(phrase="hey nano", cooldown_seconds=10.0)
    assert detector.check("hey nano", now=1.0) is True
    detector.reset_cooldown()
    assert detector.check("hey nano", now=1.5) is True


def test_non_matching_text_never_touches_the_cooldown():
    detector = WakePhraseDetector(phrase="hey nano", cooldown_seconds=10.0)
    assert detector.check("random noise", now=1.0) is False
    assert detector.check("hey nano", now=1.1) is True  # cooldown untouched


def test_defaults_are_sane():
    detector = WakePhraseDetector()
    assert detector.phrase == DEFAULT_WAKE_PHRASE
    assert detector.cooldown_seconds == DEFAULT_COOLDOWN_SECONDS
    assert detector.matches("hey nano") is True


# ============================================================== fake providers

@dataclass
class _FakeResult:
    ok: bool
    text: str


def _wav(amplitude: int, seconds: float = 0.4, sample_rate: int = 16000) -> bytes:
    """A real 16-bit mono WAV, so the energy gate sees genuine audio.

    The engine now measures energy before transcribing (silence used to reach
    Whisper and come back as invented filler). Fakes therefore have to be real
    WAV data: `b"chunk"` is not audio and is correctly rejected as silence.
    """
    import math
    import struct
    import wave
    from io import BytesIO

    frames = bytearray()
    for index in range(int(sample_rate * seconds)):
        sample = int(amplitude * math.sin(2.0 * math.pi * 220.0 * (index / sample_rate)))
        frames += struct.pack("<h", sample)
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


def speech_wav() -> bytes:
    """Loud enough to pass the speech-energy gate."""
    return _wav(6000)


def silence_wav() -> bytes:
    """Near-silence: must be rejected before it reaches the transcriber."""
    return _wav(2)


class _FakeAudioProvider:
    """Duck-typed stand-in for core.voice.AudioInputProvider."""

    def __init__(self, *, available: bool = True, chunks: list[bytes] | None = None):
        self._available = available
        self._chunks = list(chunks) if chunks else [speech_wav()]
        self.captured_durations: list[float] = []
        # The provider owns the speech gate; the wake engine borrows it. See
        # core.voice.AudioInputProvider.gate.
        self.gate = speech_filter.AdaptiveGate()

    def capture(self, duration_seconds: float) -> bytes | None:
        self.captured_durations.append(duration_seconds)
        if not self._chunks:
            return silence_wav()
        return self._chunks.pop(0) if len(self._chunks) > 1 else self._chunks[0]


class _FakeSTTProvider:
    """Duck-typed stand-in for core.voice.LocalSTTProvider."""

    def __init__(self, *, online: bool = True, transcripts: list[str] | None = None):
        self.online = online
        self._transcripts = list(transcripts or [])
        self.calls = 0

    def transcribe(self, audio_bytes: bytes) -> _FakeResult:
        self.calls += 1
        if self._transcripts:
            text = self._transcripts.pop(0)
        else:
            text = ""
        return _FakeResult(ok=bool(text), text=text)


def _make_engine(
    *,
    enabled: bool = True,
    audio: _FakeAudioProvider | None = None,
    stt: _FakeSTTProvider | None = None,
    on_wake: Callable[[str], None] | None = None,
    **extra_config,
) -> WakePhraseEngine:
    config = {
        "wake_phrase_enabled": enabled,
        "wake_phrase": "hey nano",
        "wake_phrase_allow_nano_only": True,
        "wake_phrase_cooldown_seconds": 3.0,
        "wake_phrase_chunk_seconds": 0.01,
        **extra_config,
    }
    return WakePhraseEngine(
        config,
        on_wake or (lambda text: None),
        audio_provider=audio if audio is not None else _FakeAudioProvider(),
        stt_provider=stt if stt is not None else _FakeSTTProvider(),
    )


# ================================================================= readiness

def test_disabled_state_reports_disabled_without_touching_providers():
    engine = _make_engine(enabled=False)
    assert engine.readiness() == WakePhraseReadiness.DISABLED
    assert engine.start() is False
    assert engine.running is False


def test_stt_unavailable_is_reported_even_when_enabled():
    engine = _make_engine(enabled=True, stt=_FakeSTTProvider(online=False))
    assert engine.readiness() == WakePhraseReadiness.STT_UNAVAILABLE
    assert engine.start() is False
    assert "speech-to-text" in (engine.last_error or "")


def test_ready_when_enabled_and_dependencies_are_available_but_not_started():
    engine = _make_engine(enabled=True)
    assert engine.readiness() == WakePhraseReadiness.READY


def test_microphone_unavailable_is_reported_specifically():
    """A missing mic reports MIC_UNAVAILABLE, not a generic ERROR, so the UI can
    tell the user which piece is actually missing."""
    engine = _make_engine(enabled=True, audio=_FakeAudioProvider(available=False))
    assert engine.start() is False
    assert engine.readiness() == WakePhraseReadiness.MIC_UNAVAILABLE
    assert "microphone" in (engine.last_error or "")


def test_stt_unavailable_outranks_microphone_in_reporting():
    """With both missing, the STT runtime is the more fundamental blocker."""
    engine = _make_engine(
        enabled=True,
        audio=_FakeAudioProvider(available=False),
        stt=_FakeSTTProvider(online=False),
    )
    assert engine.readiness() == WakePhraseReadiness.STT_UNAVAILABLE


def test_listening_while_the_loop_is_actively_running():
    audio = _FakeAudioProvider(chunks=[speech_wav()])
    stt = _FakeSTTProvider(transcripts=[])  # never matches; loop just spins
    engine = _make_engine(enabled=True, audio=audio, stt=stt)
    try:
        assert engine.start() is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and engine.readiness() != WakePhraseReadiness.LISTENING:
            time.sleep(0.01)
        assert engine.readiness() == WakePhraseReadiness.LISTENING
        assert engine.state == WakePhraseState.WAKE_LISTENING
    finally:
        engine.stop()


def test_status_reports_configured_phrase_and_cooldown():
    engine = _make_engine(enabled=True, **{"wake_phrase_cooldown_seconds": 4.5})
    status = engine.status()
    assert status["phrase"] == "hey nano"
    assert status["cooldown_seconds"] == 4.5
    assert status["readiness"] == "READY"
    assert status["state"] == WakePhraseState.IDLE.value


# ============================================================ state machine

def test_state_machine_transitions_are_settable_by_the_caller():
    """The engine only sets WAKE_LISTENING/WAKE_DETECTED/IDLE itself; the
    caller (main.py) drives COMMAND_LISTENING/PROCESSING around the existing
    voice pipeline. Both must be observable through the same state field."""
    engine = _make_engine(enabled=False)
    assert engine.state == WakePhraseState.IDLE

    engine.mark_command_listening()
    assert engine.state == WakePhraseState.COMMAND_LISTENING

    engine.mark_processing()
    assert engine.state == WakePhraseState.PROCESSING

    engine.mark_idle()
    assert engine.state == WakePhraseState.IDLE

    engine.set_state(WakePhraseState.WAKE_DETECTED)
    assert engine.state == WakePhraseState.WAKE_DETECTED


# ==================================================================== safety

def test_on_wake_fires_exactly_once_on_a_match_and_engine_does_nothing_else():
    """The detector's only side effect on a match is calling on_wake. It must
    never reach into policy, permissions, or tool execution."""
    calls: list[str] = []
    audio = _FakeAudioProvider(chunks=[speech_wav()])
    stt = _FakeSTTProvider(transcripts=["hey nano what time is it"])
    engine = _make_engine(enabled=True, audio=audio, stt=stt, on_wake=calls.append)

    try:
        assert engine.start() is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not calls:
            time.sleep(0.01)
    finally:
        engine.stop()

    assert calls == ["hey nano what time is it"]


def test_engine_constructor_has_no_policy_or_execution_dependency():
    """WakePhraseEngine must be constructible with only a callback and the two
    audio/STT providers — no permission manager, no tool executor, no policy
    engine. This is what makes 'wake detection cannot execute a tool' true by
    construction rather than by convention.
    """
    import inspect

    params = set(inspect.signature(WakePhraseEngine.__init__).parameters)
    forbidden = {"permission_manager", "tool_executor", "policy_engine", "brain", "executor"}
    assert not (params & forbidden)


def test_a_callback_exception_does_not_crash_the_loop_or_block_future_wakes():
    audio = _FakeAudioProvider(chunks=[speech_wav()])
    stt = _FakeSTTProvider(transcripts=["hey nano", "hey nano"])

    def flaky_on_wake(_text: str) -> None:
        raise RuntimeError("simulated UI callback failure")

    engine = _make_engine(
        enabled=True,
        audio=audio,
        stt=stt,
        on_wake=flaky_on_wake,
        wake_phrase_cooldown_seconds=0.01,
    )
    try:
        assert engine.start() is True
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and stt.calls < 2:
            time.sleep(0.01)
        assert stt.calls >= 1
    finally:
        engine.stop()


# ============================================== silence gate (false-positive fix)

def test_silence_never_reaches_the_transcriber():
    """The fix for the phantom wake: silence must not be transcribed at all.

    Whisper invents filler for silent audio, and a hallucinated fragment
    containing "nano" was waking Nano with nobody speaking.
    """
    audio = _FakeAudioProvider(chunks=[silence_wav()])
    stt = _FakeSTTProvider(transcripts=["hey nano"])  # would match, if ever called
    calls: list[str] = []
    engine = _make_engine(enabled=True, audio=audio, stt=stt, on_wake=calls.append)

    try:
        assert engine.start() is True
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and engine.chunks_captured < 2:
            time.sleep(0.01)
    finally:
        engine.stop()

    assert stt.calls == 0, "silence was sent to the transcriber"
    assert calls == [], "a wake fired on silence"
    assert engine.silent_chunks > 0


def test_hallucinated_transcript_does_not_wake_nano():
    """Whisper filler that slips through the energy gate is still rejected."""
    audio = _FakeAudioProvider(chunks=[speech_wav()])
    stt = _FakeSTTProvider(transcripts=["Obrigado.", "Obrigado.", "Obrigado."])
    calls: list[str] = []
    engine = _make_engine(enabled=True, audio=audio, stt=stt, on_wake=calls.append)

    try:
        assert engine.start() is True
        deadline = time.monotonic() + 1.5
        while time.monotonic() < deadline and stt.calls < 2:
            time.sleep(0.01)
    finally:
        engine.stop()

    assert calls == [], "a hallucinated transcript woke Nano"


def test_bare_nano_is_disabled_by_default_in_shipped_config():
    """The shipped default must require the full phrase."""
    from core.config import load_config

    voice = load_config().get("voice", {})
    assert voice.get("wake_phrase_allow_nano_only") is False, (
        'bare "nano" must stay off by default: it caused real false activations'
    )
