"""Manual voice activation must not depend on an automatic one.

THE DEFECT THESE EXIST FOR
--------------------------
The desktop migration made Ctrl+Shift+Space the primary way to talk to Nano and
turned the "Ei Nano" wake detector off by default. Both changes were correct.
Together they broke speaking to Nano entirely, in two independent places, and
neither showed up in a test:

1. **The speech gate belonged to the wake detector.** ``_command_has_speech``
   reached for ``wake_phrase_provider._engine.gate`` and used it only if it was
   ``calibrated`` -- which only ever happened as a side effect of the wake loop
   running. With the wake phrase off, every hotkey and UI turn fell through to
   ``speech_filter.has_speech_energy``'s fixed 220 RMS floor. On the user's
   machine Settings -> Test Microphone measured their voice at **RMS 124** and
   reported "Voz detetada (nivel 124, limiar 12)", while the hotkey compared the
   same voice against **220** and answered "nao ouvi nada". Same microphone,
   same person, seconds apart, eighteen times the bar.

2. **Voice readiness required a wake path.** ``VoiceEngine.readiness()``
   returned MODEL_MISSING when no wake path was usable, and the UI treats
   anything but READY as "voice unavailable" -- so the composer's microphone
   button was rendered ``disabled`` and could not be clicked at all.

Neither was fixed by changing a threshold. The gate moved to the microphone,
which is what every trigger already shares, and readiness stopped asking a
question about wake paths that has nothing to do with whether Nano can hear you.
"""
from __future__ import annotations

import asyncio
import math
import struct
import wave
from io import BytesIO

import pytest

from core import speech_filter
from core.voice import AudioInputProvider, VoiceEngine, VoiceRuntime


# --------------------------------------------------------------------- audio

def wav_at_rms(target_rms: float, seconds: float = 3.0, rate: int = 16000) -> bytes:
    """A continuous tone whose measured RMS is approximately ``target_rms``."""
    amplitude = target_rms * math.sqrt(2)
    frames = bytearray()
    for index in range(int(rate * seconds)):
        value = int(amplitude * math.sin(2 * math.pi * 180 * index / rate))
        frames += struct.pack("<h", max(-32768, min(32767, value)))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(bytes(frames))
    return buffer.getvalue()


#: The level the user's real voice actually measured on this machine, taken
#: from the Test Microphone reading in the bug report: "nivel 124, limiar 12".
HUMAN_VOICE_RMS = 124.0

#: A quiet room, measured on the same microphone moments later.
ROOM_TONE_RMS = 5.0


# --------------------------------------------------------------------- stubs

class _Input:
    """Stand-in for AudioInputProvider, including the gate it now owns."""

    def __init__(self, audio: bytes | None):
        self._available = True
        self.audio = audio
        self.device_index = None
        self.windows: list[float] = []
        self.captures = 0
        self.gate = speech_filter.AdaptiveGate()

    def capture(self, duration_seconds):
        self.captures += 1
        self.windows.append(duration_seconds)
        return self.audio


class _STT:
    def __init__(self, text: str = "que horas sao"):
        self.text = text
        self.calls = 0

    def transcribe(self, audio):
        self.calls += 1
        return type("R", (), {"text": self.text, "ok": True})()


class _Session:
    def __init__(self):
        self.session_id = "test"
        self.transitions: list[str] = []

    def waiting_for_wake_word(self): self.transitions.append("waiting")
    def start_listening(self): self.transitions.append("listening")
    def transcribing(self): self.transitions.append("transcribing")
    def cancel(self): self.transitions.append("cancelled")
    def error(self, _d): self.transitions.append("error")
    def speaking(self): self.transitions.append("speaking")
    def status(self): return {"state": self.transitions[-1] if self.transitions else "idle"}


class _EngineWithoutWake:
    """A voice engine with NO wake-phrase provider of any kind.

    This is the shape the hotkey has to work against: the primary activation
    must be self-sufficient, not quietly reliant on an object the wake path
    happens to create.
    """

    def __init__(self, audio: bytes | None, transcript: str = "que horas sao"):
        self.enabled = True
        self.session = _Session()
        self.input_provider = _Input(audio)
        self.stt_provider = _STT(transcript)


