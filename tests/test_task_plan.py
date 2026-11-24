"""Tasks of several steps: the plan, and when the person is asked about it.

The plan executes nothing itself. It asks once only when a step is one the
guardrails always ask about (then approving it spares that question for the
listed steps) or when the turn read outside content; a plan of safe steps
just runs. The executor's own approvals (closing apps, power, deleting,
sending) still apply. Running a plan with arguments: tests/test_task_chains.py.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.brain import Brain
from core.guardrails import GuardrailsEngine
from core.permission_manager import PermissionManager
from plugins import task_plan

PLAN = {"goal": "Preparar a apresentação de amanhã", "steps": [
    {"label": "Procurar a apresentação", "tool": "pc_file_search"},
    {"label": "Criar um evento para ensaiar", "tool": "calendar_add_event"},
    {"label": "Pôr o volume a 30", "tool": "pc_volume_set"},
]}


def _call(name, args):
    function = type("Function", (), {"name": name, "arguments": json.dumps(args)})()
    return type("ToolCall", (), {"function": function})()


def _brain(approve=True):
    guard = GuardrailsEngine()
    seen: list[str] = []

    async def confirm(message, meta):
        seen.append(message)
        return approve

    guard.set_confirm_callback(confirm)
    executor = MagicMock()
    executor.registry = {"task_plan": {}, "pc_file_search": {}, "calendar_add_event": {}, "pc_volume_set": {}}

    async def execute(name, args):
        if name == "task_plan":
            return {"success": True, "status": "ok", "output": task_plan.task_plan(args)}
        return {"success": True, "status": "ok", "output": {"ok": True}}

    executor.execute_tool_async = AsyncMock(side_effect=execute)
    brain = Brain("test-key", guard, AsyncMock(), {"ollama_enabled": False}, tool_executor=executor)
    brain._sync_plugin_tools = lambda _name: None
    return brain, seen, executor


def test_the_plan_is_validated_and_bounded():
    assert task_plan.validate(PLAN)["ok"]
    assert task_plan.validate({"goal": "x", "steps": []})["error"] == "invalid_plan"
    too_long = {"goal": "x", "steps": [{"label": "a", "tool": "pc_volume_set"}] * (task_plan.MAX_STEPS + 1)}
    assert task_plan.validate(too_long)["error"] == "plan_too_long"
    nested = {"goal": "x", "steps": [{"label": "plano", "tool": "task_plan"}]}
    assert task_plan.validate(nested)["ok"] is False
    assert task_plan.validate({"goal": "x", "steps": [{"label": "a", "tool": "rm -rf"}]})["ok"] is False


def test_the_question_lists_every_step():
    text = task_plan.describe(task_plan.validate(PLAN))
    for index, step in enumerate(PLAN["steps"], start=1):
        assert f"{index}. {step['label']}" in text


def test_showing_a_plan_is_autonomous_in_the_policy():
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("task_plan", PLAN)
    assert capability == "nano.plan.show"
    assert manager.policy_engine.evaluate(capability, arguments={}).decision.value == "AUTONOMOUS"


@pytest.mark.asyncio
async def test_an_approved_plan_is_asked_once_and_its_steps_are_not_asked_again():
    brain, seen, executor = _brain(approve=True)
    brain._turn_plan_tools = set()
    result = await brain._run_tool(_call("task_plan", PLAN))
    assert result["success"] and len(seen) == 1 and "1. Procurar a apresentação" in seen[0]
    # calendar_add_event normally asks "Vou criar um evento... Confirmas?"
    step = await brain._run_tool(_call("calendar_add_event", {"title": "Ensaio", "start": "2026-11-20T10:00"}))
    assert step["success"] and len(seen) == 1


@pytest.mark.asyncio
async def test_a_plan_of_safe_steps_is_not_asked_but_a_step_outside_it_still_asks():
    brain, seen, _ = _brain(approve=True)
    await brain._run_tool(_call("task_plan", {"goal": "x", "steps": [{"label": "volume", "tool": "pc_volume_set"}]}))
    assert seen == []           # nothing risky in it: no "Avanço?"
    await brain._run_tool(_call("calendar_add_event", {"title": "Ensaio", "start": "2026-11-20T10:00"}))
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_a_declined_plan_runs_nothing():
    brain, seen, executor = _brain(approve=False)
    result = await brain._run_tool(_call("task_plan", PLAN))
    assert result.get("cancelled") is True
    executor.execute_tool_async.assert_not_awaited()
    assert brain._turn_plan_tools == set()


@pytest.mark.asyncio
async def test_a_plan_with_a_tool_nano_does_not_have_goes_back_to_the_model():
    brain, seen, executor = _brain()
    result = await brain._run_tool(_call("task_plan", {"goal": "x", "steps": [
        {"label": "formatar", "tool": "system_format_drive"}]}))
    assert result["success"] is False and seen == []
    executor.execute_tool_async.assert_not_awaited()


def test_multi_step_requests_are_offered_the_plan_tool():
    from core import model_selection
    from plugins import pc_control

    tools = pc_control.get_tools() + task_plan.get_tools()
    for message in ("prepara o PC para a apresentação: abre o PowerPoint e depois põe o volume a 30",
                    "open Spotify and then set the volume to 20"):
        names = {t["function"]["name"] for t in model_selection.select_tools(message, tools)}
        assert "task_plan" in names, message
    names = {t["function"]["name"] for t in model_selection.select_tools("abre o Spotify", tools)}
    assert "task_plan" not in names
