"""Chained tasks: the step runner, routines made by voice, plans NANO runs,
"tenta outra vez", and asking only for what is risky.

core/task_runner.py, core/brain_tasks.py, plugins/routines.py and the
``actions`` of core/protocols.py.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from core import protocols, task_runner
from core.brain import Brain
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine
from core.permission_manager import PermissionManager
from core.trust import TrustLevel
from plugins import pc_control, routines, task_plan


def run(coro):
    return asyncio.run(coro)


async def _no_sleep(_seconds):
    return None


def ok(**output):
    return {"success": True, "status": "completed", "output": {"ok": True, **output}}


def failed(code, message=""):
    return {"success": False, "status": "verification_failed",
            "error": f"Verificação falhou: {code}",
            "output": {"ok": False, "status": code, "error": code, "message": message}}


class Script:
    """A fake gate: each tool answers from a list of results, in order."""

    def __init__(self, answers=None, default=None):
        self.answers = {name: list(values) for name, values in (answers or {}).items()}
        self.default = default or ok()
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, name, args):
        self.calls.append((name, dict(args)))
        queue = self.answers.get(name)
        if queue:
            answer = queue.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        return self.default


STUDY_STEPS = [
    {"tool": "pc_app_launch", "args": {"name": "Notion"}, "label": "Abrir Notion"},
    {"tool": "pc_app_launch", "args": {"name": "Chrome"}, "label": "Abrir Chrome"},
    {"tool": "pc_window_snap", "args": {"query": "Chrome", "position": "left"}, "label": "Chrome à esquerda"},
    {"tool": "pc_volume_set", "args": {"level": 30}, "label": "Volume a 30%"},
]


# ══ The step runner ═════════════════════════════════════════════════════

def test_every_step_runs_in_order_and_is_reported():
    gate = Script({"pc_volume_set": [ok(level=30)]})
    statuses: list[str] = []
    result = run(task_runner.run_steps(STUDY_STEPS, gate, status=statuses.append, sleep=_no_sleep))
    assert [name for name, _ in gate.calls] == [s["tool"] for s in STUDY_STEPS]
    assert result["ok"] and result["done"] == 4 and result["total"] == 4
    assert [o["status"] for o in result["steps"]] == ["ok"] * 4
    assert statuses[0] == "⚙️ 1/4 · Abrir Notion" and len(statuses) == 4
    assert task_runner.short_report(result) == ""


def test_a_hiccup_is_retried_once_and_then_counts_as_done():
    gate = Script({"pc_volume_set": [failed("audio_failed"), ok(level=30)]})
    result = run(task_runner.run_steps([STUDY_STEPS[3]], gate, sleep=_no_sleep))
    assert result["ok"] and result["steps"][0]["attempts"] == 2


def test_a_refusal_or_an_ambiguous_target_is_never_retried():
    gate = Script({"pc_app_launch": [{"success": False, "status": "permission_denied", "error": "Recusado"}],
                   "pc_window_snap": [failed("ambiguous", "Há duas janelas Chrome.")]})
    steps = [STUDY_STEPS[0], {"tool": "pc_window_snap", "args": {"query": "Chrome", "position": "left"},
                              "label": "Chrome à esquerda"}]
    result = run(task_runner.run_steps(steps, gate, sleep=_no_sleep))
    assert [o["status"] for o in result["steps"]] == ["denied", "failed"]
    assert [o["attempts"] for o in result["steps"]] == [1, 1]
    assert len(gate.calls) == 2


def test_a_toggle_or_a_site_is_never_retried_even_on_a_hiccup():
    gate = Script({"pc_media_control": [failed("failed")], "pc_web_open_url": [failed("timeout")]})
    steps = [{"tool": "pc_media_control", "args": {"action": "play_pause"}, "label": "Música"},
             {"tool": "pc_web_open_url", "args": {"url": "https://example.com"}, "label": "Site"}]
    result = run(task_runner.run_steps(steps, gate, sleep=_no_sleep))
    assert len(gate.calls) == 2 and not result["ok"]


def test_a_tool_the_executor_marks_unsafe_to_retry_is_not_retried():
    gate = Script({"pc_volume_set": [failed("audio_failed"), ok(level=30)]})
    result = run(task_runner.run_steps([STUDY_STEPS[3]], gate, sleep=_no_sleep,
                                       retry_allowed=lambda tool: tool != "pc_volume_set"))
    assert not result["ok"] and len(gate.calls) == 1


def test_a_window_that_is_not_there_yet_after_a_launch_is_waited_for():
    gate = Script({"pc_window_snap": [failed("not_found"), failed("not_found"), ok()]})
    result = run(task_runner.run_steps(STUDY_STEPS[1:3], gate, sleep=_no_sleep))
    assert result["ok"] and result["steps"][1]["attempts"] == 3


def test_a_window_step_without_a_launch_before_it_is_not_retried_on_not_found():
    gate = Script({"pc_window_snap": [failed("not_found"), ok()]})
    result = run(task_runner.run_steps([STUDY_STEPS[2]], gate, sleep=_no_sleep))
    assert not result["ok"] and len(gate.calls) == 1


def test_when_an_app_fails_to_open_the_steps_on_its_window_are_skipped_not_failed():
    gate = Script({"pc_app_launch": [ok(), failed("not_found", "Não encontrei o Chrome.")]})
    result = run(task_runner.run_steps(STUDY_STEPS, gate, sleep=_no_sleep))
    by_label = {o["label"]: o for o in result["steps"]}
    assert by_label["Abrir Chrome"]["status"] == "failed"
    assert by_label["Chrome à esquerda"]["status"] == "skipped"
    assert by_label["Chrome à esquerda"]["code"] == "dependency_failed"
    assert by_label["Volume a 30%"]["status"] == "ok"       # independent: still done
    assert ("pc_window_snap", {"query": "Chrome", "position": "left"}) not in gate.calls
    report = task_runner.short_report(result)
    assert "Abrir Chrome" in report and "Chrome à esquerda" in report


def test_a_launch_waits_for_its_window_when_a_later_step_needs_it(monkeypatch):
    listings = iter([[], [{"process": "notepad.exe", "title": "x"}],
                     [{"process": "chrome.exe", "title": "Nova aba - Google Chrome"}]])

    async def probe(name, _args):
        assert name == "pc_window_list"
        return ok(windows=next(listings))

    gate = Script({"pc_app_launch": [ok(status="launched", app={"name": "Google Chrome",
                                                               "executable": r"C:\\Chrome\\chrome.exe"})]})
    result = run(task_runner.run_steps(STUDY_STEPS[1:3], gate, probe=probe, sleep=_no_sleep))
    assert result["steps"][0]["verified"] is True
    assert result["ok"]


def test_a_launch_whose_window_never_shows_is_said_plainly(monkeypatch):
    monkeypatch.setattr(task_runner, "LAUNCH_WAIT_SECONDS", 0.0)

    async def probe(_name, _args):
        return ok(windows=[])

    gate = Script()
    result = run(task_runner.run_steps(STUDY_STEPS[1:3], gate, probe=probe, sleep=_no_sleep))
    assert result["steps"][0]["verified"] is False
    assert "janela" in result["steps"][0]["message"]


def test_a_launch_with_nothing_after_it_does_not_wait():
    async def probe(_name, _args):
        raise AssertionError("no window wait was needed")

    result = run(task_runner.run_steps([STUDY_STEPS[0]], Script(), probe=probe, sleep=_no_sleep))
    assert result["ok"] and result["steps"][0]["verified"] is None


def test_a_volume_that_did_not_end_where_asked_is_not_done():
    gate = Script({"pc_volume_set": [ok(level=80), ok(level=80)]})
    result = run(task_runner.run_steps([STUDY_STEPS[3]], gate, sleep=_no_sleep))
    assert result["steps"][0]["status"] == "failed"
    assert "80%" in result["steps"][0]["message"]


def test_the_emergency_stop_stops_the_remaining_steps():
    stop = {"now": False}

    async def gate(name, args):
        stop["now"] = True       # pressed while the first step ran
        return ok()

    result = run(task_runner.run_steps(STUDY_STEPS, gate, should_stop=lambda: stop["now"], sleep=_no_sleep))
    assert [o["status"] for o in result["steps"]] == ["ok", "cancelled", "cancelled", "cancelled"]
    assert result["cancelled"] == 3 and not result["ok"]


def test_a_step_that_raises_is_a_failure_not_a_crash():
    gate = Script({"pc_app_launch": [RuntimeError("boom"), RuntimeError("boom")]})
    result = run(task_runner.run_steps([STUDY_STEPS[0]], gate, sleep=_no_sleep))
    assert result["steps"][0]["status"] == "failed"


def test_failure_codes_come_from_the_most_specific_place():
    assert task_runner.failure_code(failed("not_found")) == "not_found"
    assert task_runner.failure_code({"ok": False, "cancelled": True}) == "cancelled"
    assert task_runner.failure_code({"success": False, "status": "permission_denied"}) == "permission_denied"
    assert task_runner.failure_code({"success": False, "status": "failed", "error": "tool_exception"}) == "tool_exception"
    assert task_runner.succeeded(ok()) and not task_runner.succeeded(failed("x"))
    assert not task_runner.succeeded({"success": True, "output": {"ok": False}})


# ══ Routines: steps a protocol may hold ═════════════════════════════════

def test_a_routine_step_is_checked_and_labelled():
    step = protocols.validate_action({"tool": "pc_window_snap", "args": {"query": "Chrome", "position": "left",
                                                                         "window_id": 99}})
    assert step == {"tool": "pc_window_snap", "args": {"query": "Chrome", "position": "left"},
                    "label": "Pôr Chrome à esquerda"}
    assert protocols.validate_action({"tool": "pc_window_batch_close", "args": {"app": "discord"}})["asks"]
    assert protocols.validate_action({"tool": "set_reminder",
                                      "args": {"text": "pausa", "when": "50 minutos"}})["args"]["when"] == "50 minutos"
    assert protocols.validate_action({"tool": "pc_volume_set", "args": {"level": "30"}})["args"] == {"level": 30}


@pytest.mark.parametrize("raw", [
    {"tool": "pc_input_type_text", "args": {"text": "olá"}},
    {"tool": "pc_power_shutdown", "args": {}},
    {"tool": "pc_file_recycle", "args": {"path": "C:/x.txt"}},
    {"tool": "pc_app_launch", "args": {"name": "C:\\Windows\\System32\\cmd.exe"}},
    {"tool": "pc_app_launch", "args": {"name": "powershell"}},
    {"tool": "pc_web_open_url", "args": {"url": "javascript:alert(1)"}},
    {"tool": "pc_window_snap", "args": {"window_id": 5, "position": "left"}},
    {"tool": "pc_window_snap", "args": {"query": "Chrome", "position": "middle"}},
    {"tool": "pc_volume_set", "args": {"level": 140}},
    {"tool": "pc_settings_open", "args": {"section": "registry"}},
    {"tool": "pc_app_launch", "args": "Spotify"},
])
def test_a_routine_cannot_hold_an_unsafe_or_malformed_step(raw):
    with pytest.raises(protocols.ProtocolError):
        protocols.validate_action(raw)


def test_a_routine_has_a_bounded_number_of_steps():
    many = [{"tool": "pc_volume_set", "args": {"level": 10}}] * (protocols.MAX_ACTIONS + 1)
    with pytest.raises(protocols.ProtocolError):
        protocols.validate_actions(many)


def test_actions_run_after_the_window_fields_and_lock_comes_last(tmp_path):
    store = protocols.ProtocolStore(tmp_path / "protocols.json")
    saved = store.save({"name": "Estudo", "open_apps": ["Notion"], "lock": True,
                        "actions": [{"tool": "pc_window_snap", "args": {"query": "Notion", "position": "right"}}]})
    tools = [s["tool"] for s in protocols.compile_protocol(saved)["steps"]]
    assert tools == ["pc_app_launch", "pc_window_snap", "pc_session_lock"]


def test_an_old_protocol_file_without_actions_still_loads(tmp_path):
    path = tmp_path / "protocols.json"
    path.write_text(json.dumps({"version": 1, "protocols": [
        {"id": "boa-noite-abc123", "name": "Boa noite", "phrases": ["boa noite"], "close_apps": [],
         "open_apps": [], "open_urls": [], "volume": 10, "media": "stop", "settings_section": "",
         "lock": True, "message": "Dorme bem."}]}), encoding="utf-8")
    items = protocols.ProtocolStore(path).list()
    assert items and items[0]["actions"] == []


@pytest.mark.parametrize("said", [
    "prepara o meu setup de estudo", "Ei Nano, prepara-me o setup de estudo!",
    "setup de estudo", "ativa a rotina setup de estudo", "prepare my study setup",
])
def test_the_natural_ways_of_asking_start_the_routine(tmp_path, said):
    store = protocols.ProtocolStore(tmp_path / "protocols.json")
    store.save({"name": "Setup de estudo", "phrases": ["study setup"], "actions": STUDY_STEPS})
    assert store.match(said) is not None, said
    assert store.match("prepara o jantar") is None


# ══ Routines by voice (plugins/routines.py) ═════════════════════════════

@pytest.fixture
def store(tmp_path, monkeypatch):
    fresh = protocols.ProtocolStore(tmp_path / "protocols.json")
    monkeypatch.setattr(protocols, "STORE", fresh)
    return fresh


def test_a_routine_is_saved_listed_found_and_deleted_by_name(store):
    saved = routines.routine_save({"name": "Setup de estudo", "phrases": ["vou estudar"], "steps": STUDY_STEPS})
    assert saved["ok"] and saved["routine"]["steps"][0] == "Abrir Notion"
    assert "setup de estudo" in saved["message"].casefold()
    assert routines.routine_list({})["count"] == 1
    found = routines.routine_run({"name": "estudo"})        # loose, but only one fits
    assert found["ok"] and [s["tool"] for s in found["steps"]] == [s["tool"] for s in STUDY_STEPS]
    assert routines.routine_run({"name": "vou estudar"})["ok"]
    assert routines.routine_delete({"name": "Setup de estudo"})["ok"]
    assert routines.routine_list({})["count"] == 0


def test_saving_the_same_name_again_updates_it(store):
    first = routines.routine_save({"name": "Modo jogo", "steps": [STUDY_STEPS[0]]})
    second = routines.routine_save({"name": "modo jogo", "steps": STUDY_STEPS})
    assert second["status"] == "updated" and second["routine"]["id"] == first["routine"]["id"]
    assert len(store.list()) == 1 and len(store.list()[0]["actions"]) == 4


def test_a_bad_routine_is_explained_to_the_model_not_saved(store):
    result = routines.routine_save({"name": "Mau", "steps": [{"tool": "pc_power_shutdown", "args": {}}]})
    assert not result["ok"] and "pc_power_shutdown" in result["message"]
    assert routines.routine_save({"name": "Vazio", "steps": []})["ok"] is False
    assert store.list() == []
    assert routines.routine_run({"name": "não existe"})["status"] == "not_found"


def test_routines_are_nanos_own_data_in_the_policy():
    manager = PermissionManager()
    for tool in ("routine_save", "routine_list", "routine_run", "routine_delete"):
        capability = manager.resolve_tool_capability(tool, {})
        assert manager.policy_engine.evaluate(capability, arguments={}).decision.value == "AUTONOMOUS", tool
    guard = GuardrailsEngine()
    assert guard.requires_confirmation("routine_delete", {"name": "x"})
    assert not guard.requires_confirmation("routine_save", {"name": "x"})


def test_routine_requests_are_offered_the_routine_tools():
    from core import model_selection

    tools = pc_control.get_tools() + task_plan.get_tools() + routines.get_tools()
    for message in ("prepara o meu setup de estudo", "guarda isto como rotina setup de estudo",
                    "que rotinas tenho?", "save this as my study routine"):
        names = {t["function"]["name"] for t in model_selection.select_tools(message, tools)}
        assert {"routine_run", "routine_list"} <= names, message
    names = {t["function"]["name"] for t in model_selection.select_tools("guarda isto como rotina", tools)}
    assert "routine_save" in names
    names = {t["function"]["name"] for t in model_selection.select_tools(
        "abre o notion e o spotify, põe o volume a 30 e encosta o chrome à esquerda", tools)}
    assert {"task_plan", "pc_app_launch", "pc_window_snap", "pc_volume_set"} <= names


# ══ Plans and routines inside a conversation (core/brain_tasks.py) ══════

class FakeExecutor:
    """Answers like ToolExecutor, with the real tool schemas in its registry."""

    def __init__(self, answers=None, untrusted=()):
        self.calls: list[tuple[str, dict]] = []
        self.answers = {name: list(values) for name, values in (answers or {}).items()}
        self.untrusted = set(untrusted)
        self.registry = {}
        for tool in pc_control.get_tools() + task_plan.get_tools() + routines.get_tools():
            fn = tool["function"]
            self.registry[fn["name"]] = {"input_schema": fn["parameters"]}
        self.registry["web_navigate_extract"] = {"input_schema": {"type": "object"}}
        self.registry["set_reminder"] = {"input_schema": {"type": "object"}}
        self.registry["calendar_add_event"] = {"input_schema": {"type": "object"}}

    def get_retry_policy(self, _name):
        return "SAFE_TO_RETRY"

    async def execute_tool_async(self, name, args=None, **_kw):
        args = dict(args or {})
        self.calls.append((name, args))
        queue = self.answers.get(name)
        if queue:
            return queue.pop(0)
        if name == "task_plan":
            return {"success": True, "status": "completed", "output": task_plan.task_plan(args)}
        if name.startswith("routine_"):
            return {"success": True, "status": "completed",
                    "output": routines.TOOL_HANDLERS[name](args)}
        if name in self.untrusted:
            return {"success": True, "status": "completed", "output": {"ok": True, "text": "page"},
                    "metadata": {"trust": TrustLevel.UNTRUSTED_EXTERNAL.value}}
        if name == "pc_volume_set":
            return ok(level=args.get("level"))
        if name == "pc_window_list":
            return ok(windows=[{"process": "chrome.exe", "title": "Chrome"},
                               {"process": "notion.exe", "title": "Notion"}])
        return ok()


def make_brain(executor=None, approve=True):
    guard = GuardrailsEngine()
    asked: list[str] = []

    async def confirm(message, _meta):
        asked.append(message)
        return approve

    guard.set_confirm_callback(confirm)
    executor = executor or FakeExecutor()
    brain = Brain("", guard, MemoryEngine(), {"ollama_enabled": False}, tool_executor=executor)
    brain._sync_plugin_tools = lambda _name: None
    brain._begin_task_turn()
    brain._turn_tool_results, brain._turn_tool_inflight, brain._turn_plan_tools = {}, {}, set()
    return brain, executor, asked


def plan_call(steps, goal="Preparar o estudo"):
    return {"function": {"name": "task_plan", "arguments": json.dumps({"goal": goal, "steps": steps})}}


def tool_call(name, args):
    return {"function": {"name": name, "arguments": json.dumps(args)}}


async def drain(brain, calls):
    holder: dict = {}
    statuses = [line async for line in brain._run_calls(calls, holder)]
    return holder["results"], statuses


def test_a_plan_with_arguments_is_run_by_nano_step_by_step_without_asking():
    brain, executor, asked = make_brain()
    results, statuses = run(drain(brain, [plan_call(STUDY_STEPS)]))
    executed = [name for name, _ in executor.calls if name not in ("task_plan", "pc_window_list")]
    assert executed == [s["tool"] for s in STUDY_STEPS]
    assert asked == []                                      # only safe steps: no "Avanço?"
    assert any("1/4" in line for line in statuses) and all(line.startswith("_thinking_:") for line in statuses)
    output = results[0]["output"]
    assert results[0]["status"] == "plan_executed" and output["executed_by_nano"]
    assert output["done"] == 4 and [s["status"] for s in output["steps"]] == ["ok"] * 4
    note = brain._plan_report_note()                        # offers to save it as a routine, once
    assert note and "rotina" in note
    assert brain._plan_report_note() is None


def test_the_models_own_copy_of_a_planned_step_does_not_run_twice():
    brain, executor, _ = make_brain()
    calls = [plan_call(STUDY_STEPS[:1]), tool_call("pc_app_launch", {"name": "notion"})]
    results, _ = run(drain(brain, calls))
    launches = [args for name, args in executor.calls if name == "pc_app_launch"]
    assert launches == [{"name": "Notion"}]
    assert results[1]["status"] == "covered_by_plan"


def test_a_plan_with_a_step_the_guardrails_ask_about_asks_once():
    brain, executor, asked = make_brain()
    steps = [STUDY_STEPS[3], {"tool": "calendar_add_event", "label": "Marcar o ensaio"}]
    run(drain(brain, [plan_call(steps)]))
    assert len(asked) == 1 and "Marcar o ensaio" in asked[0]


def test_a_plan_after_reading_a_page_always_asks():
    brain, executor, asked = make_brain(FakeExecutor(untrusted={"web_navigate_extract"}))
    run(drain(brain, [tool_call("web_navigate_extract", {"url": "https://example.com"})]))
    run(drain(brain, [plan_call(STUDY_STEPS)]))
    assert len(asked) == 1


def test_a_declined_plan_runs_nothing():
    brain, executor, asked = make_brain(FakeExecutor(untrusted={"web_navigate_extract"}), approve=False)
    run(drain(brain, [tool_call("web_navigate_extract", {"url": "https://example.com"})]))
    results, _ = run(drain(brain, [plan_call(STUDY_STEPS)]))
    assert results[0].get("cancelled") is True
    assert not any(name == "pc_app_launch" for name, _ in executor.calls)


def test_a_plan_step_with_bad_arguments_goes_back_to_the_model_before_anything_runs():
    brain, executor, asked = make_brain()
    steps = [STUDY_STEPS[0], {"tool": "pc_volume_set", "args": {"level": "alto"}, "label": "Volume"}]
    results, _ = run(drain(brain, [plan_call(steps)]))
    assert results[0]["success"] is False and "Volume" in results[0]["error"]
    assert executor.calls == []


def test_what_a_plan_did_not_do_is_said_by_nano_and_tenta_outra_vez_redoes_only_that():
    executor = FakeExecutor({"pc_app_launch": [ok(), failed("launch_failed"), failed("launch_failed")]})
    brain, executor, _ = make_brain(executor)
    run(drain(brain, [plan_call(STUDY_STEPS)]))
    note = brain._plan_report_note()
    assert "Abrir Chrome" in note and "Chrome à esquerda" in note and "tenta outra vez" in note.casefold()

    # Next turn: "tenta outra vez" re-runs the Chrome launch and its window step only, no model.
    brain.conversation.append({"role": "assistant", "content": "Fiz quase tudo." + note})
    brain.route_for_async = AsyncMock(side_effect=AssertionError("the model was consulted"))
    executor.calls.clear()
    tokens = run(_collect(brain.chat("tenta outra vez")))
    again = [(n, a) for n, a in executor.calls if n != "pc_window_list"]
    assert again == [("pc_app_launch", {"name": "Chrome"}),
                     ("pc_window_snap", {"query": "Chrome", "position": "left"})]
    answer = "".join(t for t in tokens if not t.startswith("_thinking_:"))
    assert "2 de 2" in answer
    assert brain.last_metadata["direct_action"] == "retry"


def test_tenta_outra_vez_means_nothing_special_once_the_conversation_moved_on():
    executor = FakeExecutor({"pc_app_launch": [failed("launch_failed"), failed("launch_failed")]})
    brain, executor, _ = make_brain(executor)
    run(drain(brain, [plan_call(STUDY_STEPS[:1])]))
    brain._begin_task_turn()            # another turn happened in between
    brain._begin_task_turn()
    assert brain._retry_candidate("tenta outra vez") is None


def test_a_routine_said_by_name_runs_through_the_same_checked_steps(store):
    routines.routine_save({"name": "Setup de estudo", "steps": STUDY_STEPS})
    brain, executor, asked = make_brain()
    results, statuses = run(drain(brain, [tool_call("routine_run", {"name": "estudo"})]))
    executed = [name for name, _ in executor.calls if name not in ("routine_run", "pc_window_list")]
    assert executed == [s["tool"] for s in STUDY_STEPS]
    assert results[0]["status"] == "routine_executed" and results[0]["output"]["done"] == 4
    assert asked == []


def test_guarda_isto_como_rotina_saves_what_nano_just_did(store):
    brain, executor, _ = make_brain()
    run(drain(brain, [plan_call(STUDY_STEPS)]))
    brain._begin_task_turn()                                 # the next request
    result = run(brain._run_tool(tool_call("routine_save", {"name": "Setup de estudo",
                                                            "from_last_actions": True})))
    assert result["success"], result
    saved = store.list()[0]
    assert [a["tool"] for a in saved["actions"]] == [s["tool"] for s in STUDY_STEPS]
    assert store.match("prepara o meu setup de estudo") is not None


def test_guarda_isto_with_nothing_done_asks_for_the_steps(store):
    brain, executor, _ = make_brain()
    brain._begin_task_turn()
    result = run(brain._run_tool(tool_call("routine_save", {"name": "X", "from_last_actions": True})))
    assert result["success"] is False and result["status"] == "nothing_to_save"
    assert store.list() == []


def test_saving_or_running_a_routine_after_reading_a_page_asks_first(store):
    brain, executor, asked = make_brain(FakeExecutor(untrusted={"web_navigate_extract"}), approve=False)
    run(drain(brain, [tool_call("web_navigate_extract", {"url": "https://example.com"})]))
    result = run(brain._run_tool(tool_call("routine_save", {"name": "X", "steps": STUDY_STEPS[:1]})))
    assert result.get("cancelled") and len(asked) == 1 and store.list() == []


def test_a_plan_the_model_carried_out_says_which_steps_never_happened():
    brain, executor, _ = make_brain()
    steps = [{"label": "Procurar o ficheiro", "tool": "pc_file_search"},
             {"label": "Abrir o ficheiro", "tool": "pc_file_open"}]
    run(drain(brain, [plan_call(steps)]))
    run(drain(brain, [tool_call("pc_file_search", {"query": "apresentação"})]))
    note = brain._plan_report_note()
    assert note and "Abrir o ficheiro" in note and "Procurar o ficheiro" not in note


async def _collect(stream):
    return [token async for token in stream]


def test_a_protocol_said_in_chat_shows_each_step_and_can_be_retried(store):
    store.save({"name": "Modo estudo", "phrases": ["modo estudo"], "open_apps": ["Notion"], "volume": 20,
                "message": "Bom estudo!"})
    executor = FakeExecutor({"pc_volume_set": [{"success": False, "status": "permission_denied"}]})
    brain, executor, _ = make_brain(executor)
    brain.route_for_async = AsyncMock(side_effect=AssertionError("the model was consulted"))
    tokens = run(_collect(brain.chat("modo estudo")))
    assert any("1/2" in t for t in tokens if t.startswith("_thinking_:"))
    answer = "".join(t for t in tokens if not t.startswith("_thinking_:"))
    assert answer.startswith("Bom estudo!") and "Volume a 20%" in answer and "tenta outra vez" in answer
    executor.calls.clear()
    run(_collect(brain.chat("tenta outra vez")))
    assert [n for n, _ in executor.calls] == ["pc_volume_set"]


def test_a_different_call_next_to_a_plan_is_still_made():
    brain, executor, _ = make_brain()
    calls = [plan_call(STUDY_STEPS[:1]), tool_call("pc_app_launch", {"name": "Spotify"})]
    results, _ = run(drain(brain, calls))
    launches = [args["name"] for name, args in executor.calls if name == "pc_app_launch"]
    assert launches == ["Notion", "Spotify"] and results[1]["status"] != "covered_by_plan"


def test_guarda_isto_in_the_same_request_saves_this_requests_actions(store):
    brain, executor, _ = make_brain()
    brain._last_actions = [protocols.validate_action({"tool": "pc_volume_set", "args": {"level": 5}})]
    run(drain(brain, [tool_call("pc_app_launch", {"name": "Spotify"})]))
    result = run(brain._run_tool(tool_call("routine_save", {"name": "Música", "from_last_actions": True})))
    assert result["success"]
    assert [a["args"] for a in store.list()[0]["actions"]] == [{"name": "Spotify"}]
