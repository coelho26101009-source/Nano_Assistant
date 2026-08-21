"""Contract tests for what the UI renders.

The UI must never invent a state. These tests pin the shape and the vocabulary
of the readiness payload so a backend change cannot silently make the interface
report health it has not verified.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"

# The only states the UI is allowed to display.
ALLOWED_STATES = {
    "READY", "WORKING", "WAITING", "APPROVAL_REQUIRED", "PROCESSING",
    "SETUP_REQUIRED", "MODEL_MISSING", "MODEL_LOADING", "MODEL_UNAVAILABLE",
    "PROVIDER_READY", "LISTENING", "STT_UNAVAILABLE", "MIC_UNAVAILABLE",
    "OLLAMA_UNAVAILABLE", "OLLAMA_NOT_INSTALLED", "UNAVAILABLE", "NOT_INSTALLED",
    "BACKEND_OFFLINE", "DISABLED", "OFFLINE", "ERROR", "EXPERIMENTAL",
    "NOT_AVAILABLE", "UNKNOWN",
    # Task statuses render through the same indicator.
    "QUEUED", "PLANNING", "RUNNING", "RETRYING", "RECOVERABLE",
    "WAITING_FOR_PERMISSION", "NEEDS_ATTENTION",
    "COMPLETED", "CANCELLED", "FAILED",
}


@pytest.fixture(scope="module")
def main_module():
    import core.main as module
    return module


# ------------------------------------------------------------- readiness

def test_system_readiness_reports_every_subsystem(main_module):
    payload = main_module.get_system_readiness()
    for key in ("agent", "voice", "wakeWord", "model", "worker", "providers"):
        assert key in payload, f"readiness payload is missing '{key}'"
    assert "emergencyStop" in payload
    assert "autonomyMode" in payload


def test_every_reported_state_is_in_the_allowed_vocabulary(main_module):
    payload = main_module.get_system_readiness()
    states = [
        payload["agent"]["state"],
        payload["voice"]["state"],
        payload["wakeWord"]["state"],
        payload["model"]["state"],
        payload["worker"]["state"],
        payload["browser"]["state"],
        payload["vision"]["state"],
    ]
    unknown = [state for state in states if state not in ALLOWED_STATES]
    assert not unknown, f"states outside the allowed vocabulary: {unknown}"


def test_voice_readiness_is_not_ready_without_its_runtime(main_module):
    """Voice must not claim READY just because it is enabled in config."""
    import importlib.util

    payload = main_module.get_system_readiness()
    runtimes_present = all(
        importlib.util.find_spec(name) is not None
        for name in ("faster_whisper", "edge_tts", "pyaudio")
    )
    if not runtimes_present:
        assert payload["voice"]["state"] != "READY"
        assert payload["voice"]["blockers"], "a non-ready voice must explain why"


def test_wake_word_reports_model_missing_without_a_model(main_module):
    payload = main_module.get_system_readiness()
    assert payload["wakeWord"]["state"] in ALLOWED_STATES
    if payload["wakeWord"]["state"] == "READY":
        assert payload["wakeWord"]["modelStatus"] == "READY"


def test_provider_health_is_measured_not_asserted(main_module):
    providers = main_module.get_provider_health()
    assert set(providers) >= {"ollama", "cloud", "browser", "desktop"}
    # "online" may only appear for a provider that was actually reachable.
    assert providers["cloud"] in {"configured", "not_configured"}


def test_command_center_no_longer_hardcodes_provider_health():
    source = (REPO_ROOT / "core" / "main.py").read_text(encoding="utf-8")
    assert '"providers": {"ollama": "online"' not in source
    assert "get_provider_health()" in source


def test_readiness_payload_is_json_serialisable(main_module):
    json.dumps(main_module.get_system_readiness())


def test_emergency_stop_is_reflected_in_readiness(main_module):
    main_module.permission_manager.set_emergency_stop(True)
    try:
        payload = main_module.get_system_readiness()
        assert payload["emergencyStop"] is True
        assert payload["agent"]["state"] == "OFFLINE"
    finally:
        main_module.permission_manager.set_emergency_stop(False)


# ------------------------------------------------- permission UI contract

def test_pending_permission_carries_everything_the_ui_must_show(main_module):
    manager = main_module.permission_manager
    request_id = manager.request_permission(
        "filesystem.write",
        {"path": "notes.txt"},
        task_id="ui-contract",
        reason="Escrever notas do projeto",
        tool="filesystem.write_file",
        agent="CodingAgent",
    )
    try:
        pending = [item for item in manager.get_pending_permissions() if item["id"] == request_id]
        assert pending, "request did not reach the pending list"
        request = pending[0]
        # The Permission Center renders each of these; a missing field would
        # degrade the dialog to "authorize an operation".
        for field in ("capability", "target", "scope", "risk", "reason", "tool", "task_id"):
            assert field in request, f"pending permission is missing '{field}'"
        assert request["target"], "a permission request must name its target"
    finally:
        manager.resolve_permission(request_id, "deny")


def test_permission_decisions_offered_by_the_ui_are_the_ones_the_backend_accepts():
    """The UI must offer exactly the three decisions the policy accepts."""
    source = (FRONTEND / "components" / "Pages.tsx").read_text(encoding="utf-8")
    for decision in ("deny", "allow_once", "allow_for_task"):
        assert f'"{decision}"' in source, f"the permission UI never offers {decision}"
    # ALLOW_PERSISTENT stays disabled by design and must not be offered anywhere.
    for path in list((FRONTEND / "components").glob("*.tsx")) + list((FRONTEND / "pages").glob("*.tsx")):
        assert "allow_persistent" not in path.read_text(encoding="utf-8"), (
            f"{path.name} offers persistent permission, which the core disabled"
        )


def test_backend_still_refuses_persistent_allow(main_module):
    assert main_module.resolve_permission("whatever", "allow_persistent")["ok"] is False
    assert main_module.set_permission_policy("filesystem.write", "allow_persistent")["ok"] is False


def test_ui_never_renders_secret_looking_arguments():
    source = (FRONTEND / "components" / "ui.tsx").read_text(encoding="utf-8")
    assert "sanitizeArgs" in source
    assert "secret|token|password" in source


# ------------------------------------------------------- task rendering

def test_task_detail_exposes_verification_and_retry_state(main_module):
    task = main_module.task_engine.create_task("ui contract task", description="verificação")
    try:
        detail = main_module.get_task_detail(task["id"])
        assert detail["ok"] is True
        for field in ("status", "progress", "retries", "last_event", "created_at", "metadata"):
            assert field in detail["task"], f"task detail is missing '{field}'"
        assert "events" in detail and "permissions" in detail
    finally:
        main_module.task_engine.cancel_task(task["id"])


def test_cancel_endpoint_is_exposed_and_releases_grants(main_module):
    task = main_module.task_engine.create_task("cancel me")
    result = main_module.cancel_agent_task(task["id"])
    assert result["ok"] is True
    assert result["task"]["status"] == "CANCELLED"
    assert main_module.permission_manager.list_task_grants(task["id"]) == []


# ------------------------------------------------------- frontend wiring

def _frontend_sources() -> str:
    parts = []
    for path in list((FRONTEND / "components").glob("*.tsx")) + list((FRONTEND / "pages").glob("*.tsx")):
        parts.append(path.read_text(encoding="utf-8"))
    parts.append((FRONTEND / "lib" / "backend.ts").read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_safety_controls_are_reachable_from_the_ui():
    """The kill switch had no control at all. It must stay wired."""
    source = _frontend_sources()
    for endpoint in ("set_emergency_stop", "get_system_readiness", "cancel_agent_task", "resolve_permission"):
        assert endpoint in source, f"the UI never calls '{endpoint}'"


def _strip_comments(source: str) -> str:
    """Remove // and /* */ comments so prose about a bug is not read as the bug."""
    import re

    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def test_status_indicators_never_assert_readiness_as_a_constant():
    """A StatusIndicator may not claim health with a literal prop.

    `state={expression}` is fine — the value came from the backend. A literal
    `state="READY"` would be the UI asserting health nobody measured. The only
    literals allowed are honest downgrades, which claim less, never more.
    """
    import re

    # DISABLED belongs here for the same reason as the rest: it claims *less*
    # capability, never more. The one literal use is "autorizacao permanente:
    # desativada por desenho", which the backend enforces unconditionally --
    # see test_backend_still_refuses_persistent_allow.
    honest_downgrades = {
        "EXPERIMENTAL", "NOT_AVAILABLE", "SETUP_REQUIRED", "UNKNOWN", "OFFLINE", "DISABLED",
    }
    literal_state = re.compile(r'<StatusIndicator[^>]*?\sstate="([A-Z_]+)"', re.S)

    for path in (FRONTEND / "components").glob("*.tsx"):
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for state in literal_state.findall(code):
            assert state in honest_downgrades, (
                f"{path.name} asserts '{state}' as a constant instead of reading it from the backend"
            )


