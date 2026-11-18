"""Ambiguous PC targets become a question with buttons, never a guess."""
from __future__ import annotations

import json

from core import clarification
from tests.test_multi_provider import build_brain, run

WINDOWS = [
    {"window_id": 101, "title": "relatório.docx - Word", "process": "WINWORD.EXE"},
    {"window_id": 202, "title": "relatório.pdf - Edge", "process": "msedge.exe"},
]
APPS = [
    {"name": "Spotify", "app_id": "start:Spotify", "source": "start_menu"},
    {"name": "Spotify Lite", "app_id": "uwp:SpotifyLite", "source": "uwp"},
]


def _ambiguous(candidates, message="Há 2 janelas que correspondem a 'relatório'. Qual delas?"):
    """Shaped exactly as ToolExecutor returns a handler's ambiguous failure."""
    output = {"ok": False, "status": "ambiguous", "error": "ambiguous",
              "message": message, "candidates": candidates}
    return {"success": False, "status": "verification_failed", "output": output,
            "error": "Verificação falhou: ambiguous", "metadata": {}}


def test_ambiguous_windows_become_options_with_the_exact_window_id():
    question = clarification.from_tool_result("pc_window_close", _ambiguous(WINDOWS))
    assert question["tool"] == "pc_window_close"
    assert "Qual delas" in question["question"]
    assert [o["label"] for o in question["options"]] == [
        "relatório.docx - Word — WINWORD.EXE", "relatório.pdf - Edge — msedge.exe"]
    assert "window_id 101" in question["options"][0]["reply"]
    assert "window_id 202" in question["options"][1]["reply"]


def test_ambiguous_apps_become_options_with_the_app_id():
    question = clarification.from_tool_result("pc_app_launch", _ambiguous(APPS, "Qual queres?"))
    assert [o["label"] for o in question["options"]] == ["Spotify", "Spotify Lite"]
    assert "app_id start:Spotify" in question["options"][0]["reply"]


def test_the_raw_handler_shape_is_understood_too():
    raw = _ambiguous(WINDOWS)["output"]
    assert clarification.from_tool_result("pc_window_focus", raw) is not None


def test_nothing_to_ask_for_success_not_found_or_a_single_candidate():
    assert clarification.from_tool_result("x", {"success": True, "output": {"ok": True}}) is None
    assert clarification.from_tool_result("x", {"ok": False, "status": "not_found", "candidates": []}) is None
    assert clarification.from_tool_result("x", _ambiguous(WINDOWS[:1])) is None
    assert clarification.from_tool_result("x", _ambiguous("not a list")) is None
    assert clarification.from_tool_result("x", None) is None
    assert clarification.from_tool_result("x", _ambiguous([{"junk": 1}, 3, None])) is None


def test_options_are_bounded_and_deduplicated():
    many = [{"window_id": i, "title": "x" * 500, "process": "p.exe"} for i in range(20)]
    many.append(dict(many[0]))
    question = clarification.from_tool_result("pc_window_close", _ambiguous(many))
    assert len(question["options"]) == clarification.MAX_OPTIONS
    assert question["more"] == 21 - clarification.MAX_OPTIONS
    assert all(len(o["label"]) <= clarification.MAX_LABEL for o in question["options"])


def test_a_bool_is_not_a_window_id():
    question = clarification.from_tool_result("x", _ambiguous([
        {"window_id": True, "title": "a"}, {"window_id": False, "title": "b"}]))
    assert question is None


def test_the_overlay_gets_labels_only():
    question = clarification.from_tool_result("pc_window_close", _ambiguous(WINDOWS))
    overlay = clarification.for_overlay(question)
    assert overlay["options"] == [o["label"] for o in question["options"]]
    assert "reply" not in json.dumps(overlay)
    assert clarification.for_overlay(None) is None


# ── the Brain records the question, per turn ──────────────────────────────

class _Executor:
    def __init__(self, results):
        self.results = list(results)
        self.registry = {}
        self.calls = []

    async def execute_tool_async(self, name, args=None, **_kw):
        self.calls.append((name, args))
        return self.results.pop(0)


def _call(name, arguments):
    return {"function": {"name": name, "arguments": json.dumps(arguments)}}


def test_the_brain_keeps_the_question_from_an_ambiguous_call(monkeypatch):
    executor = _Executor([_ambiguous(WINDOWS)])
    brain = build_brain(monkeypatch, executor=executor)
    brain.turn_clarification = None
    run(brain._run_tool(_call("pc_window_close", {"query": "relatório"})))
    assert brain.turn_clarification["tool"] == "pc_window_close"
    # Asking never executed anything else.
    assert len(executor.calls) == 1


def test_a_later_success_of_the_same_tool_clears_the_question(monkeypatch):
    executor = _Executor([_ambiguous(WINDOWS),
                          {"success": True, "status": "completed", "output": {"ok": True}, "metadata": {}}])
    brain = build_brain(monkeypatch, executor=executor)
    brain.turn_clarification = None
    run(brain._run_tool(_call("pc_window_close", {"query": "relatório"})))
    run(brain._run_tool(_call("pc_window_close", {"window_id": 101})))
    assert brain.turn_clarification is None


def test_main_forwards_the_question_to_the_ui_and_the_overlay():
    source = open("core/main.py", encoding="utf-8").read()
    assert 'result["clarification"] = question' in source
    assert "clarification.for_overlay(" in source
    shell = open("frontend/pages/index.tsx", encoding="utf-8").read()
    assert "clarification: final?.clarification" in shell
    assert "onChoose={(reply) => sendMessage(reply)}" in shell
