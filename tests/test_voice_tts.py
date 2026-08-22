"""A voice turn must speak every time, not only the first time.

THE DEFECT THESE EXIST FOR
--------------------------
After the hotkey pipeline started working, the human retest found Nano spoke on
the first voice turn of a session and was mute on every one after it. The reply
still appeared in the chat, and the overlay went "A ouvir" -> "A processar" ->
"Pronto" with no visible "A falar", which read exactly like the SPEAKING branch
being skipped.

It was not. SPEAKING was reached every time. The failure was one line lower:

* ``LocalTTSProvider.speak`` synthesised into ONE fixed filename in
  ``os.getcwd()`` -- ``nano_tts_tmp.mp3``, in the project root;
* ``pygame.mixer.music.load()`` holds that file open and nothing ever called
  ``unload()``;
* so the cleanup ``os.unlink`` raised PermissionError on Windows into an
  ``except OSError: pass`` that discarded it silently;
* and the NEXT utterance tried to write the same still-locked path and died
  with ``[Errno 13] Permission denied``.

Measured before the fix: turn 1 returned True, turns 2 and 3 both raised
PermissionError. The whole failure took milliseconds, which is why "A falar"
was never seen -- it was displayed and replaced faster than a person can
perceive, not skipped.

``AudioOutputProvider.play`` had the identical bug, plus its unlink sat outside
a ``finally`` so any playback error leaked the file too.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import os
import sys
import types

import pytest

from core.voice import (
    AudioOutputProvider, LocalTTSProvider, VoiceRuntime, _discard_scratch_audio,
    _new_scratch_audio_path,
)


# ======================================================================
#  A pygame that behaves like Windows: a loaded file stays LOCKED
# ======================================================================

class FakeMixerMusic:
    """Models the one behaviour that mattered: load() locks, unload() frees."""

    def __init__(self, owner):
        self._owner = owner

    def load(self, path):
        self._owner.locked.add(os.path.abspath(path))
        self._owner.loaded.append(path)

    def play(self):
        self._owner.plays += 1

    def get_busy(self):
        return False

    def unload(self):
        self._owner.unloads += 1
        self._owner.locked.clear()

    def stop(self):
        self._owner.stops += 1


class FakePygame(types.ModuleType):
    def __init__(self):
        super().__init__("pygame")
        # importlib.util.find_spec() raises for a module in sys.modules with no
        # spec, and the provider calls it to decide whether it is available.
        self.__spec__ = importlib.machinery.ModuleSpec("pygame", None)
        self.locked: set[str] = set()
        self.loaded: list[str] = []
        self.plays = 0
        self.unloads = 0
        self.stops = 0
        self.mixer = types.SimpleNamespace(
            get_init=lambda: True, init=lambda **k: None, music=FakeMixerMusic(self))


@pytest.fixture
def fake_pygame(monkeypatch):
    module = FakePygame()
    monkeypatch.setitem(sys.modules, "pygame", module)
    return module


@pytest.fixture
def fake_edge_tts(monkeypatch, fake_pygame):
    """edge-tts that refuses to overwrite a file the mixer still holds.

    This is the Windows semantics the real bug depended on. A test using a
    permissive fake would pass against the broken code and prove nothing.
    """
    written: list[str] = []

    class Communicate:
        def __init__(self, text, voice, rate=None, volume=None):
            self.text = text

        async def save(self, path):
            if os.path.abspath(path) in fake_pygame.locked:
                raise PermissionError(f"[Errno 13] Permission denied: '{path}'")
            written.append(path)
            with open(path, "wb") as handle:
                handle.write(b"\x00" * 32)

    module = types.ModuleType("edge_tts")
    module.__spec__ = importlib.machinery.ModuleSpec("edge_tts", None)
    module.Communicate = Communicate
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return written


# ======================================================================
#  1. The file lifecycle: the actual root cause
# ======================================================================

def test_speaking_twice_in_a_row_works(fake_edge_tts, fake_pygame):
    """The exact reported failure: mute after the first utterance."""
    provider = LocalTTSProvider({})
    provider.online = True

    assert asyncio.run(provider.speak("primeira frase")) is True
    assert asyncio.run(provider.speak("segunda frase")) is True, (
        "the second utterance failed -- this is the bug where Nano spoke once "
        "per launch and was silent afterwards"
    )


def test_speaking_five_times_in_a_row_works(fake_edge_tts, fake_pygame):
    provider = LocalTTSProvider({})
    provider.online = True
    for index in range(5):
        assert asyncio.run(provider.speak(f"frase {index}")) is True


def test_each_utterance_gets_its_own_file(fake_edge_tts, fake_pygame):
    provider = LocalTTSProvider({})
    provider.online = True
    asyncio.run(provider.speak("uma"))
    asyncio.run(provider.speak("outra"))
    assert len(set(fake_edge_tts)) == 2, (
        "reusing one filename is what let a stale lock block the next turn"
    )


def test_the_mixer_is_released_after_every_utterance(fake_edge_tts, fake_pygame):
    """unload() is the call whose absence caused the lock."""
    provider = LocalTTSProvider({})
    provider.online = True
    asyncio.run(provider.speak("olá"))
    assert fake_pygame.unloads == 1
    assert fake_pygame.locked == set(), "the file is still held after playback"


def test_no_scratch_audio_is_left_behind(fake_edge_tts, fake_pygame):
    provider = LocalTTSProvider({})
    provider.online = True
    asyncio.run(provider.speak("olá"))
    for path in fake_edge_tts:
        assert not os.path.exists(path), f"{path} was left on disk"


def test_scratch_audio_never_lands_in_the_project(fake_edge_tts, fake_pygame):
    """It used to be written to os.getcwd(), i.e. the repository root."""
    provider = LocalTTSProvider({})
    provider.online = True
    asyncio.run(provider.speak("olá"))
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    for path in fake_edge_tts:
        assert not os.path.abspath(path).startswith(repo_root), (
            f"scratch audio was written inside the project: {path}"
        )


def test_a_playback_failure_still_releases_and_deletes_the_file(fake_edge_tts, fake_pygame):
    """A leaked lock after an error would silence every later turn."""
    def explode():
        raise RuntimeError("device disappeared")

    fake_pygame.mixer.music.play = explode
    provider = LocalTTSProvider({})
    provider.online = True

    with pytest.raises(Exception):
        asyncio.run(provider.speak("olá"))

    assert fake_pygame.unloads == 1, "the mixer must be released even on failure"
    for path in fake_edge_tts:
        assert not os.path.exists(path)


def test_the_speaker_test_path_has_the_same_lifecycle(fake_pygame):
    """AudioOutputProvider.play carried an identical copy of the bug."""
    provider = AudioOutputProvider({})
    assert asyncio.run(provider.play(b"\x00\x01" * 64)) is True
    assert asyncio.run(provider.play(b"\x00\x01" * 64)) is True
    assert fake_pygame.unloads == 2
    assert fake_pygame.locked == set()


def test_discarding_a_missing_scratch_file_is_not_an_error():
    path = _new_scratch_audio_path(".mp3")
    os.unlink(path)
    _discard_scratch_audio(path)      # must not raise


# ======================================================================
#  2. The speaking contract
# ======================================================================

class _Speaker:
    """Stands in for VoiceEngine, counting what was actually spoken."""

    def __init__(self, *, enabled: bool = True, fail: bool = False):
        self.enabled = enabled
        self.fail = fail
        self.spoken: list[str] = []
        self.session = _Session()

    async def speak(self, text):
        self.spoken.append(text)
        if self.fail:
            return False
        return True


class _Session:
    def __init__(self):
        self.session_id = "test"
        self.state_calls: list[str] = []

    def speaking(self): self.state_calls.append("speaking")
    def cancel(self): self.state_calls.append("cancel")
    def status(self): return {"state": "IDLE"}


def runtime_with(config: dict, *, fail: bool = False, enabled: bool = True) -> VoiceRuntime:
    engine = _Speaker(enabled=enabled, fail=fail)
    return VoiceRuntime(engine, config=config)


VOICE_ON = {"voice": {"tts_enabled": True, "voice_reply_tts": True, "typed_chat_tts": False}}


@pytest.mark.parametrize("source", ["hotkey", "ui", "wake_phrase"])
def test_every_voice_source_speaks_a_real_answer(source):
    """The source must not silently change whether Nano speaks."""
    runtime = runtime_with(VOICE_ON)
    should, reason = runtime.speaking_decision(source, "são três horas")
    assert should is True, f"{source} refused to speak: {reason}"
    assert reason == "ok"


def test_an_empty_answer_is_not_spoken():
    runtime = runtime_with(VOICE_ON)
    for empty in ("", "   ", None):
        should, reason = runtime.speaking_decision("hotkey", empty)
        assert should is False
        assert reason == "empty_response"


def test_turning_speech_off_is_respected_and_explained():
    runtime = runtime_with({"voice": {"tts_enabled": False, "voice_reply_tts": True}})
    should, reason = runtime.speaking_decision("hotkey", "olá")
    assert should is False
    assert reason == "tts_enabled=false"


def test_voice_reply_tts_false_is_respected_and_explained():
    runtime = runtime_with({"voice": {"tts_enabled": True, "voice_reply_tts": False}})
    should, reason = runtime.speaking_decision("hotkey", "olá")
    assert should is False
    assert reason == "voice_reply_tts=false"


def test_typed_chat_tts_never_silences_a_voice_turn():
    """The two settings must not be coupled in either direction."""
    runtime = runtime_with({"voice": {"tts_enabled": True, "voice_reply_tts": True,
                                      "typed_chat_tts": False}})
    should, _ = runtime.speaking_decision("hotkey", "olá")
    assert should is True, "a voice turn must not be muted by the TYPED chat setting"


def test_a_settings_change_applies_to_the_very_next_turn():
    """Live settings, not a snapshot taken when the runtime was built."""
    config = {"voice": {"tts_enabled": True, "voice_reply_tts": True}}
    runtime = runtime_with(config)
    assert runtime.speaking_decision("hotkey", "olá")[0] is True

    config["voice"]["voice_reply_tts"] = False       # what update_setting does
    assert runtime.speaking_decision("hotkey", "olá")[0] is False

    config["voice"]["voice_reply_tts"] = True
    assert runtime.speaking_decision("hotkey", "olá")[0] is True


def test_a_disabled_voice_stack_does_not_claim_it_will_speak():
    runtime = runtime_with(VOICE_ON, enabled=False)
    should, reason = runtime.speaking_decision("hotkey", "olá")
    assert should is False
    assert reason == "voice_disabled"


def test_a_caller_asking_for_silence_is_obeyed():
    runtime = runtime_with(VOICE_ON)
    should, reason = runtime.speaking_decision("hotkey", "olá", speak=False)
    assert should is False
    assert reason == "caller_requested_silence"


def test_every_refusal_carries_a_reason():
    """A silent turn must never be unexplained again."""
    for config in ({"voice": {"tts_enabled": False}},
                   {"voice": {"voice_reply_tts": False}},
                   {"voice": {}}):
        runtime = runtime_with(config)
        for text, speak in (("", True), ("olá", False), ("olá", True)):
            should, reason = runtime.speaking_decision("hotkey", text, speak=speak)
            assert reason, "the decision must always state a reason"
            if not should:
                assert reason != "ok"


# ======================================================================
#  3. Typed chat stays silent (the other half of the contract)
# ======================================================================

def test_typed_chat_stays_silent_by_default():
    import core.main as main

    voice_cfg = main.CONFIG.setdefault("voice", {})
    before = dict(voice_cfg)
    try:
        voice_cfg.update({"tts_enabled": True, "typed_chat_tts": False,
                          "voice_reply_tts": True})
        assert main._should_speak("text") is False, "typing must not make Nano talk"
        assert main._should_speak("voice") is True, "speaking must make Nano talk back"
    finally:
        voice_cfg.clear()
        voice_cfg.update(before)


def test_the_master_switch_silences_both():
    import core.main as main

    voice_cfg = main.CONFIG.setdefault("voice", {})
    before = dict(voice_cfg)
    try:
        voice_cfg.update({"tts_enabled": False, "typed_chat_tts": True,
                          "voice_reply_tts": True})
        assert main._should_speak("text") is False
        assert main._should_speak("voice") is False
    finally:
        voice_cfg.clear()
        voice_cfg.update(before)


# ======================================================================
#  4. The whole turn, repeatedly -- the shape of the human bug
# ======================================================================

class _TurnSession:
    """The full VoiceSession surface a complete turn drives."""

    def __init__(self):
        self.session_id = "test-turn"
        self.transitions: list[str] = []

    def waiting_for_wake_word(self): self.transitions.append("waiting")
    def start_listening(self): self.transitions.append("listening")
    def transcribing(self): self.transitions.append("transcribing")
    def thinking(self): self.transitions.append("thinking")
    def speaking(self): self.transitions.append("speaking")
    def cancel(self): self.transitions.append("cancelled")
    def error(self, _detail): self.transitions.append("error")
    def status(self): return {"state": self.transitions[-1] if self.transitions else "idle"}


class _TurnEngine:
    """A voice engine that completes a turn without touching hardware."""

    def __init__(self, *, transcript="que horas sao", audio=b"AUDIO", fail_tts_times=0):
        self.enabled = True
        self.session = _TurnSession()
        self.stt_provider = _FixedSTT(transcript)
        self.input_provider = _FakeMic(audio)
        self.spoken: list[str] = []
        self._fail_remaining = fail_tts_times

    async def speak(self, text):
        self.spoken.append(text)
        if self._fail_remaining > 0:
            self._fail_remaining -= 1
            return False
        return True


class _FakeMic:
    def __init__(self, audio):
        self._available = True
        self.audio = audio
        self.device_index = None
        self.gate = _AlwaysSpeech()

    def capture(self, duration_seconds):
        return self.audio


class _AlwaysSpeech:
    """Stands in for the calibrated gate; this suite is about TTS, not gating."""
    calibrated = True
    threshold = 12.0
    last_rms = 500.0
    min_voiced_ratio = 0.1

    def has_speech(self, audio):
        return bool(audio)


class _FixedSTT:
    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return type("R", (), {"text": self.text, "ok": True})()


class _AnsweringBrain:
    """Brain stand-in: the runtime's quick path awaits an async generator."""

    def __init__(self, answer="São três horas."):
        self.answer = answer
        self.calls = 0

    def chat(self, text, stream=True):
        self.calls += 1
        answer = self.answer

        async def generate():
            yield answer

        return generate()


