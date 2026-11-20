"""Watching the screen for a while: "avisa-me quando o download acabar".

Pure logic with a fake clock and fake readers; the plugin and policy wiring
are checked through the real registry. No screen is read.
"""
from __future__ import annotations

import pytest

from core import screen_watch
from core.permission_manager import PermissionManager


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def setup():
    state = {"text": "", "titles": [], "notices": []}
    clock = _Clock()
    watcher = screen_watch.ScreenWatcher(read_text=lambda: state["text"],
                                         list_titles=lambda: list(state["titles"]),
                                         emit=state["notices"].append, clock=clock)
    watcher._ensure_thread = lambda: None          # tests drive tick() themselves
    return watcher, state, clock


def test_text_that_appears_is_announced_once(setup):
    watcher, state, clock = setup
    assert watcher.start("Avisa-me se aparecer um erro", appears=["Erro"])["ok"]
    assert watcher.tick() == []
    state["text"] = "Build failed: ERRO 42"
    clock.advance(11)
    notices = watcher.tick()
    assert len(notices) == 1 and "erro" in notices[0]["text"] and notices[0]["kind"] == "watch"
    assert watcher.status() == []                  # one notice, then the watch ends
    clock.advance(11)
    assert watcher.tick() == []


def test_a_download_that_finishes_is_announced_after_two_checks(setup):
    watcher, state, clock = setup
    state["text"] = "A transferir 45%"
    watcher.start("Avisa-me quando o download acabar", disappears=["%"])
    watcher.tick()
    state["text"] = "Transferência concluída"
    clock.advance(11)
    assert watcher.tick() == []                    # one miss is not enough
    clock.advance(11)
    assert len(watcher.tick()) == 1


def test_text_never_seen_does_not_count_as_gone(setup):
    watcher, state, clock = setup
    watcher.start("x", disappears=["%"])
    for _ in range(5):
        clock.advance(11)
        assert watcher.tick() == []


def test_a_window_closing_is_announced(setup):
    watcher, state, clock = setup
    state["titles"] = ["A transferir - Steam", "Explorador"]
    watcher.start("Avisa quando o Steam acabar", window_closes=["a transferir"])
    watcher.tick()
    state["titles"] = ["Explorador"]
    clock.advance(5)
    watcher.tick()
    clock.advance(5)
    notices = watcher.tick()
    assert notices and "fechou" in notices[0]["text"]


def test_a_window_opening_is_announced(setup):
    watcher, state, clock = setup
    watcher.start("Diz-me quando abrir o Teams", window_appears=["teams"])
    state["titles"] = ["Microsoft Teams"]
    clock.advance(5)
    assert len(watcher.tick()) == 1


def test_it_gives_up_when_time_runs_out(setup):
    watcher, state, clock = setup
    watcher.start("x", appears=["nunca"], minutes=1)
    clock.advance(61)
    notices = watcher.tick()
    assert notices and notices[0]["title"] and watcher.status() == []


def test_limits(setup):
    watcher, state, clock = setup
    assert watcher.start("x")["error"] == "no_condition"
    assert watcher.start("x", appears=["a"], text_allowed=False)["error"] == "screen_text_disabled"
    assert watcher.start("x", window_appears=["a"], text_allowed=False)["ok"]
    big = watcher.start("x", window_appears=["b"], minutes=9999)
    assert big["watch"]["minutesLeft"] == screen_watch.MAX_MINUTES
    watcher.start("x", window_appears=["c"])
    assert watcher.start("x", window_appears=["d"])["error"] == "too_many_watches"
    assert len(watcher.stop()["stopped"]) == screen_watch.MAX_WATCHES


def test_a_failing_reader_skips_the_check(setup):
    watcher, state, clock = setup

    def broken():
        raise RuntimeError("OCR down")

    watcher.configure(read_text=broken)
    watcher.start("x", appears=["erro"])
    clock.advance(11)
    assert watcher.tick() == []