class _Brain:
    def __init__(self):
        self.calls = 0

    def chat(self, *a, **k):
        self.calls += 1
        return {"response": "ok"}


def make_runtime(engine, timeout: int = 7) -> VoiceRuntime:
    return VoiceRuntime(engine, brain=_Brain(),
                        config={"voice": {"wake_command_timeout_seconds": timeout}})


# ==========================================================================
#  1. The gate belongs to the microphone
# ==========================================================================

def test_the_speech_gate_is_owned_by_the_microphone():
    provider = AudioInputProvider({})
    assert isinstance(provider.gate, speech_filter.AdaptiveGate), (
        "the single audio-input authority must own the speech gate; every "
        "trigger records through it and so must judge speech by one number"
    )


def test_the_wake_detector_shares_the_microphone_gate_rather_than_owning_one():
    engine = VoiceEngine({"enabled": True})
    assert engine.wake_phrase_provider._engine.gate is engine.input_provider.gate, (
        "two gate objects means two thresholds, which is exactly how the hotkey "
        "and the Test Microphone button came to disagree"
    )


def test_calibrating_through_either_path_is_visible_to_both():
    engine = VoiceEngine({"enabled": True})
    engine.input_provider.gate.calibrate([40.0, 41.0, 39.0, 40.0])
    assert engine.wake_phrase_provider._engine.gate.calibrated is True
    assert engine.wake_phrase_provider._engine.gate.threshold == engine.input_provider.gate.threshold


# ==========================================================================
#  2. THE BUG: a real voice, with the wake phrase off
# ==========================================================================

def test_a_real_voice_is_accepted_with_the_wake_detector_switched_off():
    """The exact reported failure, at the exact measured level.

    RMS 124 is what the user's voice measured. The old code compared it against
    speech_filter's fixed 220 floor whenever the wake detector had not
    calibrated a gate, so it was rejected and the turn ended "nao ouvi nada".
    """
    runtime = make_runtime(_EngineWithoutWake(wav_at_rms(HUMAN_VOICE_RMS)))
    assert runtime._command_has_speech(wav_at_rms(HUMAN_VOICE_RMS)) is True, (
        f"a voice at RMS {HUMAN_VOICE_RMS:.0f} must be heard; it is well above "
        "the gate's own floor and the Test Microphone button already says so"
    )


def test_the_old_static_floor_would_still_reject_that_voice():
    """Proves the test above is not vacuous: the bar really was out of reach."""
    audio = wav_at_rms(HUMAN_VOICE_RMS)
    assert speech_filter.rms_of_wav(audio) < speech_filter.DEFAULT_SILENCE_RMS
    assert speech_filter.has_speech_energy(audio) is False, (
        "if the fixed floor ever accepts this level, this regression test has "
        "stopped describing the bug it was written for"
    )


def test_a_quiet_room_is_still_rejected():
    """No threshold was loosened: silence must never become a request."""
    runtime = make_runtime(_EngineWithoutWake(wav_at_rms(ROOM_TONE_RMS)))
    assert runtime._command_has_speech(wav_at_rms(ROOM_TONE_RMS)) is False


def test_the_command_gate_and_the_test_button_use_one_threshold():
    """They disagreed by 18x, which is the whole bug in one number."""
    engine = VoiceEngine({"enabled": True})
    runtime = VoiceRuntime(engine)
    gate = engine.wake_phrase_provider._engine.gate      # what Test Microphone reads
    audio = wav_at_rms(HUMAN_VOICE_RMS)

    button_says = (speech_filter.rms_of_wav(audio) >= gate.threshold
                   and speech_filter.voiced_ratio(audio, silence_rms=gate.threshold)
                   >= gate.min_voiced_ratio)
    turn_says = runtime._command_has_speech(audio)
    assert button_says == turn_says is True, (
        "the button that tests the microphone must agree with the thing it tests"
    )


def test_a_hotkey_turn_reaches_the_transcriber_with_no_wake_engine_present():
    """End to end through the real turn, with no wake provider in existence."""
    engine = _EngineWithoutWake(wav_at_rms(HUMAN_VOICE_RMS))
    runtime = make_runtime(engine)
    result = asyncio.run(runtime.run_voice_turn("hotkey", chime=False, speak=False))

    assert engine.input_provider.captures == 1, "the hotkey must record for itself"
    assert engine.stt_provider.calls == 1, (
        "a real voice must reach the transcriber even with the wake phrase off"
    )
    assert result.get("error") not in {"no_speech", "no_audio"}