def _turn_runtime(engine, brain=None, config=None):
    return VoiceRuntime(engine, brain=brain or _AnsweringBrain(),
                        config=config or {"voice": {"tts_enabled": True,
                                                    "voice_reply_tts": True,
                                                    "wake_command_timeout_seconds": 5}})


def _run(runtime, source="hotkey"):
    phases: list[str] = []
    runtime.set_observer(on_phase=lambda p, d: phases.append(p))
    result = asyncio.run(runtime.run_voice_turn(source, chime=False))
    return result, phases


def test_a_hotkey_turn_emits_speaking_and_speaks_exactly_once():
    engine = _TurnEngine()
    result, phases = _run(_turn_runtime(engine))

    assert "SPEAKING" in phases, "the overlay never learned that Nano was talking"
    assert len(engine.spoken) == 1, f"TTS was called {len(engine.spoken)} times, expected 1"
    assert result["spoken"] is True
    assert result["should_speak"] is True
    assert result["speak_skip_reason"] is None


def test_two_consecutive_voice_turns_both_speak():
    """The regression that matters: the bug only appeared on turn two."""
    engine = _TurnEngine()
    runtime = _turn_runtime(engine)
    for turn in (1, 2):
        result, phases = _run(runtime)
        assert "SPEAKING" in phases, f"turn {turn} did not reach SPEAKING"
        assert result["spoken"] is True, f"turn {turn} did not speak"
    assert len(engine.spoken) == 2