def test_starting_a_watch_asks_and_stopping_does_not():
    manager = PermissionManager()
    start = manager.resolve_tool_capability("pc_screen_watch", {"goal": "x", "appears": ["erro"]})
    assert start == "pc.screen.watch"
    evaluation = manager.policy_engine.evaluate(start, arguments={})
    assert evaluation.decision.value == "APPROVAL_REQUIRED" and evaluation.requires_confirmation
    stop = manager.resolve_tool_capability("pc_screen_watch_stop", {})
    assert manager.policy_engine.evaluate(stop, arguments={}).decision.value == "AUTONOMOUS"


def test_the_plugin_refuses_screen_text_while_the_switch_is_off(monkeypatch):
    from plugins import pc_control

    monkeypatch.setattr(pc_control, "_screen_text_enabled", lambda: False)
    monkeypatch.setattr(screen_watch.WATCHER, "_ensure_thread", lambda: None)
    try:
        refused = pc_control.pc_screen_watch({"goal": "erro", "appears": ["erro"]})
        assert refused["ok"] is False and refused["status"] == "screen_text_disabled"
        started = pc_control.pc_screen_watch({"goal": "Teams", "window_appears": ["teams"]})
        assert started["ok"] is True and started["status"] == "watching"
        listed = pc_control.pc_screen_watch_stop({"list_only": True})
        assert listed["ok"] and len(listed["watches"]) == 1
        assert pc_control.pc_screen_watch_stop({})["ok"]
    finally:
        screen_watch.WATCHER.stop()


def test_asking_to_be_told_when_something_happens_offers_the_watch_tool():
    from core import model_selection
    from plugins import pc_control

    tools = pc_control.get_tools()
    for message in ("avisa-me quando o download acabar", "let me know when the build finishes",
                    "fica de olho no ecrã e diz-me se aparecer um erro"):
        selected = {t["function"]["name"] for t in model_selection.select_tools(message, tools)}
        assert "pc_screen_watch" in selected, message


def test_text_already_on_screen_does_not_count_as_appearing(setup):
    # NANO's own chat shows the request, so the word is usually on screen when
    # the watch starts. Only a NEW occurrence counts.
    watcher, state, clock = setup
    state["text"] = "Tu: avisa-me se aparecer erro"
    watcher.start("Avisa-me se aparecer um erro", appears=["erro"])
    assert watcher.tick() == []
    clock.advance(11)
    assert watcher.tick() == []
    state["text"] = "Tu: avisa-me se aparecer erro | Erro: disco cheio"
    clock.advance(11)
    assert len(watcher.tick()) == 1


def test_one_ocr_miss_does_not_make_the_same_text_appear_again(setup):
    watcher, state, clock = setup
    state["text"] = "erro"
    watcher.start("x", appears=["erro"])
    watcher.tick()
    state["text"] = ""                  # one missed reading
    clock.advance(11)
    assert watcher.tick() == []
    state["text"] = "erro"              # the same text, read again
    clock.advance(11)
    assert watcher.tick() == []


def test_a_window_already_open_does_not_count_as_opening(setup):
    watcher, state, clock = setup
    state["titles"] = ["Microsoft Teams"]
    watcher.start("Diz-me quando abrir outra janela do Teams", window_appears=["teams"])
    clock.advance(5)
    assert watcher.tick() == []
    state["titles"] = ["Microsoft Teams", "Reunião | Microsoft Teams"]
    clock.advance(5)
    assert len(watcher.tick()) == 1


def test_a_watch_added_after_the_thread_exits_gets_a_new_thread(monkeypatch):
    # The loop clears its slot under the lock when it leaves, so the next
    # start() can never find a "live" thread that is about to exit.
    monkeypatch.setattr(screen_watch, "TICK_SECONDS", 0.01)
    watcher = screen_watch.ScreenWatcher(read_text=lambda: "", list_titles=lambda: [])
    watcher.start("x", window_appears=["a"])
    first = watcher._thread
    assert first is not None and first.is_alive()
    watcher.stop()
    first.join(2)
    assert not first.is_alive() and watcher._thread is None
    watcher.start("y", window_appears=["b"])
    second = watcher._thread
    assert second is not None and second is not first and second.is_alive()
    watcher.shutdown()
    second.join(2)
    assert not second.is_alive()
