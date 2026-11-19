"""Retrying / editing the last turn, and empty threads that never pile up.

Two features of the chat that stand on the conversation store:

* "Tentar de novo" and "Editar" take the thread's LAST turn back out
  (``ConversationStore.truncate_last_turn``) before the question is asked
  again, so the transcript, the Brain's window and the retrieval index never
  hold two versions of the same turn.
* "Nova conversa" pressed repeatedly used to leave a row per press in the rail.
  An empty thread is now reused, discarded when the user walks away from it,
  and never listed.
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
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "nano.db")
    engine = MemoryEngine()
    built = MemoryStack(engine, background=False)
    try:
        yield built
    finally:
        built.stop()
        engine.close()


def _turn(stack, thread_id, question, answer):
    stack.record_user_message(question, conversation_id=thread_id)
    stack.record_assistant_message(answer, conversation_id=thread_id)


# ================================================================ last turn


def test_rewinding_removes_the_last_question_and_its_answer_only(stack):
    thread = stack.new_conversation()["id"]
    _turn(stack, thread, "primeira pergunta bastante longa", "primeira resposta bastante longa")
    _turn(stack, thread, "segunda pergunta bastante longa", "segunda resposta bastante longa")

    result = stack.rewind_last_turn(thread)

    assert result["ok"] is True
    assert result["text"] == "segunda pergunta bastante longa"
    assert result["removed"] == 2
    rows = stack.conversations.messages(thread)
    assert [(r["role"], r["content"]) for r in rows] == [
        ("user", "primeira pergunta bastante longa"),
        ("assistant", "primeira resposta bastante longa"),
    ]
    assert stack.conversations.get(thread)["messageCount"] == 2


def test_rewinding_also_removes_the_turns_tool_rows(stack):
    thread = stack.new_conversation()["id"]
    _turn(stack, thread, "pergunta inicial bastante longa", "resposta inicial bastante longa")
    stack.record_user_message("abre o bloco de notas por favor", conversation_id=thread)
    stack.conversations.append(thread, "tool", '{"ok": true}')
    stack.record_assistant_message("Abri o Bloco de Notas.", conversation_id=thread)

    assert stack.rewind_last_turn(thread)["removed"] == 3
    rows = stack.conversations.messages(thread, roles=frozenset({"user", "assistant", "tool"}))
    assert [r["role"] for r in rows] == ["user", "assistant"]


def test_a_rewound_turn_is_no_longer_searchable(stack):
    thread = stack.new_conversation()["id"]
    _turn(stack, thread, "qual é a capital da Mongólia exatamente",
          "A capital da Mongólia é Ulan Bator, a cidade maior do país.")
    assert stack.index.search("Ulan Bator", kinds=["message"])

    stack.rewind_last_turn(thread)

    assert stack.index.search("Ulan Bator", kinds=["message"]) == []
    assert stack.index.search("Mongólia", kinds=["message"]) == []


def test_rewinding_an_empty_thread_reports_that_there_is_nothing_to_retry(stack):
    thread = stack.new_conversation()["id"]
    assert stack.rewind_last_turn(thread) == {"ok": False, "error": "no_turn"}
    assert stack.rewind_last_turn("conv_does_not_exist")["error"] == "unknown_conversation"


# ============================================================= empty threads


def test_new_conversation_reuses_the_empty_one_already_open(stack):
    first = stack.new_conversation()
    second = stack.new_conversation()
    assert first["id"] == second["id"]
    assert len(stack.conversations.list(limit=50)) == 1


def test_an_empty_thread_the_user_leaves_is_discarded(stack):
    kept = stack.new_conversation()["id"]
    _turn(stack, kept, "uma conversa com conteúdo real", "uma resposta com conteúdo real")
    empty = stack.new_conversation()["id"]
    assert empty != kept

    stack.open_conversation(kept)

    assert stack.conversations.exists(empty) is False
    assert stack.conversations.exists(kept) is True


def test_a_thread_with_messages_is_never_discarded(stack):
    first = stack.new_conversation()["id"]
    _turn(stack, first, "uma pergunta qualquer bastante longa", "uma resposta qualquer bastante longa")
    second = stack.new_conversation()["id"]
    stack.open_conversation(first)
    stack.new_conversation()
    assert stack.conversations.exists(first)
    assert stack.conversations.exists(second) is False


def test_a_named_thread_is_kept_even_when_empty(stack):
    named = stack.new_conversation("Ideias para o projeto")["id"]
    stack.new_conversation()
    assert stack.conversations.exists(named)


def test_a_held_thread_survives_a_click_on_new_conversation(stack):
    """The gap between Enter and the question being written.

    send_message resolves the owner on the bridge thread and the coroutine
    writes the question a moment later. A "Nova conversa" click inside that
    gap must not discard the thread the question is about to land in.
    """
    owner = stack.new_conversation()["id"]
    stack.hold(owner)
    other = stack.new_conversation()["id"]
    assert other != owner, "a held thread is about to receive a turn; it is not reusable"
    assert stack.conversations.exists(owner)

    stack.record_user_message("a mensagem que estava a caminho", conversation_id=owner)
    stack.release(owner)
    assert [r["content"] for r in stack.conversations.messages(owner)] == [
        "a mensagem que estava a caminho"]


def test_the_rail_lists_no_empty_threads():
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    body = source.split("def list_conversations(")[1].split("\n@eel.expose")[0]
    assert "messageCount" in body, "list_conversations must leave empty threads out of the rail"


def test_retry_rewinds_before_it_dispatches():
    """The model must never see the answer it is being asked to replace."""
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    body = source.split("def retry_last_turn(")[1].split("\n@eel.expose")[0]
    rewind = body.index("rewind_last_turn(")
    rebuild = body.index("switch_conversation(")
    dispatch = body.index("_dispatch_turn(")
    assert rewind < rebuild < dispatch
