"""The NANO island's backend side: what reaches the desktop shell, and what may not.

The island itself (electron/lib/island.js and electron/overlay/) is tested by
the Electron suite. These tests hold the Python half: the events it is fed
(chat turns, tool steps, approvals, music, volume) and the one operation it
can call back (media_control).
"""
from __future__ import annotations

import pytest

from core import now_playing


@pytest.fixture
def main_module():
    import core.main as module
    return module


@pytest.fixture
def emitted(main_module, monkeypatch):
    events: list[tuple[str, dict]] = []

    class FakeBridge:
        running = True
        operations = ()

        def emit(self, event, payload):
            events.append((event, payload))

    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", FakeBridge())
    monkeypatch.setattr(main_module, "_notify_ui", lambda *_args: None)
    return events


# ── chat turns ─────────────────────────────────────────────────────────────

def test_a_chat_turn_tells_the_island_it_is_thinking_working_and_done(main_module, emitted):
    main_module._emit_stream_start("m1", "olá")
    main_module._emit_stream_status("m1", "⚙️ web_search, set_reminder...")
    main_module._emit_stream_end("m1", {"text": "x"})
    assert emitted == [
        ("chat_activity", {"phase": "thinking"}),
        ("chat_activity", {"phase": "working", "tools": ["web_search", "set_reminder"]}),
        ("chat_activity", {"phase": "done"}),
    ]


def test_a_failed_turn_says_error_and_nothing_else(main_module, emitted):
    main_module._emit_stream_error("m1", "provider_failed", "groq.APIStatusError 500 secret detail")
    assert emitted == [("chat_activity", {"phase": "error"})], "no provider detail reaches the island"


@pytest.mark.parametrize("status", [
    "🧠 a usar llama3 local...",
    "\\🔄 O Groq falhou; a tentar outro...",
    "⚙️ rm -rf / && echo; ...",
    "",
])
def test_a_status_that_is_not_a_tool_step_shows_no_tool(main_module, status):
    assert main_module._island_tools(status) == []


def test_tool_names_are_bounded(main_module):
    many = ", ".join(f"tool_{i}" for i in range(10))
    assert main_module._island_tools(f"⚙️ {many}...") == ["tool_0", "tool_1", "tool_2", "tool_3"]


# ── approvals ──────────────────────────────────────────────────────────────

def test_an_approval_is_announced_but_never_counted_as_shown(main_module, emitted):
    meta = {"tool": "pc_window_close", "action": "FECHAR JANELA", "target": "Discord — #geral",
            "args": {"password": "hunter2", "text": "segredo"}}
    shown = main_module._island_confirm_listener("request", "req1", "Fechar?", meta)
    assert shown is False, "the island cannot answer, so it must not count as a place the request was shown"
    assert emitted == [("approval", {"phase": "request", "id": "req1",
                                     "action": "FECHAR JANELA", "target": "Discord — #geral"})]
    assert "hunter2" not in repr(emitted) and "segredo" not in repr(emitted), "arguments never reach the island"

    main_module._island_confirm_listener("resolved", "req1", None, None)
    assert emitted[-1] == ("approval", {"phase": "resolved", "id": "req1"})


def test_the_island_listener_is_registered_with_the_guardrails(main_module):
    assert main_module._island_confirm_listener in main_module.guardrails._listeners


