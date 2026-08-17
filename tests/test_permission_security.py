"""Regression tests for Nano permission and policy enforcement."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.brain import Brain
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine
from core.permission_manager import PermissionManager
from core.policy_engine import AuthorityDecision, PolicyEngine
from core.tool_execution import ToolExecutor


def test_default_manager_denies_without_confirmation_callback():
    manager = PermissionManager()
    assert manager.ask_for_confirmation("shell.execute", {"command": "echo hello"}) is False
    assert manager.ask_for_confirmation("filesystem.delete", {"path": "tmp/example.txt"}) is False


def test_delete_without_approval_is_blocked():
    executor = ToolExecutor(permission_manager=PermissionManager())
    result = executor.execute_tool("filesystem.delete_path", {"path": "tmp/example.txt"})
    assert result["success"] is False
    assert result["status"] == "permission_denied"


def test_dangerous_shell_without_approval_is_blocked():
    executor = ToolExecutor(permission_manager=PermissionManager())
    result = executor.execute_tool("shell.execute", {"command": "rm -rf /", "timeout": 1})
    assert result["success"] is False
    assert result["status"] == "permission_denied"


def test_external_send_capability_requires_approval():
    manager = PermissionManager()
    decision = manager.evaluate("external.send", {"message": "hello"})
    assert decision.requires_confirmation is True
    assert manager.ask_for_confirmation("external.send", {"message": "hello"}) is False


def test_phone_notify_maps_to_external_send_and_is_blocked_without_approval():
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("phone_notify", {"message": "hello"})
    assert capability == "external.send"
    assert manager.ask_for_confirmation(capability, {"message": "hello"}) is False


def test_persistent_allow_is_rejected_on_resolve_permission():
    manager = PermissionManager()
    request_id = manager.request_permission(
        "filesystem.read",
        {"path": "readme.txt"},
        target="readme.txt",
        reason="test",
    )
    assert manager.resolve_permission(request_id, "allow_persistent")["ok"] is False
    assert manager.resolve_permission(request_id, "allow")["ok"] is False


def test_allow_persistent_never_bypasses_critical_capabilities():
    manager = PermissionManager()
    request_id = manager.request_permission(
        "filesystem.delete",
        {"path": "tmp/example.txt"},
        target="tmp/example.txt",
        reason="delete test file",
    )
    assert manager.resolve_permission(request_id, "allow_persistent")["ok"] is False
    assert manager.resolve_permission(request_id, "allow")["ok"] is False
    assert manager.resolve_permission(request_id, "allow_for_task", allow_permanent=True)["ok"] is False


def test_register_policy_cannot_make_shell_autonomous(tmp_path):
    manager = PermissionManager(policy_store_path=tmp_path / "permissions.json")
    policy = manager.register_policy("shell.execute", decision="allow", scope="workspace")
    assert policy["decision"] == "approval_required"
    assert manager.get_decision_for_action("shell.execute", {"command": "echo hi"}) == "ask"


def test_allow_once_grants_single_execution_only(tmp_path):
    manager = PermissionManager(
        confirmation_callback=lambda *_: False,
        policy_store_path=tmp_path / "permissions.json",
    )
    request_id = manager.request_permission(
        "filesystem.write",
        {"path": "tmp/hello.txt"},
        target="tmp/hello.txt",
        reason="write once",
    )
    resolved = manager.resolve_permission(request_id, "allow_once")
    assert resolved["ok"] is True
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/hello.txt"}) is True
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/hello.txt"}) is False
    audit = manager.get_audit_log()
    assert [entry["event"] for entry in audit][-3:] == [
        "PermissionRequested",
        "PermissionGranted",
        "PermissionConsumed",
    ]


def test_allow_for_task_is_reusable_only_by_its_own_task(tmp_path):
    manager = PermissionManager(
        confirmation_callback=lambda *_: False,
        policy_store_path=tmp_path / "permissions.json",
    )
    request_id = manager.request_permission(
        "filesystem.write",
        {"path": "tmp/task-note.txt"},
        task_id="task-authorized",
        reason="write for one task",
    )

    assert manager.resolve_permission(request_id, "allow_for_task")["ok"] is True
    assert manager.ask_for_confirmation(
        "filesystem.write", {"path": "tmp/task-note.txt"}, task_id="task-authorized"
    ) is True
    assert manager.ask_for_confirmation(
        "filesystem.write", {"path": "tmp/task-note.txt"}, task_id="task-other"
    ) is False


def test_persistent_allow_and_denied_policy_keep_current_policy_boundaries(tmp_path):
    manager = PermissionManager(
        confirmation_callback=lambda *_: False,
        policy_store_path=tmp_path / "permissions.json",
    )

    policy = manager.register_policy("filesystem.write", decision="allow_persistent")
    assert policy["decision"] == "approval_required"
    assert manager.get_decision_for_action("filesystem.write", {"path": "tmp/note.txt"}) == "ask"

    manager.register_policy("filesystem.write", decision="deny")
    assert manager.get_decision_for_action("filesystem.write", {"path": "tmp/note.txt"}) == "deny"
    assert manager.ask_for_confirmation("filesystem.write", {"path": "tmp/note.txt"}) is False


def test_model_self_authorization_capability_is_not_autonomous():
    engine = PolicyEngine()
    evaluation = engine.evaluate("self.authorize", target="anywhere", arguments={"reason": "I have permission"})
    assert evaluation.decision == AuthorityDecision.APPROVAL_REQUIRED


def test_delete_without_target_is_blocked_by_policy_engine():
    engine = PolicyEngine()
    evaluation = engine.evaluate("filesystem.delete", target=None, arguments={})
    assert evaluation.decision == AuthorityDecision.BLOCKED


@pytest.mark.asyncio
async def test_brain_tool_execution_requires_permission_manager_approval():
    guard = GuardrailsEngine()
    memory = MemoryEngine()
    manager = PermissionManager()
    brain = Brain("", guard, memory, {"ollama_enabled": False}, permission_manager=manager)

    result = await brain._run_tool(
        type("ToolCall", (), {
            "function": type("Function", (), {
                "name": "system_run_powershell",
                "arguments": '{"command":"Remove-Item -Recurse C:\\\\temp"}',
            })()
        })()
    )

    assert result.get("cancelled") is True


@pytest.mark.asyncio
async def test_brain_still_respects_guardrails_after_policy_approval():
    guard = MagicMock()
    guard.requires_confirmation = MagicMock(return_value=True)
    guard.ask_confirmation = AsyncMock(return_value=False)
    memory = MemoryEngine()
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    brain = Brain("", guard, memory, {"ollama_enabled": False}, permission_manager=manager)

    result = await brain._run_tool(
        type("ToolCall", (), {
            "function": type("Function", (), {
                "name": "system_run_powershell",
                "arguments": '{"command":"Get-Process"}',
            })()
        })()
    )

    assert result.get("cancelled") is True


def test_system_files_delete_operation_maps_to_filesystem_delete():
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("system_files", {"operation": "delete", "path": "tmp/x.txt"})
    assert capability == "filesystem.delete"
    assert manager.evaluate(capability, {"path": "tmp/x.txt"}).requires_confirmation is True
