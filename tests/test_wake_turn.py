"""What happens after "Hey Nano" is heard.

The defect these tests exist for: the user said "Hey Nano", said nothing else,
and roughly two minutes later Nano announced "Olá, como posso ajudá-lo?". The
chain was: silence was captured for the full command window, the transcriber
invented filler for it, and that filler was handed to the Brain as a request.

So the rule is: silence must never become a request. The turn ends, the session
returns to listening, and the Brain is never told anything happened.
"""
from __future__ import annotations

import asyncio
import math
import struct
import wave
from io import BytesIO

import pytest

from core import speech_filter
from core.voice import ProviderError, VoiceRuntime


def _wav(amplitude: int, seconds: float = 1.0, sample_rate: int = 16000) -> bytes:
    frames = bytearray()
    for index in range(int(sample_rate * seconds)):
        frames += struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * index / sample_rate)))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


SPEECH = _wav(6000)
SILENCE = _wav(2)


class _Result:
    def __init__(self, text: str, ok: bool = True):
        self.text = text
        self.ok = ok


class _Input:
    def __init__(self, audio: bytes | None, raises: Exception | None = None):
        self.audio = audio
        self.raises = raises
        self.windows: list[float] = []
        # Part of the real provider's contract, and named in the log line that
        # reports a dead microphone.
        self.device_index = None
        # The real AudioInputProvider owns the speech gate, because every
        # trigger records through it and so every trigger must judge speech by
        # the same number. A stand-in without one is not a stand-in for the
        # provider, and using the real gate here means these tests exercise the
        # real decision rather than a mock of it.
        self.gate = speech_filter.AdaptiveGate()

    def capture(self, duration_seconds):
        self.windows.append(duration_seconds)
        if self.raises:
            raise self.raises
        return self.audio


