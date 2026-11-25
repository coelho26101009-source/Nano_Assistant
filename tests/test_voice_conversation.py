"""Continuous voice conversation: NANO listens again after it answers.

After a spoken answer NANO opens the microphone for a few seconds without a
hotkey or a wake phrase. Silence, "obrigado" / "that's all", an answer that
was not spoken, or MAX_FOLLOW_UPS end it. Barge-in (off by default) stops
NANO's voice when the person talks over it. No hardware: scripted fakes.
"""
from __future__ import annotations

import asyncio
import io
import struct
import time
import wave

from core import voice as voice_mod
from core.voice import VoiceRuntime, is_closing_phrase
from tests.test_voice_tts import _AlwaysSpeech, _AnsweringBrain, _TurnSession


class _ScriptedSTT:
    def __init__(self, lines):
        self.lines = list(lines)

    def transcribe(self, audio):
        text = self.lines.pop(0) if self.lines else ""
        return type("R", (), {"text": text, "ok": True})()


class _ScriptedMic:
    """Returns audio for each scripted line, then nothing (silence)."""

    def __init__(self, turns):
        self._available = True
        self.turns = turns
        self.captures = 0
        self.device_index = None
        self.gate = _AlwaysSpeech()

    def capture(self, duration_seconds):
        self.captures += 1
        return b"AUDIO" if self.captures <= self.turns else None


class _Engine:
    def __init__(self, lines, *, speak_seconds=0.0):
        self.enabled = True
        self.session = _TurnSession()
        self.stt_provider = _ScriptedSTT(lines)
        self.input_provider = _ScriptedMic(len(lines))
        self.spoken: list[str] = []
        self.speak_seconds = speak_seconds
        self.stopped = False
        engine = self

        class _TTS:
            def stop(self_inner):
                engine.stopped = True

        self.tts_provider = _TTS()

    async def speak(self, text):
        self.spoken.append(text)
        waited = 0.0
        while waited < self.speak_seconds and not self.stopped:
            await asyncio.sleep(0.05)
            waited += 0.05
        return True


def _runtime(engine, brain=None, **voice):
    cfg = {"tts_enabled": True, "voice_reply_tts": True, "wake_command_timeout_seconds": 5, **voice}
    return VoiceRuntime(engine, brain=brain or _AnsweringBrain(), config={"voice": cfg})


def _turn(runtime):
    return asyncio.run(runtime.run_voice_turn("hotkey", chime=False))


def test_it_keeps_listening_after_an_answer_until_silence():
    engine = _Engine(["que horas são", "e amanhã de manhã", "abre o spotify"])
    brain = _AnsweringBrain()
    result = _turn(_runtime(engine, brain))
    assert brain.calls == 3 and len(engine.spoken) == 3
    assert result["ok"] and len(result["follow_ups"]) == 3     # 2 answered + 1 silence
    assert result["follow_ups"][-1]["cancelled"] is True
    assert engine.input_provider.captures == 4


def test_thank_you_ends_the_conversation_without_asking_the_brain():
    for closing in ("é tudo", "that's all", "obrigado"):
        engine = _Engine(["que horas são", closing, "isto já não se ouve"])
        brain = _AnsweringBrain()
        result = _turn(_runtime(engine, brain))
        assert brain.calls == 1, closing
        # "obrigado" is also Whisper's favourite filler, so the speech filter
        # drops it first; either way the conversation ends quietly.
        assert result["follow_ups"][0]["error"] in {"conversation_closed", "no_usable_command"}
        assert result["follow_ups"][0]["cancelled"] is True


def test_switched_off_it_is_one_question_one_answer():
    engine = _Engine(["que horas são", "e amanhã"])
    brain = _AnsweringBrain()
    _turn(_runtime(engine, brain, voice_conversation_mode=False))
    assert brain.calls == 1 and engine.input_provider.captures == 1


def test_a_conversation_is_bounded():
    lines = [f"pergunta {i}" for i in range(20)]
    engine = _Engine(lines)
    brain = _AnsweringBrain()
    _turn(_runtime(engine, brain))
    assert brain.calls == VoiceRuntime.MAX_FOLLOW_UPS + 1


def test_closing_phrases_only_match_the_whole_utterance():
    for text in ("Obrigado!", "thanks", "That's all.", "é tudo", "Thank you, Nano"):
        assert is_closing_phrase(text), text
    for text in ("obrigado, e agora abre o Spotify", "stop the music", "thanks for the help with excel"):
        assert not is_closing_phrase(text), text


def _wav(level: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(struct.pack("<h", level) * 1600)
    return buffer.getvalue()


def test_barge_in_stops_nano_when_the_person_talks_over_it(monkeypatch):
    engine = _Engine(["conta-me uma história"], speak_seconds=3.0)
    loud = _wav(9000)

    def capture(duration_seconds):
        engine.input_provider.captures += 1
        if engine.input_provider.captures == 1:
            return b"AUDIO"                     # the question
        time.sleep(0.02)
        if engine.spoken:
            return loud                         # the person talks over NANO
        # A real microphone always hands back audio. Returning nothing here
        # ended the watch when it started before NANO did (a CI flake).
        return _wav(0)

    engine.input_provider.capture = capture
    monkeypatch.setattr(voice_mod.VoiceRuntime, "MAX_FOLLOW_UPS", 0)
    started = time.monotonic()
    result = _turn(_runtime(engine, voice_barge_in=True))
    assert engine.stopped is True and result["interrupted"] is True
    assert time.monotonic() - started < 2.5, "NANO kept talking after being interrupted"


def test_without_barge_in_the_microphone_is_left_alone_while_speaking(monkeypatch):
    engine = _Engine(["olá"], speak_seconds=0.2)
    monkeypatch.setattr(voice_mod.VoiceRuntime, "MAX_FOLLOW_UPS", 0)
    result = _turn(_runtime(engine))
    assert engine.input_provider.captures == 1 and result.get("interrupted") is False
