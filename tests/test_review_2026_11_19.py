"""Fixes from the full code review of 2026-11-19."""
from __future__ import annotations

import pytest

from core.permission_manager import PermissionManager
from core.plugin_loader import load_all_plugins
from core.policy_engine import AuthorityDecision, PolicyEngine
from core.trust import is_untrusted_capability


@pytest.mark.parametrize("tool", [
    "list_reminders", "calendar_list_events", "list_facts", "memory_search_history",
    "rag_search", "rag_list_docs", "system_stats", "monitor_status",
])
def test_reading_nanos_own_data_does_not_ask_or_block(tool):
    """'What's on my agenda?' used to ask for approval; system_stats was blocked."""
    load_all_plugins()
    manager = PermissionManager()
    capability = manager.resolve_tool_capability(tool, {})
    evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
    assert evaluation.decision == AuthorityDecision.AUTONOMOUS, (tool, capability)


@pytest.mark.parametrize("tool", [
    "cancel_reminder", "calendar_add_event", "calendar_delete_event",
    "remember_fact", "forget_fact", "rag_index_pdf", "iot_command", "phone_notify",
])
def test_changing_or_sending_still_asks(tool):
    load_all_plugins()
    manager = PermissionManager()
    capability = manager.resolve_tool_capability(tool, {})
    evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
    assert evaluation.requires_confirmation, (tool, capability)


def test_indexed_documents_are_untrusted():
    assert is_untrusted_capability("nano.documents.read")
    assert not is_untrusted_capability("nano.data.read")


def test_a_missing_risk_is_reported_as_a_level_not_an_enum_repr():
    request = PolicyEngine().permission_request(
        task_id=None, agent=None, tool=None, capability="x", risk=None,
        target=None, scope=None, reason=None)
    assert request["risk"] == "MEDIUM"


def test_vision_reuses_a_recent_ollama_inventory(monkeypatch):
    from core import vision

    calls = []

    def transport(method, url, **kwargs):
        calls.append(url)
        return {"models": [{"name": "llava:7b"}]}

    monkeypatch.setattr(vision, "_TAGS_CACHE", {})
    monkeypatch.setattr(vision, "_http_json", transport)
    monkeypatch.setattr(vision, "TRANSPORT", transport)
    assert vision.local_vision_models("http://h:1") == ["llava:7b"]
    assert vision.local_vision_models("http://h:1") == ["llava:7b"]
    assert len(calls) == 1
