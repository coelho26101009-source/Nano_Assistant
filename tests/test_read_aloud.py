""""Ler em voz alta" under an answer: core.main.read_aloud.

Typed answers are never spoken on their own (typed_chat_tts is off by default),
so this is the explicit, per-answer request. It must only speak while spoken
replies are on and the voice is installed, never talk over a voice turn, read
much further than a spoken reply, and tell the window when it is over exactly
once -- a reading replaced by another, or stopped for a voice turn, stays quiet.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.voice import VoiceSessionState


class _Session:
    def __init__(self) -> None:
        self.state = VoiceSessionState.IDLE

    def cancel(self) -> None:
        self.state = VoiceSessionState.IDLE


class _TTS:
    def __init__(self, online: bool) -> None:
        self.online = online


class _Voice:
    """Speaks until released, like a real utterance that takes a while."""

    def __init__(self, *, enabled: bool = True, online: bool = True) -> None:
        self.enabled = enabled
        self.tts_provider = _TTS(online)
        self.session = _Session()
        self.spoken: list[tuple[str, int | None]] = []
        self.stopped = 0
        self._release = threading.Event()

    async def speak(self, text: str, limit: int | None = None) -> bool:
        self.session.state = VoiceSessionState.SPEAKING
        self.spoken.append((text, limit))
        await asyncio.get_running_loop().run_in_executor(None, self._release.wait, 5)
        return True

    def finish(self) -> None:
        self._release.set()

    def stop_playback(self) -> None:
        self.stopped += 1
        self.session.cancel()
        self._release.set()


class _Runtime:
    def __init__(self, active: bool = False) -> None:
        self.active = active
        self.turns: list[str] = []

    def turn_status(self) -> dict:
        return {"active": self.active, "source": "hotkey" if self.active else None}

    async def run_voice_turn(self, source: str) -> dict:
        self.turns.append(source)
        return {"ok": True}


@pytest.fixture()
def wired(monkeypatch):
    import core.main as main

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    notes: list[tuple[str, tuple]] = []
    voice = _Voice()
    runtime = _Runtime()
    monkeypatch.setattr(main, "_get_or_create_loop", lambda: loop)
    monkeypatch.setattr(main, "_notify_ui", lambda name, *args: notes.append((name, args)))
    monkeypatch.setattr(main, "_desktop_emit", lambda *a, **k: None)
    monkeypatch.setattr(main, "voice", voice)
    monkeypatch.setattr(main, "voice_runtime", runtime)
    monkeypatch.setattr(main, "_reading_id", None)
    monkeypatch.setitem(main.CONFIG, "voice", {**main.CONFIG.get("voice", {}), "tts_enabled": True})
    try:
        yield main, voice, runtime, notes
    finally:
        voice.finish()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def _ends(notes) -> list[str]:
    return [args[0] for name, args in notes if name == "on_read_aloud_end"]


def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out")
        time.sleep(0.01)


def test_an_answer_is_read_without_markdown_and_much_further_than_a_spoken_reply(wired):
    main, voice, _runtime, notes = wired
    answer = "**Done.** Here is the list:\n\n- one\n- two\n\n" + ("A long sentence. " * 80)
    result = main.read_aloud("m1", answer)

    assert result == {"ok": True, "id": "m1"}
    _wait_for(lambda: voice.spoken)
    text, limit = voice.spoken[0]
    assert "**" not in text and text.startswith("Done.")
    assert limit == main.READ_ALOUD_LIMIT > 700
    assert len(text) > 700, "a requested reading is not cut like a short spoken reply"

    voice.finish()
    _wait_for(lambda: _ends(notes))
    assert _ends(notes) == ["m1"]
    assert voice.session.state == VoiceSessionState.IDLE
    assert main._reading_id is None


@pytest.mark.parametrize("enabled,tts_enabled,online,error", [
    (False, True, True, "voice_off"),
    (True, False, True, "voice_off"),
    (True, True, False, "tts_unavailable"),
])
def test_nothing_is_read_unless_spoken_replies_are_on_and_installed(wired, monkeypatch,
                                                                  enabled, tts_enabled, online, error):
    main, voice, _runtime, notes = wired
    voice.enabled = enabled
    voice.tts_provider.online = online
    monkeypatch.setitem(main.CONFIG, "voice", {**main.CONFIG["voice"], "tts_enabled": tts_enabled})

    result = main.read_aloud("m1", "Hello there.")
    assert result["ok"] is False and result["error"] == error and result["detail"]
    assert voice.spoken == [] and notes == []


def test_nano_never_talks_over_a_voice_conversation(wired):
    main, voice, runtime, notes = wired
    runtime.active = True
    result = main.read_aloud("m1", "Hello there.")
    assert result["error"] == "voice_busy"
    assert voice.spoken == [] and voice.stopped == 0 and notes == []


def test_an_answer_that_is_only_code_has_nothing_to_read(wired):
    main, voice, _runtime, _notes = wired
    result = main.read_aloud("m1", "```python\nprint('hi')\n```")
    assert result["error"] == "nothing_to_read"
    assert voice.spoken == []


def test_a_new_reading_replaces_the_one_in_progress_and_only_it_announces_its_end(wired):
    main, voice, _runtime, notes = wired
    assert main.read_aloud("m1", "First answer.")["ok"]
    _wait_for(lambda: len(voice.spoken) == 1)

    assert main.read_aloud("m2", "Second answer.")["ok"]
    assert voice.stopped == 1, "the first reading is silenced before the second starts"
    _wait_for(lambda: len(voice.spoken) == 2)
    voice.finish()
    _wait_for(lambda: _ends(notes))
    time.sleep(0.05)
    assert _ends(notes) == ["m2"]


def test_a_voice_turn_stops_the_reading_first_and_the_end_is_announced_once(wired):
    main, voice, runtime, notes = wired
    assert main.read_aloud("m1", "An answer being read.")["ok"]
    _wait_for(lambda: voice.spoken)

    ack = main.start_voice_turn("hotkey")
    assert ack["accepted"] is True
    assert voice.stopped == 1, "the microphone would hear NANO's own voice"
    _wait_for(lambda: runtime.turns == ["hotkey"])
    time.sleep(0.05)
    assert _ends(notes) == ["m1"]
    assert main._reading_id is None
