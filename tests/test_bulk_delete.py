"""Bulk conversation deletion: the backend operation and its reporting.

SPLIT OUT OF ``test_auto_memory.py`` ON PURPOSE. These tests exercise
``MemoryStack.delete_conversations``, ``ConversationStore.delete_many`` and the
``delete_conversations`` bridge endpoint — the bulk-delete work, which is a
different change from the Memory / Second Brain one and ships separately. Left
in the memory module they would have made that module fail to import against a
tree without the bulk endpoint, which is exactly the coupling this file exists
to remove.

The fixture is duplicated rather than shared through a conftest: the two files
are meant to be independent, and a shared fixture would put the coupling back
one level down.
"""
from __future__ import annotations

import pytest

import core.memory as memory_module
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack


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


# ============================================================= bulk deletion


def test_deleting_several_conversations_removes_their_messages_and_index_rows(stack):
    ids = []
    for index in range(3):
        thread = stack.new_conversation(f"Conversa {index}")
        ids.append(thread["id"])
        stack.record_user_message("uma pergunta suficientemente longa para indexar",
                                  conversation_id=thread["id"])
        stack.record_assistant_message("uma resposta suficientemente longa para indexar",
                                       conversation_id=thread["id"])
    keep = stack.new_conversation("Fica")
    stack.record_user_message("esta conversa fica exactamente onde está",
                              conversation_id=keep["id"])

    result = stack.delete_conversations(ids)
    assert result["ok"] is True
    assert result["removed"] == 3
    assert result["failed"] == []
    assert {thread["id"] for thread in stack.conversations.list(limit=50)} == {keep["id"]}
    for conversation_id in ids:
        assert stack.conversations.messages(conversation_id) == []
        assert stack.conversations.get_summary(conversation_id)["summary"] == ""


def test_bulk_deletion_reports_a_partial_failure_instead_of_claiming_success(stack):
    """Reporting "done" for a batch that partly failed is the specific outcome
    the per-id result shape exists to prevent."""
    thread = stack.new_conversation()
    result = stack.delete_conversations([thread["id"], "conv_que_nao_existe"])
    assert result["removed"] == 1
    assert result["ok"] is False
    assert [entry["id"] for entry in result["failed"]] == ["conv_que_nao_existe"]


def test_bulk_deletion_releases_the_active_thread_it_deleted(stack):
    thread = stack.new_conversation()
    assert stack.active_conversation_id == thread["id"]
    stack.delete_conversations([thread["id"]])
    assert stack.active_conversation_id is None


def test_bulk_deletion_keeps_the_long_term_memories_born_in_those_threads(stack):
    """Same lifecycle rule as a single delete. A memory is a separate object
    with its own row in Memória; selecting more conversations does not change
    what a conversation IS."""
    thread = stack.new_conversation()
    stack.capture_memories("O meu PC tem uma GTX 1660 Ti.", conversation_id=thread["id"])
    assert stack.memories.stats()["active"] == 1
    stack.delete_conversations([thread["id"]])
    assert stack.memories.stats()["active"] == 1


def test_bulk_deletion_is_bounded_and_refuses_an_empty_request(stack):
    from core.conversation_store import MAX_LIST_LIMIT

    assert stack.delete_conversations([])["removed"] == 0
    assert stack.delete_conversations([])["ok"] is False
    # A renderer asking for more than the store will ever list is truncated,
    # not honoured.
    result = stack.delete_conversations([f"conv_{i}" for i in range(MAX_LIST_LIMIT + 50)])
    assert result["requested"] <= MAX_LIST_LIMIT


def test_the_same_id_sent_twice_is_one_deletion_and_not_one_failure(stack):
    """Deduplication is what keeps the REPORT honest, not just the work small.

    Without it the second copy of an id addresses a thread the first copy has
    already removed, that delete fails, and the batch comes back ok=False with
    a failure naming a conversation that was in fact deleted successfully. The
    user would be told something went wrong with the very row that went.
    """
    first = stack.new_conversation("Uma")
    second = stack.new_conversation("Outra")

    result = stack.delete_conversations(
        [first["id"], first["id"], second["id"], first["id"]])

    assert result["ok"] is True
    assert result["requested"] == 2, "the duplicates were counted as work"
    assert result["removed"] == 2
    assert result["failed"] == []
    assert sorted(result["deleted"]) == sorted([first["id"], second["id"]])
    assert stack.conversations.list(limit=50) == []


def test_blank_and_whitespace_ids_are_dropped_rather_than_reported_as_failures(stack):
    """The list arrives from the renderer, so it is normalised before it is
    believed. An empty string is not a conversation that failed to delete; it
    is not a conversation."""
    thread = stack.new_conversation()
    result = stack.delete_conversations([thread["id"], "", "   ", None])
    assert result["ok"] is True
    assert result["requested"] == 1
    assert result["removed"] == 1
    assert result["failed"] == []


def test_the_bulk_endpoint_takes_ids_and_nothing_that_resembles_a_query():
    """No generic DB endpoint. The set of things the renderer can ask for has
    to stay finite and readable on one screen."""
    import inspect

    import core.main as main_module

    signature = inspect.signature(main_module.delete_conversations)
    assert list(signature.parameters) == ["conversation_ids"]
    source = inspect.getsource(main_module.delete_conversations)
    for forbidden in ("SELECT", "DELETE FROM", "WHERE", "execute("):
        assert forbidden not in source