def test_without_a_desktop_shell_the_listener_stays_quiet(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", None)
    assert main_module._island_confirm_listener("request", "r", "m", {}) is False


# ── the control operation ──────────────────────────────────────────────────

def test_media_control_only_accepts_the_three_transport_commands(main_module):
    for action in ("shutdown", "volume_up", "../../x", "", None, "play_pause; rm"):
        assert main_module._ctl_media_control({"action": action}) == {"ok": False, "error": "invalid_action"}


def test_media_control_falls_back_to_the_media_key(monkeypatch):
    pressed = []
    from core.pc_control import keyboard

    monkeypatch.setattr(now_playing, "winrt_available", lambda: False)
    monkeypatch.setattr(keyboard, "press_media", lambda name: pressed.append(name) or {"sent": True})
    watcher = now_playing.NowPlaying(emit=lambda *_: None)
    assert watcher.control("next") == {"ok": True}
    assert pressed == ["next"]


# ── what is playing ────────────────────────────────────────────────────────

def _song(**changes):
    song = {"key": "Spotify.exe|Entropy|Beach Bunny", "app": "Spotify", "title": "Entropy",
            "artist": "Beach Bunny", "status": "playing", "position": 10.0, "duration": 180.0,
            "position_at": 1_000_000.0, "can_previous": True, "can_next": True, "can_toggle": True}
    song.update(changes)
    return song


class Clock:
    def __init__(self, seconds: float):
        self.now = seconds

    def __call__(self) -> float:
        return self.now


def test_a_new_song_is_sent_once_with_its_cover():
    events, readings = [], [dict(_song(), art="data:image/png;base64,AAAA"), _song()]
    clock = Clock(1000.0)
    watcher = now_playing.NowPlaying(emit=lambda e, p: events.append((e, p)),
                                     media_reader=lambda _key: readings.pop(0), clock=clock)
    first = watcher.media_tick()
    assert first["changed"] == "track" and first["art"] == "data:image/png;base64,AAAA"
    clock.now += 1
    assert watcher.media_tick() is None, "the same song a second later is not news"
    assert len(events) == 1


def test_the_cover_is_only_asked_for_a_new_song():
    asked = []

    def reader(key):
        asked.append(key)
        return dict(_song(), art="") if key is None else _song()

    watcher = now_playing.NowPlaying(emit=lambda *_: None, media_reader=reader, clock=Clock(1000.0))
    watcher.media_tick()
    watcher.media_tick()
    assert asked == [None, "Spotify.exe|Entropy|Beach Bunny"]


def test_pause_skip_and_seek_are_news_but_playing_on_is_not():
    clock = Clock(1000.0)
    queue = [
        _song(),
        _song(position=11.0, position_at=1_001_000.0),           # one second on: not news
        _song(status="paused", position=11.0, position_at=1_001_000.0),
        _song(status="playing", position=11.0, position_at=1_002_000.0),
        _song(position=90.0, position_at=1_002_500.0),            # sought to 1:30
        _song(key="Spotify.exe|Next|Band", title="Next", artist="Band", position=0.0),
    ]
    events = []
    watcher = now_playing.NowPlaying(emit=lambda e, p: events.append(p.get("changed")),
                                     media_reader=lambda _k: queue.pop(0), clock=clock)
    for step in range(6):
        clock.now = 1000.0 + step * 0.5 + (1 if step >= 1 else 0)
        watcher.media_tick()
    assert events == ["track", "status", "status", "seek", "track"]


def test_nothing_playing_any_more_is_said_once():
    queue = [_song(), None, None, _song(status="stopped")]
    events = []
    watcher = now_playing.NowPlaying(emit=lambda e, p: events.append(p), media_reader=lambda _k: queue.pop(0),
                                     clock=Clock(1000.0))
    for _ in range(4):
        watcher.media_tick()
    assert [e.get("status") for e in events] == ["playing", "none"]


def test_a_failing_reader_is_not_an_error():
    def broken(_key):
        raise OSError("SMTC went away")

    watcher = now_playing.NowPlaying(emit=lambda *_: None, media_reader=broken)
    assert watcher.media_tick() is None


def test_volume_changes_are_news_but_the_starting_level_is_not():
    events = []
    watcher = now_playing.NowPlaying(emit=lambda e, p: events.append((e, p)))
    assert watcher.volume_tick({"level": 40, "muted": False}) is None
    assert watcher.volume_tick({"level": 40, "muted": False}) is None
    watcher.volume_tick({"level": 50, "muted": False})
    watcher.volume_tick({"level": 50, "muted": True})
    assert events == [("volume", {"level": 50, "muted": False}), ("volume", {"level": 50, "muted": True})]


@pytest.mark.parametrize("data, content_type, expected", [
    (b"\x89PNG\r\n\x1a\nxxxx", None, "data:image/png;base64,"),
    (b"\xff\xd8\xffxxxx", "", "data:image/jpeg;base64,"),
    (b"anything", "image/webp", "data:image/webp;base64,"),
    (b"<svg onload=alert(1)>", "image/svg+xml", ""),
    (b"x" * (now_playing.MAX_ART_BYTES + 1), "image/png", ""),
    (None, "image/png", ""),
])
def test_only_plain_bounded_images_become_a_cover(data, content_type, expected):
    url = now_playing.art_data_url(data, content_type)
    assert url.startswith(expected) if expected else url == ""


@pytest.mark.parametrize("app_id, name", [
    ("Spotify.exe", "Spotify"),
    ("chrome", "chrome"),
    ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", "ZuneMusic"),
    ("", ""),
])
def test_app_names_are_readable(app_id, name):
    assert now_playing.app_name(app_id) == name


def test_off_windows_there_is_no_music_and_no_error():
    assert now_playing.read_media() is None or now_playing.winrt_available()
