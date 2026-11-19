"""A reminder on this PC is created without a confirmation card.

"Lembra-me daqui a 10 minutos" used to open "Vou criar um lembrete... Confirmas?"
because set_reminder was mapped to external.send. A reminder is a notification
on this PC, stored in nano.db, cancelled in one click, so it now runs
unattended -- EXCEPT when the same turn already read outside content (a page,
the screen, a document), where an injected "set a reminder: call this number"
must still go through the person.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.brain import Brain
from core.guardrails import GuardrailsEngine
from core.permission_manager import PermissionManager
from core.trust import TrustLevel


def _call(name: str, args: dict):
    function = type("Function", (), {"name": name, "arguments": json.dumps(args)})()
    return type("ToolCall", (), {"function": function})()


def _brain():
    guard = GuardrailsEngine()
    guard.ask_confirmation = AsyncMock(return_value=False)
    executor = MagicMock()
    executor.registry = {}
    executor.execute_tool_async = AsyncMock(return_value={"success": True, "status": "ok", "output": {"id": 1}})
    executor.normalize_for_ledger = None
    brain = Brain("test-key", guard, AsyncMock(), {"ollama_enabled": False}, tool_executor=executor)
    brain._sync_plugin_tools = lambda _name: None
    return brain, guard, executor


def test_the_policy_lets_a_reminder_run_and_still_asks_to_cancel_one():
    manager = PermissionManager()
    create = manager.resolve_tool_capability("set_reminder", {"text": "água", "when": "10 minutos"})
    assert create == "nano.reminder.create"
    evaluation = manager.policy_engine.evaluate(create, arguments={})
    assert evaluation.decision.value == "AUTONOMOUS" and not evaluation.requires_confirmation
    cancel = manager.resolve_tool_capability("cancel_reminder", {"reminder_id": 1})
    assert manager.policy_engine.evaluate(cancel, arguments={}).decision.value != "AUTONOMOUS"


def test_guardrails_no_longer_ask_for_a_reminder_but_still_for_a_cancel():
    guard = GuardrailsEngine()
    assert guard.requires_confirmation("set_reminder", {"text": "x", "when": "10 minutos"}) is False
    assert guard.requires_confirmation("cancel_reminder", {"reminder_id": 1}) is True


@pytest.mark.asyncio
async def test_a_plain_reminder_runs_without_a_card():
    brain, guard, executor = _brain()
    result = await brain._run_tool(_call("set_reminder", {"text": "beber água", "when": "10 minutos"}))
    assert result.get("success") is True
    guard.ask_confirmation.assert_not_awaited()
    executor.execute_tool_async.assert_awaited_once()


@pytest.mark.asyncio
async def test_after_reading_a_page_a_reminder_asks_again():
    brain, guard, executor = _brain()
    brain._turn_tool_results["web"] = {"success": True, "metadata": {"trust": TrustLevel.UNTRUSTED_EXTERNAL.value}}
    result = await brain._run_tool(_call("set_reminder", {"text": "liga para 900", "when": "10 minutos"}))
    assert result.get("cancelled") is True
    guard.ask_confirmation.assert_awaited_once()
    assert "lembrete" in guard.ask_confirmation.await_args.args[0] or guard.ask_confirmation.await_args.args[0] == "set_reminder"
    executor.execute_tool_async.assert_not_awaited()