def test_no_component_reintroduces_the_hardcoded_provider_block():
    """The exact shape the audit found: providers declared online in code."""
    import re

    block = re.compile(r'providers\s*[:=]\s*\{[^}]*["\']online["\']', re.S)
    for path in list((FRONTEND / "components").glob("*.tsx")) + list((FRONTEND / "pages").glob("*.tsx")):
        code = _strip_comments(path.read_text(encoding="utf-8"))
        assert not block.search(code), f"{path.name} hardcodes provider health"


# ------------------------------------------------- status vocabulary parity

def test_every_task_status_has_a_label_in_the_ui():
    """An unmapped status normalises to UNKNOWN and renders "Desconhecido".

    Task rows pass `task.status` straight to StatusIndicator, so a status the
    label map does not know makes the whole Tasks page read "Desconhecido".
    """
    import core.main as main

    source = (FRONTEND / "components" / "ui.tsx").read_text(encoding="utf-8")
    labelled = set(re.findall(r"^\s*([A-Z_]+):\s*\"", source, re.M))

    statuses = (
        main.ACTIVE_TASK_STATUSES | main.ATTENTION_TASK_STATUSES | main.TERMINAL_TASK_STATUSES
    )
    missing = sorted(statuses - labelled)
    assert not missing, f"these task statuses render as 'Desconhecido': {missing}"


def test_the_agent_does_not_report_error_for_tasks_awaiting_the_user(main_module):
    """NEEDS_ATTENTION means the user has something to do, not that Nano broke."""
    source = (REPO_ROOT / "core" / "main.py").read_text(encoding="utf-8")
    block = re.search(r'summary\.get\("NEEDS_ATTENTION"\):\s*\n(?:\s*#.*\n)*\s*agent_state = "([A-Z_]+)"', source)
    assert block, "the NEEDS_ATTENTION branch of the agent state disappeared"
    assert block.group(1) != "ERROR", (
        "a task waiting for the user makes the agent report ERROR, which pins the "
        "indicator red permanently"
    )


def test_agent_state_stays_inside_the_allowed_vocabulary(main_module):
    assert main_module.get_system_readiness()["agent"]["state"] in ALLOWED_STATES