class _STT:
    def __init__(self, text: str = "que horas sao"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return _Result(self.text)


class _Session:
    def __init__(self):
        self.session_id = "test-session"
        self.transitions: list[str] = []

    def _record(self, name):
        self.transitions.append(name)

    def waiting_for_wake_word(self):
        self._record("waiting")

    def start_listening(self):
        self._record("listening")

    def transcribing(self):
        self._record("transcribing")

    def cancel(self):
        self._record("cancelled")

    def error(self, _detail):
        self._record("error")

    def status(self):
        return {"state": self.transitions[-1] if self.transitions else "idle"}


class _Engine:
    def __init__(self, audio: bytes | None, *, transcript: str = "que horas sao", raises=None, enabled=True):
        self.enabled = enabled
        self.session = _Session()
        self.input_provider = _Input(audio, raises)
        self.stt_provider = _STT(transcript)


class _Bus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def publish(self, event, payload=None):
        self.events.append((event, payload or {}))

    def names(self):
        return [name for name, _ in self.events]


class _Brain:
    """A Brain that fails the test if it is ever consulted."""

    def __init__(self):
        self.calls = 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return {"response": "should never happen"}


def _runtime(engine, *, bus=None, timeout=7):
    return VoiceRuntime(
        engine,
        brain=_Brain(),
        event_bus=bus or _Bus(),
        config={"voice": {"wake_command_timeout_seconds": timeout}},
    )


def _turn(runtime, **kwargs):
    return asyncio.run(runtime.process_wake_word_turn(**kwargs))


# ============================================================ silence is not a request

def test_silence_after_the_wake_word_cancels_the_turn():
    bus = _Bus()
    engine = _Engine(SILENCE)
    result = _turn(_runtime(engine, bus=bus))

    assert result["ok"] is False
    assert result["error"] == "no_speech"
    assert result["cancelled"] is True
    assert engine.stt_provider.calls == 0, "silence was sent to the transcriber"
    assert "VoiceWakeCancelled" in bus.names()


def test_a_cancelled_turn_never_reaches_the_brain():
    """The exact failure: a phantom greeting from a turn nobody spoke in."""
    engine = _Engine(SILENCE)
    runtime = _runtime(engine)
    _turn(runtime)
    assert runtime.brain.calls == 0, "the Brain answered a request that was never made"


def test_no_audio_at_all_is_reported_as_its_own_failure():
    """A dead microphone and a quiet room are different problems.

    They used to return the identical "no_speech", which is precisely why a
    real defect survived until a human tested it: the log said "nao ouvi nada"
    whether the device had returned nothing or had returned a perfectly good
    voice that the gate then rejected. The turn is still cancelled and the
    Brain is still never consulted -- only the reason is now specific.
    """
    engine = _Engine(None)
    result = _turn(_runtime(engine))
    assert result["error"] == "no_audio"
    assert result["cancelled"] is True
    assert engine.stt_provider.calls == 0


def test_a_quiet_room_is_reported_with_the_numbers_that_decided_it():
    """The measurement that would have identified the bug in one glance."""
    engine = _Engine(SILENCE)
    result = _turn(_runtime(engine))
    assert result["error"] == "no_speech"
    assert "rms" in result and "threshold" in result, (
        "a rejected turn must record the energy it measured and the bar it "
        "was compared against, or the next threshold bug is invisible again"
    )
    assert result["rms"] < result["threshold"]


def test_a_hallucinated_transcript_is_rejected_after_transcription():
    """Filler can survive the energy gate when there is background noise."""
    engine = _Engine(SPEECH, transcript="Obrigado.")
    runtime = _runtime(engine)
    result = _turn(runtime)

    assert result["ok"] is False
    assert result["error"] == "no_usable_command"
    assert result["cancelled"] is True
    assert runtime.brain.calls == 0


def test_an_empty_transcript_is_rejected():
    engine = _Engine(SPEECH, transcript="   ")
    result = _turn(_runtime(engine))
    assert result["error"] == "no_usable_command"


def test_a_cancelled_turn_returns_the_session_to_listening():
    engine = _Engine(SILENCE)
    _turn(_runtime(engine))
    assert "cancelled" in engine.session.transitions, "the session was left mid-turn"


def test_a_cancelled_turn_explains_itself_in_portuguese():
    result = _turn(_runtime(_Engine(SILENCE)))
    assert result["detail"], "a cancelled turn must say why"
    assert "escutar" in result["detail"].lower()


# ==================================================================== timeout

def test_the_command_window_is_the_configured_timeout():
    engine = _Engine(SILENCE)
    runtime = _runtime(engine, timeout=9)
    _turn(runtime)
    assert engine.input_provider.windows == [9], "the configured wake timeout was ignored"


def test_the_timeout_is_bounded_so_the_microphone_is_never_held_open():
    """An unbounded window would hold the mic and block the wake engine."""
    assert _runtime(_Engine(SILENCE), timeout=900).command_timeout_seconds <= 15
    assert _runtime(_Engine(SILENCE), timeout=0).command_timeout_seconds >= 3


def test_an_explicit_duration_overrides_the_default():
    engine = _Engine(SILENCE)
    _turn(_runtime(engine, timeout=7), duration_seconds=4)
    assert engine.input_provider.windows == [4]


def test_the_listening_window_is_announced_so_the_ui_can_show_it():
    bus = _Bus()
    _turn(_runtime(_Engine(SILENCE), bus=bus, timeout=6))
    listening = [payload for name, payload in bus.events if name == "VoiceCommandListening"]
    assert listening, "the UI is never told that Nano started listening"
    assert listening[0]["timeout"] == 6


def test_the_wake_itself_is_announced_before_listening_starts():
    """The audible/visible acknowledgment depends on this event."""
    bus = _Bus()
    _turn(_runtime(_Engine(SILENCE), bus=bus))
    names = bus.names()
    assert "WakeWordDetected" in names
    assert names.index("WakeWordDetected") < names.index("VoiceCommandListening")


# ================================================================== failures

def test_a_microphone_failure_is_reported_not_swallowed():
    engine = _Engine(SPEECH, raises=ProviderError("device busy"))
    bus = _Bus()
    result = _turn(_runtime(engine, bus=bus))

    assert result["ok"] is False
    assert result["error"] == "microphone_failed"
    assert result["detail"], "a microphone failure must carry the reason"
    assert "VoiceError" in bus.names()


def test_a_disabled_voice_stack_refuses_the_turn_without_capturing():
    engine = _Engine(SPEECH, enabled=False)
    result = _turn(_runtime(engine))
    assert result["error"] == "voice_disabled"
    assert engine.input_provider.windows == [], "the microphone was opened while voice was disabled"


# ================================================ the gate itself, on real shapes

def test_the_energy_gate_separates_speech_from_silence():
    assert speech_filter.has_speech_energy(SPEECH) is True
    assert speech_filter.has_speech_energy(SILENCE) is False


def test_the_gate_is_safe_on_garbage_input():
    """A malformed buffer must read as 'no speech', never crash the loop."""
    for payload in (b"", b"chunk", b"\x00" * 10):
        assert speech_filter.has_speech_energy(payload) is False


def test_known_whisper_filler_is_recognised_as_hallucination():
    for filler in ("Obrigado.", "obrigado", "Thank you.", "Legendas pela comunidade Amara.org"):
        assert speech_filter.is_hallucination(filler) is True, f"{filler!r} was accepted as a command"


def test_a_real_command_is_not_mistaken_for_filler():
    for command in ("que horas sao", "abre o navegador", "cria uma tarefa nova"):
        assert speech_filter.is_hallucination(command) is False
        assert speech_filter.is_usable_command(command) is True


def test_describe_reports_what_the_gate_measured():
    """Used for diagnosing a mic that is too quiet, rather than guessing."""
    described = speech_filter.describe(SPEECH)
    for field in ("bytes", "rms", "voiced_ratio", "has_speech"):
        assert field in described, f"describe() is missing '{field}'"
    assert described["has_speech"] is True
    assert speech_filter.describe(SILENCE)["has_speech"] is False
