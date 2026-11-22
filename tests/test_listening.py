"""Listening until the person stops talking, and "Ei Nano, <pedido>" in one breath.

What these tests pin down:
  * a voice turn no longer records a fixed seven seconds: it ends after a
    short pause, waits a bounded time for someone to start, and never records
    past the maximum;
  * the wake phrase has to OPEN what was said, so a conversation about NANO
    does not wake it, and the request after the phrase is kept;
  * the utterance listener transcribes nothing while nobody speaks, and a
    request said with the phrase reaches the turn without being asked again.
"""
from __future__ import annotations

import asyncio
import math
import struct
import threading
import time

import pytest

from core import listening, speech_filter
from core.listening import EndpointConfig, Endpointer, Hysteresis
from core.wake_phrase import WakePhraseDetector, WakePhraseEngine

RATE = 16000
BLOCK = 1024
BLOCK_SECONDS = BLOCK / RATE


def _tone(amplitude: int, seconds: float) -> bytes:
    frames = bytearray()
    for index in range(int(RATE * seconds)):
        frames += struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * index / RATE)))
    return bytes(frames)


def _blocks(*parts: tuple[int, float]) -> list[bytes]:
    pcm = b"".join(_tone(amplitude, seconds) for amplitude, seconds in parts)
    return [pcm[i:i + BLOCK * 2] for i in range(0, len(pcm), BLOCK * 2)]


class _LoudIsSpeech:
    """A detector stand-in: loud blocks are speech."""

    name = "test"

    def reset(self):
        pass

    def is_speech(self, pcm: bytes) -> bool:
        return listening.pcm_rms(pcm) > 500


def _reader(blocks: list[bytes]):
    queue = list(blocks)
    return lambda: queue.pop(0) if queue else None


# ================================================================ endpointer

def test_a_short_question_ends_after_the_pause_not_after_seven_seconds():
    ender = Endpointer(EndpointConfig(start_timeout=7, end_silence=0.8))
    reason = None
    t = 0.0
    while reason is None:
        speaking = 0.5 <= t < 1.5          # one second of speech
        reason = ender.feed(speaking, 0.064)
        t += 0.064
    assert reason == "end"
    assert t < 2.5, "it should stop well before the old fixed 7 s window"


def test_nobody_speaking_gives_up_at_the_start_timeout():
    ender = Endpointer(EndpointConfig(start_timeout=3))
    reasons = [ender.feed(False, 0.1) for _ in range(40)]
    assert "no_speech" in reasons
    assert ender.elapsed == pytest.approx(3.0, abs=0.11)


def test_a_click_is_not_the_start_of_speech():
    ender = Endpointer(EndpointConfig(start_timeout=2, min_speech=0.2))
    ender.feed(True, 0.064)            # 64 ms of noise
    ender.feed(False, 0.064)
    assert ender.started is False


def test_a_breath_in_the_middle_of_a_sentence_does_not_end_it():
    ender = Endpointer(EndpointConfig(end_silence=0.8))
    for _ in range(10):
        ender.feed(True, 0.064)
    for _ in range(6):                 # ~0.4 s pause
        assert ender.feed(False, 0.064) is None
    for _ in range(10):
        assert ender.feed(True, 0.064) is None
    assert ender.started


def test_a_long_request_is_capped():
    ender = Endpointer(EndpointConfig(max_seconds=4))
    reason = None
    for _ in range(200):
        reason = ender.feed(True, 0.1)
        if reason:
            break
    assert reason == "max"


def test_settings_are_bounded():
    cfg = EndpointConfig(start_timeout=999, end_silence=0, max_seconds=1).bounded()
    assert cfg.start_timeout == 15.0
    assert cfg.end_silence == 0.3
    assert cfg.max_seconds == 2.0


def test_hysteresis_keeps_a_word_that_dips_for_a_moment():
    decide = Hysteresis(0.5, 0.35)
    assert decide(0.8) is True
    assert decide(0.4) is True, "in between: the last answer stands"
    assert decide(0.2) is False
    assert decide(0.45) is False


# ================================================================ recording

def test_record_utterance_keeps_the_start_and_stops_after_the_pause():
    blocks = _blocks((0, 1.0), (8000, 1.0), (0, 3.0))
    utterance = listening.record_utterance(_reader(blocks), _LoudIsSpeech(),
                                           EndpointConfig(start_timeout=5, end_silence=0.8, preroll=0.3))
    assert utterance.reason == "end"
    assert utterance.heard_speech
    # ~0.3 s before + 1 s speech + ~0.8 s pause; never the whole 5 s.
    seconds = len(utterance.pcm) / 2 / RATE
    assert 1.8 <= seconds <= 2.6, seconds
    assert utterance.pcm[:2 * 1600] == bytes(2 * 1600) or listening.pcm_rms(utterance.pcm[:3200]) < 500, \
        "it starts with the quiet moment before the voice (nothing clipped)"