# ==========================================================================
#  3. Readiness does not require a wake path
# ==========================================================================

def test_voice_is_ready_with_no_wake_path_at_all():
    """The reason the composer's microphone button could not be clicked."""
    engine = VoiceEngine({"enabled": True, "wake_phrase_enabled": False})
    readiness, blockers = engine.readiness()

    if readiness.value == "SETUP_REQUIRED":
        pytest.skip(f"voice runtime packages are not installed here: {blockers}")

    assert readiness.value == "READY", (
        "the UI disables the microphone button for any state but READY, so "
        f"reporting {readiness.value} because no wake path exists switches "
        "manual voice input off along with the automatic one"
    )
    assert blockers == []


def test_readiness_stays_ready_even_when_both_wake_paths_are_broken():
    """Behavioural, and deliberately not just "unchanged".

    Asserting only that readiness does not change would pass under the old code
    too, because it reported MODEL_MISSING before and after. The claim worth
    pinning is the absolute one: with both wake paths definitively broken, Nano
    can still hear you when you ask it to, so readiness is READY.
    """
    engine = VoiceEngine({"enabled": True})
    if engine.readiness()[0].value == "SETUP_REQUIRED":
        pytest.skip("voice runtime packages are not installed here")

    class Broken:
        def status(self):
            return {"model_status": "MODEL_MISSING", "readiness": "ERROR",
                    "error": "no model"}

    engine.wake_word_provider = Broken()
    engine.wake_phrase_provider = Broken()
    readiness, blockers = engine.readiness()

    assert readiness.value == "READY", (
        "whether Nano can hear you when you ask it to has nothing to do with "
        f"whether it can also hear you without being asked (got {readiness.value})"
    )
    assert blockers == []


# ==========================================================================
#  4. One pipeline, and it lets go afterwards
# ==========================================================================

def test_hotkey_and_ui_run_the_identical_turn():
    """No per-trigger duplicate of capture, gating or STT."""
    for source in ("hotkey", "ui"):
        engine = _EngineWithoutWake(wav_at_rms(HUMAN_VOICE_RMS))
        runtime = make_runtime(engine)
        asyncio.run(runtime.run_voice_turn(source, chime=False, speak=False))
        assert engine.input_provider.captures == 1
        assert engine.stt_provider.calls == 1


def test_a_no_speech_turn_releases_the_lock_so_the_next_one_can_run():
    """A rejected turn must not leave Nano permanently 'busy'."""
    engine = _EngineWithoutWake(wav_at_rms(ROOM_TONE_RMS))
    runtime = make_runtime(engine)

    first = asyncio.run(runtime.run_voice_turn("hotkey", chime=False, speak=False))
    assert first["error"] == "no_speech"

    status = runtime.turn_status()
    assert status["active"] is False, "the turn lock was not released"
    assert status["source"] is None
    assert status["phase"] in {"IDLE", "WAKE_LISTENING"}

    second = asyncio.run(runtime.run_voice_turn("hotkey", chime=False, speak=False))
    assert second.get("busy") is not True, "a second activation was refused as busy"
    assert engine.input_provider.captures == 2


def test_a_turn_with_no_wake_provider_never_claims_the_microphone_is_busy():
    """_take_microphone must not fail merely because no wake engine exists."""
    engine = _EngineWithoutWake(wav_at_rms(HUMAN_VOICE_RMS))
    runtime = make_runtime(engine)
    result = asyncio.run(runtime.run_voice_turn("hotkey", chime=False, speak=False))
    assert result.get("error") != "microphone_busy"


def test_only_one_capture_happens_per_turn():
    """One microphone owner: a turn records exactly once."""
    engine = _EngineWithoutWake(wav_at_rms(HUMAN_VOICE_RMS))
    runtime = make_runtime(engine)
    asyncio.run(runtime.run_voice_turn("hotkey", chime=False, speak=False))
    assert engine.input_provider.captures == 1
