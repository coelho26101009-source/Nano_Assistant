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


def test_shell_execution_is_refused_as_unavailable_not_merely_unapproved():
    """The refusal must not read as "you have not approved this yet".

    Before the V2 checkpoint audit this returned permission_denied, which was
    true but misleading in both directions: it implied an approval could
    unlock it, and there really was a handler behind it that an approval WOULD
    have unlocked. Both are gone. The status now names the actual reason.
    """
    executor = ToolExecutor(permission_manager=PermissionManager())
    result = executor.execute_tool("shell.execute", {"command": "rm -rf /", "timeout": 1})
    assert result["success"] is False
    assert result["status"] == "unsupported_capability"
    assert result["metadata"]["unsupported"] is True
    assert "não executa comandos arbitrários" in result["error"]


def test_no_confirmation_is_ever_requested_for_shell_execution():
    """A confirmation callback that says yes to everything changes nothing.

    This is the heart of the reported bug: the person must never be offered a
    Yes that cannot deliver. If the callback is consulted at all, the refusal
    came too late.
    """
    asked: list[str] = []

    def _record(*args, **kwargs):
        asked.append(str(args[0] if args else kwargs.get("capability")))
        return True

    manager = PermissionManager(confirmation_callback=_record)
    executor = ToolExecutor(permission_manager=manager)
    result = executor.execute_tool("shell.execute", {"command": "whoami"})

    assert result["success"] is False
    assert result["status"] == "unsupported_capability"
    assert asked == [], f"a confirmation was offered for an unavailable capability: {asked}"


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


def test_register_policy_cannot_make_a_high_risk_capability_autonomous(tmp_path):
    """An approval-gated capability degrades a blanket "allow" to "ask".

    This used to be written against shell.execute, which is now blocked
    outright and so can no longer demonstrate the DOWNGRADE. process.start is
    the same shape -- HIGH risk, APPROVAL_REQUIRED -- so the generic rule stays
    covered, and the stronger shell.execute contract is asserted below.
    """
    manager = PermissionManager(policy_store_path=tmp_path / "permissions.json")
    policy = manager.register_policy("process.start", decision="allow", scope="workspace")
    assert policy["decision"] == "approval_required"
    assert manager.get_decision_for_action("process.start", {"path": "notepad.exe"}) == "ask"


def test_shell_execute_cannot_be_allow_listed_at_all(tmp_path):
    """Stronger than "ask": an unavailable capability is never approvable.

    Registering it as "allow" must not produce a decision that any Yes could
    satisfy, because there is no handler behind it to satisfy.
    """
    manager = PermissionManager(policy_store_path=tmp_path / "permissions.json")
    manager.register_policy("shell.execute", decision="allow", scope="workspace")
    assert manager.get_decision_for_action("shell.execute", {"command": "echo hi"}) == "deny"
    assert manager.policy_engine.evaluate("shell.execute", target="echo hi").decision.value == "BLOCKED"

    # And the block survives an attempt to revoke it from the Permissions page.
    manager.revoke_policy("shell.execute")
    assert manager.policy_engine.evaluate("shell.execute", target="echo hi").decision.value == "BLOCKED"


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


def _tool_call(name: str, arguments: str):
    return type("ToolCall", (), {
        "function": type("Function", (), {"name": name, "arguments": arguments})()
    })()


@pytest.mark.asyncio
async def test_brain_refuses_powershell_without_offering_approval():
    """The reported bug, at the dispatch layer.

    This previously asserted only ``cancelled is True`` -- which it reached by
    letting the request travel all the way to a confirmation and having that
    confirmation decline. That proved the machine was safe while the SENTENCE
    the user got was false: it said a Yes was the missing ingredient. Nothing
    may be offered at all now.
    """
    guard = GuardrailsEngine()
    memory = MemoryEngine()
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    brain = Brain("", guard, memory, {"ollama_enabled": False}, permission_manager=manager)

    result = await brain._run_tool(
        _tool_call("system_run_powershell", '{"command":"Remove-Item -Recurse C:\\\\temp"}')
    )

    assert result.get("ok") is False
    assert result.get("status") == "unsupported_capability"
    # Not "cancelled": nobody cancelled anything, and saying so would be the
    # same untruth in a different tense.
    assert result.get("cancelled") is None
    assert "não executa comandos arbitrários" in result.get("message", "")


@pytest.mark.asyncio
async def test_brain_never_consults_guardrails_for_an_unavailable_capability():
    """Get-Process — the exact request from the human retest.

    The guardrail mock would confirm if asked. It must not be asked: the
    refusal has to land before any confirmation layer, or the user is back to
    "Pretende prosseguir?" for something that cannot happen.
    """
    guard = MagicMock()
    guard.requires_confirmation = MagicMock(return_value=True)
    guard.ask_confirmation = AsyncMock(return_value=True)
    memory = MemoryEngine()
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    brain = Brain("", guard, memory, {"ollama_enabled": False}, permission_manager=manager)

    result = await brain._run_tool(_tool_call("system_run_powershell", '{"command":"Get-Process"}'))

    assert result.get("status") == "unsupported_capability"
    guard.requires_confirmation.assert_not_called()
    guard.ask_confirmation.assert_not_called()


@pytest.mark.asyncio
async def test_brain_still_respects_guardrails_for_capabilities_that_do_exist():
    """The unavailable-capability shortcut must not bypass real guardrails.

    Regression guard for the obvious way to get this wrong: refusing early is
    only correct for tools that do not exist. A real, confirmation-gated tool
    must still reach the guardrail and still be stoppable there.
    """
    guard = MagicMock()
    guard.requires_confirmation = MagicMock(return_value=True)
    guard.ask_confirmation = AsyncMock(return_value=False)
    memory = MemoryEngine()
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    brain = Brain("", guard, memory, {"ollama_enabled": False}, permission_manager=manager)

    result = await brain._run_tool(_tool_call("pc_screenshot_capture", "{}"))

    assert result.get("cancelled") is True
    guard.ask_confirmation.assert_awaited()


def test_system_files_delete_operation_maps_to_filesystem_delete():
    manager = PermissionManager()
    capability = manager.resolve_tool_capability("system_files", {"operation": "delete", "path": "tmp/x.txt"})
    assert capability == "filesystem.delete"
    assert manager.evaluate(capability, {"path": "tmp/x.txt"}).requires_confirmation is True
