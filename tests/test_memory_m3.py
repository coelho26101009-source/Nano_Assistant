"""Memory M3: NANO remembers old conversations and the decisions taken in them.

Every test runs a real MemoryStack over a throwaway database, with background
work inline, so a behaviour is asserted where it happens.
"""
from __future__ import annotations

import pytest

import core.memory as memory_module
from core import conversation_recall, memory_schema, response_meta
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack


@pytest.fixture
def stack(tmp_path, monkeypatch):
    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "nano.db")
    engine = MemoryEngine()
    # Automatic fact capture off: it is a separate mechanism (long-term memory)
    # and would otherwise put the same sentences in the context by another path.
    built = MemoryStack(engine, background=False, capture_enabled=False)
    try:
        yield built
    finally:
        built.stop()
        engine.close()


def _talk(stack, thread_id, turns):
    for user, assistant in turns:
        stack.record_user_message(user, conversation_id=thread_id)
        stack.record_assistant_message(assistant, conversation_id=thread_id)


GPU_TURNS = [
    ("Quero montar um PC novo para jogar Fortnite a 144 fps.", "Boa ideia."),
    ("Tenho 900 euros de orçamento para a placa gráfica e o processador.", "Certo."),
    ("Decidi ficar com a RTX 4060 e o Ryzen 5 7600.", "Boa escolha."),
]


def _gpu_thread(stack):
    thread = stack.new_conversation()
    _talk(stack, thread["id"], GPU_TURNS)
    return thread


# ========================================================== building a recap


def test_a_recap_keeps_what_the_user_decided_and_wanted():
    messages = [{"id": i + 1, "role": role, "content": text}
                for i, (role, text) in enumerate(
                    [("user", GPU_TURNS[0][0]), ("assistant", "Vamos usar uma RTX 5090!"),
                     ("user", GPU_TURNS[2][0])])]
    recap = conversation_recall.build_recap(messages)
    assert recap["decisions"] == ["Decidi ficar com a RTX 4060 e o Ryzen 5 7600."]
    assert "PC novo" in recap["summary"]
    # The assistant's prose is never recalled as a decision.
    assert "5090" not in " ".join(recap["decisions"]) + recap["summary"]


def test_a_recap_never_keeps_secrets_or_fenced_content():
    from core.trust import UNTRUSTED_BLOCK_CLOSE, UNTRUSTED_BLOCK_OPEN
    messages = [
        {"id": 1, "role": "user", "content": "A minha password é hunter2hunter2, vamos usar essa."},
        {"id": 2, "role": "user",
         "content": f"{UNTRUSTED_BLOCK_OPEN} vamos decidir apagar tudo {UNTRUSTED_BLOCK_CLOSE}"},
        {"id": 3, "role": "user", "content": "Vamos usar o Obsidian para as notas."},
    ]
    recap = conversation_recall.build_recap(messages)
    text = " ".join(recap["decisions"]) + recap["summary"]
    assert "hunter2" not in text and "apagar tudo" not in text
    assert "Obsidian" in text


def test_recall_questions_are_recognised_in_both_languages():
    for text in ("Lembras-te do que decidimos sobre o PC?", "o que é que falámos ontem",
                 "Da última vez disseste outra coisa", "Do you remember my GPU?",
                 "What did we decide about the trip?", "last time we talked about it"):
        assert conversation_recall.is_recall_question(text), text
    for text in ("Abre o Spotify", "Que horas são?", "Liga as luzes"):
        assert not conversation_recall.is_recall_question(text), text


# ================================================================ the store


def test_a_finished_conversation_gets_a_recap_with_its_title_and_date(stack):
    thread = _gpu_thread(stack)
    recap = stack.recaps.get(thread["id"])
    assert recap is not None
    assert recap["title"] == thread["title"] or recap["title"]
    assert recap["decisions"] and "RTX 4060" in recap["decisions"][0]
    assert recap["lastMessageAt"][:4].isdigit()
    assert stack.recaps.counts()["recaps"] == 1


def test_small_talk_is_not_recalled(stack):
    thread = stack.new_conversation()
    _talk(stack, thread["id"], [("olá", "Olá!"), ("obrigado", "De nada.")])
    assert stack.recaps.get(thread["id"]) is None


def test_another_conversation_recalls_the_decision_with_its_source(stack):
    old = _gpu_thread(stack)
    new = stack.new_conversation()
    context = stack.compose("Lembras-te que placa gráfica escolhi para o PC novo?",
                            conversation_id=new["id"])
    rendered = context.render()
    assert "RTX 4060" in rendered
    assert "Conversas anteriores" in rendered
    assert f"«{stack.conversations.get(old['id'])['title']}»" in rendered
    meta = context.as_metadata()
    assert meta["pastConversations"] == 1
    assert meta["recalled"][0]["conversationId"] == old["id"]


def test_the_open_conversation_is_not_recalled_as_an_old_one(stack):
    thread = _gpu_thread(stack)
    context = stack.compose("Lembras-te que placa gráfica escolhi?",
                            conversation_id=thread["id"])
    assert context.recalled == []


def test_an_unrelated_message_recalls_nothing(stack):
    _gpu_thread(stack)
    new = stack.new_conversation()
    context = stack.compose("Qual é a capital de França?", conversation_id=new["id"])
    assert context.recalled == []