def test_record_utterance_reports_silence_honestly():
    utterance = listening.record_utterance(_reader(_blocks((0, 3.0))), _LoudIsSpeech(),
                                           EndpointConfig(start_timeout=2))
    assert utterance.reason == "no_speech"
    assert utterance.heard_speech is False
    assert utterance.pcm == b""


def test_record_utterance_with_no_audio_at_all_returns_none():
    assert listening.record_utterance(lambda: None, _LoudIsSpeech()) is None


def test_the_energy_detector_follows_the_microphone_gate():
    gate = speech_filter.AdaptiveGate()
    detector = listening.EnergyDetector(gate)
    assert detector.is_speech(_tone(6000, BLOCK_SECONDS)) is True
    assert detector.is_speech(bytes(BLOCK * 2)) is False


def test_without_the_voice_pack_the_energy_detector_is_used(monkeypatch):
    monkeypatch.setattr(listening, "load_silero", lambda: None)
    detector = listening.make_detector(speech_filter.AdaptiveGate())
    assert detector.name == "energy"


# ================================================================ the phrase

@pytest.mark.parametrize("text,command", [
    ("Ei, Nano! Abre o Spotify.", "Abre o Spotify."),
    ("Hey Nano", ""),
    ("Hi Nano, open Discord", "open Discord"),
    ("Olha, ei Nano, que horas são?", "que horas são?"),
])
def test_the_phrase_opening_an_utterance_calls_nano_and_keeps_the_request(text, command):
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False)
    assert detector.split_command(text) == (True, command)


@pytest.mark.parametrize("text", [
    "Ei, não.", "Ei, nanos!", "o nano é um assistente", "eu disse ei nano", "E aí, nano", "nanotecnologia",
])
def test_talking_about_nano_or_a_mishearing_does_not_call_it(text):
    detector = WakePhraseDetector(phrase="ei nano", allow_nano_only=False)
    assert detector.split_command(text)[0] is False


def test_a_bare_nano_counts_only_when_switched_on():
    assert WakePhraseDetector(phrase="hey nano", allow_nano_only=False).split_command("Nano, abre")[0] is False
    assert WakePhraseDetector(phrase="hey nano", allow_nano_only=True).split_command("Nano, abre") == (True, "abre")


# ================================================================ the listener

class _Result:
    def __init__(self, text):
        self.text = text
        self.ok = bool(text)


class _StreamingMic:
    """Streams prepared blocks, then silence, like an open microphone."""

    _available = True
    stream_open = False

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.gate = speech_filter.AdaptiveGate()
        self._stream_rate = RATE

    def open_stream(self):
        self.stream_open = True
        return True

    def close_stream(self):
        self.stream_open = False

    def read_stream(self, seconds):
        return None

    def capture(self, seconds):
        return None

    def read_block(self):
        time.sleep(0.0005)
        return self.blocks.pop(0) if self.blocks else bytes(BLOCK * 2)


class _STT:
    online = True

    def __init__(self, quick_text, full_text=""):
        self.quick_text = quick_text
        self.full_text = full_text or quick_text
        self.quick_calls = 0
        self.full_calls = 0

    def transcribe_quick(self, audio):
        self.quick_calls += 1
        return _Result(self.quick_text)

    def transcribe(self, audio):
        self.full_calls += 1
        return _Result(self.full_text)


def _engine(mic, stt, woke):
    def on_wake(text):
        woke.append((text, engine.take_command()))

    engine = WakePhraseEngine({"wake_phrase_enabled": True, "wake_phrase": "ei nano",
                               "wake_phrase_allow_nano_only": False},
                              on_wake, audio_provider=mic, stt_provider=stt)
    return engine


def _run_until(engine, condition, timeout=5.0):
    import core.wake_phrase as wake_phrase

    original = wake_phrase.listening.make_detector
    wake_phrase.listening.make_detector = lambda gate, prefer_silero=True: _LoudIsSpeech()
    try:
        assert engine.start() is True
        deadline = time.time() + timeout
        while time.time() < deadline and not condition():
            time.sleep(0.01)
    finally:
        engine.stop()
        time.sleep(0.05)
        wake_phrase.listening.make_detector = original


def test_nothing_is_transcribed_while_nobody_speaks():
    mic = _StreamingMic(_blocks((0, 4.0)))
    stt = _STT("Ei Nano")
    woke: list = []
    engine = _engine(mic, stt, woke)
    _run_until(engine, lambda: not mic.blocks, timeout=3)
    assert stt.quick_calls == 0 and stt.full_calls == 0
    assert woke == []
    assert engine.detector_name == "test", "it listened by utterance, with the voice detector"


def test_the_phrase_alone_wakes_nano_without_a_request():
    mic = _StreamingMic(_blocks((0, 0.5), (8000, 0.8), (0, 1.5)))
    stt = _STT("Ei, Nano!")
    woke: list = []
    engine = _engine(mic, stt, woke)
    _run_until(engine, lambda: bool(woke))
    assert woke == [("Ei, Nano!", "")]
    assert stt.full_calls == 0, "a short call needs only the quick check"