def test_three_sequential_voice_turns_all_speak():
    engine = _TurnEngine()
    runtime = _turn_runtime(engine)
    for turn in range(1, 4):
        result, phases = _run(runtime)
        assert "SPEAKING" in phases, f"turn {turn} did not reach SPEAKING"
        assert result["spoken"] is True, f"turn {turn} was silent"
    assert len(engine.spoken) == 3


def test_a_cancelled_turn_never_speaks():
    """Silence in, silence out: no answer, no TTS."""
    class Silent(_AlwaysSpeech):
        def has_speech(self, audio):
            return False

    engine = _TurnEngine()
    engine.input_provider.gate = Silent()
    result, phases = _run(_turn_runtime(engine))

    assert result["cancelled"] is True
    assert "SPEAKING" not in phases
    assert engine.spoken == []


def test_voice_reply_tts_false_shows_the_answer_but_stays_quiet():
    engine = _TurnEngine()
    runtime = _turn_runtime(engine, config={"voice": {"tts_enabled": True,
                                                      "voice_reply_tts": False}})
    exchanges: list[str] = []
    runtime.set_observer(on_exchange=lambda tid, u, a: exchanges.append(a))
    result = asyncio.run(runtime.run_voice_turn("hotkey", chime=False))

    assert exchanges, "the answer must still appear in the conversation"
    assert engine.spoken == [], "TTS was called despite voice_reply_tts=false"
    assert result["speak_skip_reason"] == "voice_reply_tts=false"


def test_a_tts_failure_is_reported_and_the_next_turn_still_speaks():
    """One bad utterance must not mute the session -- the original symptom."""
    engine = _TurnEngine(fail_tts_times=1)
    runtime = _turn_runtime(engine)

    first, phases = _run(runtime)
    assert "SPEAKING" in phases
    assert first["spoken"] is False, "a failed TTS must be reported honestly"

    second, phases = _run(runtime)
    assert "SPEAKING" in phases
    assert second["spoken"] is True, "the session went mute after one failure"
    assert len(engine.spoken) == 2


def test_the_turn_records_the_decision_for_every_outcome():
    """should_speak and its reason are always present on a completed turn."""
    for config, expected in (
        ({"voice": {"tts_enabled": True, "voice_reply_tts": True}}, None),
        ({"voice": {"tts_enabled": False, "voice_reply_tts": True}}, "tts_enabled=false"),
        ({"voice": {"tts_enabled": True, "voice_reply_tts": False}}, "voice_reply_tts=false"),
    ):
        result, _ = _run(_turn_runtime(_TurnEngine(), config=config))
        assert "should_speak" in result
        assert result["speak_skip_reason"] == expected