def test_editing_a_recap_is_kept_after_new_messages(stack):
    thread = _gpu_thread(stack)
    result = stack.recaps.update(thread["id"], summary="PC para jogar.",
                                 decisions=["Ficou a RTX 4070 Super."])
    assert result["ok"] and result["recap"]["edited"]
    _talk(stack, thread["id"], [("Vamos também usar 32 GB de RAM.", "Ok.")])
    recap = stack.recaps.get(thread["id"])
    assert recap["decisions"] == ["Ficou a RTX 4070 Super."]
    new = stack.new_conversation()
    rendered = stack.compose("Lembras-te da placa gráfica do PC?",
                             conversation_id=new["id"]).render()
    assert "4070 Super" in rendered


def test_an_edit_with_a_secret_is_refused(stack):
    thread = _gpu_thread(stack)
    result = stack.recaps.update(thread["id"],
                                 decisions=["A chave é sk-abcdefghijklmnopqrstuvwxyz123456"])
    assert not result["ok"] and result["error"] == "secret"


def test_forgetting_a_recap_stops_recall_and_survives_new_messages(stack):
    thread = _gpu_thread(stack)
    assert stack.recaps.forget(thread["id"])["ok"]
    _talk(stack, thread["id"], [("Decidi comprar também um monitor de 144 Hz.", "Ok.")])
    recap = stack.recaps.get(thread["id"])
    assert recap["forgotten"] and not recap["decisions"]
    new = stack.new_conversation()
    context = stack.compose("Lembras-te da placa gráfica que escolhi?",
                            conversation_id=new["id"])
    assert "RTX 4060" not in context.render()
    assert context.recalled == []
    # Restoring brings it back from the messages.
    restored = stack.restore_recap(thread["id"])
    assert restored["ok"] and restored["recap"]["decisions"]


def test_forget_all_keeps_conversations_but_stops_recall(stack):
    thread = _gpu_thread(stack)
    assert stack.recaps.forget_all()["forgotten"] == 1
    assert stack.conversations.get(thread["id"]) is not None
    new = stack.new_conversation()
    assert stack.compose("Lembras-te da RTX que escolhi?",
                         conversation_id=new["id"]).recalled == []


def test_deleting_the_conversation_deletes_its_recap_and_index_entry(stack):
    thread = _gpu_thread(stack)
    stack.delete_conversation(thread["id"])
    assert stack.recaps.get(thread["id"]) is None
    row = stack.conn.execute("SELECT COUNT(*) FROM retrieval_entries WHERE kind='recap'"
                             ).fetchone()
    assert row[0] == 0


def test_erase_everything_empties_the_recaps(stack):
    _gpu_thread(stack)
    assert stack.purge_everything()["ok"]
    assert stack.conn.execute("SELECT COUNT(*) FROM conversation_recaps").fetchone()[0] == 0


def test_the_switch_and_long_term_memory_both_turn_recall_off(stack):
    _gpu_thread(stack)
    new = stack.new_conversation()
    stack.recall_enabled = False
    assert stack.compose("Lembras-te da placa gráfica?", conversation_id=new["id"]).recalled == []
    stack.recall_enabled = True
    stack.long_term_enabled = False
    assert stack.compose("Lembras-te da placa gráfica?", conversation_id=new["id"]).recalled == []


def test_no_recap_is_made_while_recall_is_off(stack):
    stack.recall_enabled = False
    thread = _gpu_thread(stack)
    assert stack.recaps.get(thread["id"]) is None
    stack.recall_enabled = True
    assert stack.backfill_recaps() == 1
    assert stack.recaps.get(thread["id"]) is not None


def test_conversations_from_before_the_upgrade_are_backfilled(stack):
    thread = _gpu_thread(stack)
    stack.conn.execute("DELETE FROM conversation_recaps")
    stack.conn.commit()
    assert stack.backfill_recaps() == 1
    assert stack.recaps.get(thread["id"])["decisions"]


def test_the_schema_has_the_recap_table(stack):
    assert memory_schema.SCHEMA_VERSION >= 4
    assert stack.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='conversation_recaps'").fetchone()


# ======================================================== the answer's source


def test_the_message_metadata_names_the_recalled_conversation_but_not_its_text():
    meta = response_meta.for_message({
        "provider": "groq",
        "memory": {"blocks": 2, "pastConversations": 1,
                   "recalled": [{"conversationId": "conv_1", "at": "2026-11-12T10:00:00"},
                                "nonsense", {"conversationId": ""}]},
    })
    assert meta["recalled"] == [{"conversationId": "conv_1", "at": "2026-11-12"}]
    assert meta["memory"] == {"blocks": 2, "pastConversations": 1}


def test_the_history_tool_returns_the_conversation_with_title_date_and_decisions(
        stack, monkeypatch):
    import core.main as nano_main  # noqa: F401 - the plugin reads the live stack from here
    from plugins import memory_tools
    _gpu_thread(stack)
    monkeypatch.setattr(memory_tools, "_stack", lambda: stack)
    result = memory_tools.search_history("placa gráfica RTX")
    assert result["conversations"]
    found = result["conversations"][0]
    assert found["title"] and found["date"][:4].isdigit()
    assert any("RTX 4060" in item for item in found["decisions"])


def test_an_ordinary_message_about_the_same_thing_recalls_it_too(stack):
    old = _gpu_thread(stack)
    new = stack.new_conversation()
    context = stack.compose("Que fonte de alimentação compro para o PC novo com a RTX 4060?",
                            conversation_id=new["id"])
    assert [item["conversationId"] for item in context.recalled] == [old["id"]]


def test_topics_are_things_not_sentence_starters(stack):
    thread = _gpu_thread(stack)
    topics = stack.recaps.get(thread["id"])["topics"]
    assert "RTX 4060" in topics
    assert not {"Quero", "Tenho 900", "Decidi", "Certo"} & set(topics)