def test_a_request_in_the_same_breath_goes_with_the_wake():
    mic = _StreamingMic(_blocks((0, 0.5), (8000, 4.0), (0, 1.5)))
    stt = _STT("Ei Nano, abre o", full_text="Ei Nano, abre o Spotify e põe música.")
    woke: list = []
    engine = _engine(mic, stt, woke)
    _run_until(engine, lambda: bool(woke))
    assert woke and woke[0][1] == "abre o Spotify e põe música."
    assert stt.full_calls == 1, "the whole request is transcribed properly, once"


def test_a_conversation_never_wakes_nano():
    mic = _StreamingMic(_blocks((0, 0.5), (8000, 1.0), (0, 1.5)))
    stt = _STT("o nano é um assistente")
    woke: list = []
    engine = _engine(mic, stt, woke)
    _run_until(engine, lambda: stt.quick_calls >= 1)
    time.sleep(0.05)
    assert woke == []


# ================================================================ the turn

def test_a_request_heard_with_the_wake_skips_recording():
    from core.voice import VoiceRuntime

    class Input:
        gate = speech_filter.AdaptiveGate()
        device_index = None

        def capture(self, seconds):
            raise AssertionError("nothing should be recorded")

    class Session:
        session_id = "s"

        def __getattr__(self, name):
            return lambda *a, **k: None

    class Engine:
        enabled = True
        session = Session()
        input_provider = Input()
        stt_provider = None

    runtime = VoiceRuntime(Engine(), config={"voice": {}})
    seen = []

    async def process_request(text):
        seen.append(text)
        return {"ok": True, "response": "ok"}

    runtime.process_request = process_request
    result = asyncio.run(runtime.process_wake_word_turn(command="abre o Spotify"))
    assert result["ok"] is True
    assert seen == ["abre o Spotify"]


def test_the_turn_uses_the_utterance_recorder_when_the_microphone_has_it():
    from core.voice import VoiceRuntime

    calls = []

    class Input:
        gate = speech_filter.AdaptiveGate()
        device_index = None
        _available = True

        def capture_utterance(self, config):
            calls.append(config)
            return b"", listening.Utterance(pcm=b"", reason="no_speech", speech_seconds=0,
                                            seconds=config.start_timeout, detector="silero")

        def capture(self, seconds):
            raise AssertionError("the fixed window is no longer used")

    class Session:
        session_id = "s"

        def __getattr__(self, name):
            return lambda *a, **k: None

    class Engine:
        enabled = True
        session = Session()
        input_provider = Input()
        stt_provider = None

    runtime = VoiceRuntime(Engine(), config={"voice": {"wake_command_timeout_seconds": 6,
                                                       "voice_end_silence_seconds": 1.1}})
    result = asyncio.run(runtime.process_wake_word_turn())
    assert result["error"] == "no_speech" and result["cancelled"] is True
    assert calls[0].start_timeout == 6
    assert calls[0].end_silence == pytest.approx(1.1)


# ================================================================ settings

def test_the_new_voice_settings_are_validated():
    from core import user_settings

    assert user_settings._valid_value("voice_end_silence_seconds", 0.8)
    assert not user_settings._valid_value("voice_end_silence_seconds", 0.1)
    assert not user_settings._valid_value("voice_end_silence_seconds", True)
    assert user_settings._valid_value("voice_quality", "best")
    assert not user_settings._valid_value("voice_quality", "large-v3")


def test_best_quality_is_the_turbo_model_and_fast_stays_small():
    from core import voice_setup

    assert voice_setup.model_for_quality("best") == "large-v3-turbo"
    assert voice_setup.model_for_quality("fast") == "small"
    assert voice_setup.model_for_quality("anything") == "small"
    assert "large-v3-turbo" in voice_setup.ALLOWED_MODELS


def test_the_big_model_is_never_used_before_it_is_downloaded(monkeypatch):
    from core import main as nano_main, voice_setup

    voice_cfg = nano_main.CONFIG.setdefault("voice", {})
    stt = voice_cfg.setdefault("stt", {})
    before = (voice_cfg.get("voice_quality"), stt.get("model"), nano_main.voice.stt_provider.config.get("model"))
    try:
        voice_cfg["voice_quality"] = "best"
        monkeypatch.setattr(voice_setup, "model_cached", lambda name: False)
        assert nano_main._apply_voice_quality() is False
        assert stt.get("model") == nano_main._STT_FAST_MODEL
        monkeypatch.setattr(voice_setup, "model_cached", lambda name: True)
        assert nano_main._apply_voice_quality() is True
        assert stt["model"] == "large-v3-turbo"
        voice_cfg["voice_quality"] = "fast"
        assert nano_main._apply_voice_quality() is True
        assert stt["model"] == nano_main._STT_FAST_MODEL
    finally:
        voice_cfg["voice_quality"], stt["model"] = before[0], before[1]
        nano_main.voice.stt_provider.config["model"] = before[2]
