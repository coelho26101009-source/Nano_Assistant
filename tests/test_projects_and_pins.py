"""Pinned chats and projects in the rail.

Both live in the thread's own metadata, so deleting a thread takes them with
it, and a pinned or filed thread is always listed however old it is.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.memory as memory_module
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "helios.db")
    engine = MemoryEngine()
    built = MemoryStack(engine, background=False)
    try:
        yield built
    finally:
        built.stop()
        engine.close()


def _thread(stack, text="uma pergunta bastante longa"):
    thread = stack.new_conversation()["id"]
    stack.record_user_message(text, conversation_id=thread)
    stack.record_assistant_message("uma resposta bastante longa", conversation_id=thread)
    return thread


def test_pinning_and_unpinning(stack):
    thread = _thread(stack)
    assert stack.conversations.organise(thread, pinned=True)["pinned"] is True
    assert stack.conversations.get(thread)["pinned"] is True
    assert stack.conversations.organise(thread, pinned=False)["pinned"] is False
    assert "pinned" not in stack.conversations.get(thread)["metadata"]


def test_filing_under_a_project_and_taking_it_out(stack):
    thread = _thread(stack)
    assert stack.conversations.organise(thread, project="  Escola   2026 ")["project"] == "Escola 2026"
    assert stack.conversations.get(thread)["project"] == "Escola 2026"
    assert stack.conversations.organise(thread, project="")["project"] == ""
    assert stack.conversations.get(thread)["project"] == ""


@pytest.mark.parametrize("name", ["x" * 41, "som\x07", 42])
def test_a_bad_project_name_is_refused(stack, name):
    thread = _thread(stack)
    assert stack.conversations.organise(thread, project=name)["ok"] is False


def test_an_unknown_thread_is_refused(stack):
    assert stack.conversations.organise("conv_nope", pinned=True)["ok"] is False


def test_pinned_and_filed_threads_are_listed_however_old(stack):
    old_pinned = _thread(stack, "pergunta antiga fixada aqui")
    old_filed = _thread(stack, "pergunta antiga num projeto")
    _thread(stack, "uma conversa solta qualquer")
    stack.conversations.organise(old_pinned, pinned=True)
    stack.conversations.organise(old_filed, project="Casa")
    organised = {t["id"] for t in stack.conversations.organised()}
    assert organised == {old_pinned, old_filed}


def test_deleting_a_thread_takes_its_pin_with_it(stack):
    thread = _thread(stack)
    stack.conversations.organise(thread, pinned=True)
    stack.conversations.delete(thread)
    assert stack.conversations.organised() == []


def test_the_rail_groups_pinned_then_projects_then_days():
    source = (ROOT / "frontend" / "lib" / "conversations.ts").read_text(encoding="utf-8")
    assert "export function organiseThreads(" in source
    rail = (ROOT / "frontend" / "components" / "Rail.tsx").read_text(encoding="utf-8")
    assert "organiseThreads(matching)" in rail
