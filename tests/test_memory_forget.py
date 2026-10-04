"""Forgetting a memory forgets everything the Second Brain derived from it.

THE DEFECT THIS FILE EXISTS FOR (SEC-1)
---------------------------------------
A durable memory is promoted into the Second Brain: its sentence is copied into
``knowledge_nodes.summary``, the entities it names become node titles, and both
are indexed for retrieval. The Memória page could delete, archive, demote, edit
or clear that memory and the memory row and its index entry changed correctly
-- while the derived nodes kept the old sentence, kept being found, and kept
reaching the model through the composer's "Conhecimento relacionado" section,
in every mode, including the cloud ones. Startup reconciliation then made it
worse: it re-created nodes the user had deleted and added to every mention
count and edge weight on every launch, and a promotion queued before a delete
could run after it and write the forgotten sentence back.

HOW THE CONTRACT IS ASSERTED
----------------------------
Every lifecycle test plants a unique marker, proves the marker really reaches
the composed context through the KNOWLEDGE section first (so the final
assertion cannot pass vacuously), performs the action, and then asserts at the
boundary that matters: the rendered context, the system prompt, and the request
a recording fake provider actually received. Row-level checks are there too,
but they are never the only evidence: a row that is gone proves nothing about a
copy of it somewhere else.

Each test builds a real MemoryStack over a real SQLite file in ``tmp_path``.
The user's database is never involved: ``core.memory.DB_PATH`` is redirected
before any engine is constructed, and tests/conftest.py isolates the profile.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import uuid

import pytest

import core.memory as memory_module
from core import memory_schema, text_normalize
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack
from core.retrieval import RetrievalIndex

DELETE_MARK = "GHOST_DELETE_MARKER_8F2A"
ARCHIVE_MARK = "GHOST_ARCHIVE_MARKER_51B0"
DEMOTE_MARK = "GHOST_DEMOTE_MARKER_9E77"
EDIT_OLD_MARK = "GHOST_EDIT_OLD_MARKER_7D91"
EDIT_NEW_MARK = "FRESH_EDIT_NEW_MARKER_3C55"
CLEAR_MARKS = ("GHOST_CLEAR_MARKER_A1B1", "GHOST_CLEAR_MARKER_C2D2")
QUEUED_MARK = "GHOST_QUEUED_MARKER_4E10"
KEEP_MARK = "KEEP_EVIDENCE_MARKER_C3D4"
MANUAL_MARK = "MANUAL_NOTE_MARKER_77AA"
LEGACY_MARK = "LEGACY_GHOST_MARKER_0B0B"


# ===================================================================== fixtures


@pytest.fixture
def db_path(tmp_path, monkeypatch):
    path = tmp_path / "helios.db"
    monkeypatch.setattr(memory_module, "DB_PATH", path)
    return path


@pytest.fixture
def stack(db_path):
    engine = MemoryEngine()
    built = MemoryStack(engine, background=False)
    built.ensure_active()
    try:
        yield built
    finally:
        built.stop()
        engine.close()


def _open(*, background=False):
    """A fresh engine and stack over the CURRENT DB_PATH -- a relaunch of Nano."""
    engine = MemoryEngine()
    return engine, MemoryStack(engine, background=background)


def _close(engine, built):
    built.stop()
    if built._worker is not None:
        built._worker.join(timeout=5)
    engine.close()


# ====================================================================== helpers


def _remember(stack, text, kind="hardware"):
    saved = stack.remember(text, kind=kind)
    assert saved["ok"], saved
    return saved["memory"]


def _context(stack, query):
    return stack.compose(query).render()


def _knowledge_text(stack, query):
    """Only what the KNOWLEDGE section contributed. Used for preconditions."""
    context = stack.compose(query)
    return "\n".join(block.text for block in context.blocks if block.section == "knowledge")


def _titles(stack):
    return {node["title"] for node in stack.knowledge.list_nodes(limit=300)}


def _stored_graph_text(stack):
    """Every piece of text the graph AND its retrieval index hold, as one blob."""
    parts: list[str] = []
    for row in stack.conn.execute("SELECT title, summary, body FROM knowledge_nodes"):
        parts.extend(str(value or "") for value in row)
    for row in stack.conn.execute(
            "SELECT title, body, metadata FROM retrieval_entries WHERE kind='node'"):
        parts.extend(str(value or "") for value in row)
    if stack.index.fts_available:
        for row in stack.conn.execute(
                "SELECT title, body FROM retrieval_fts WHERE entry_id LIKE 'node:%'"):
            parts.extend(str(value or "") for value in row)
    return "\n".join(parts)


def _graph_state(stack):
    """The whole derived graph and its index, row for row, timestamps included."""
    tables = [row[0] for row in stack.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'knowledge_%'"
        " ORDER BY name")]
    state = {table: sorted(stack.conn.execute(f"SELECT * FROM {table}").fetchall(),
                           key=repr)
             for table in tables}
    state["retrieval_entries"] = sorted(stack.conn.execute(
        "SELECT * FROM retrieval_entries WHERE kind='node'").fetchall(), key=repr)
    if stack.index.fts_available:
        state["retrieval_fts"] = sorted(stack.conn.execute(
            "SELECT rowid, entry_id, title, body FROM retrieval_fts"
            " WHERE entry_id LIKE 'node:%'").fetchall(), key=repr)
    return state


def _node(stack, title):
    node = stack.knowledge.node_by_title(title)
    assert node is not None, f"{title!r} is not in the graph: {_titles(stack)}"
    return node


def _edge(stack, source_title, relation, target_title):
    source, target = _node(stack, source_title), _node(stack, target_title)
    row = stack.conn.execute(
        "SELECT weight FROM knowledge_edges WHERE relation=? AND"
        " ((source_id=? AND target_id=?) OR (source_id=? AND target_id=?))",
        (relation, source["id"], target["id"], target["id"], source["id"])).fetchone()
    return None if row is None else float(row[0])


def _brain(stack):
    """A Brain wired to this stack, with no provider and no tools."""
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine

    return Brain("", GuardrailsEngine(), stack.engine,
                 {"ollama_enabled": False, "local": {"enabled": False}},
                 memory_stack=stack)


def _system_prompt(stack, query):
    return asyncio.run(_brain(stack)._build_system_prompt(query, with_tools=False))


# ============================================================== A. DELETE


def test_deleting_a_memory_removes_everything_the_graph_derived_from_it(stack):
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti com o firmware {DELETE_MARK}.")
    # Precondition: the marker reaches the context THROUGH THE GRAPH, in the
    # node titles and in the node summaries alike.
    assert DELETE_MARK in _knowledge_text(stack, f"o que sabes sobre {DELETE_MARK}?")
    assert DELETE_MARK in _knowledge_text(stack, "fala-me da GTX 1660 Ti do meu PC")

    assert stack.forget(memory["id"])["ok"]

    for query in (f"o que sabes sobre {DELETE_MARK}?", "fala-me da GTX 1660 Ti do meu PC",
                  "o meu PC"):
        assert DELETE_MARK not in _context(stack, query), query
    assert DELETE_MARK not in _stored_graph_text(stack)
    # The nodes existed only because of that one memory.
    assert _titles(stack) == set()
    assert stack.knowledge.stats()["edges"] == 0


# ============================================================= B. ARCHIVE


def test_archiving_a_memory_removes_its_graph_derived_recall_but_keeps_the_memory(stack):
    memory = _remember(stack, f"O projeto Nano usa Groq e Ollama e o {ARCHIVE_MARK}.",
                       kind="project")
    assert ARCHIVE_MARK in _knowledge_text(stack, f"o projeto Nano e o {ARCHIVE_MARK}")

    assert stack.memories.update(memory["id"], status="archived")["ok"]

    assert ARCHIVE_MARK not in _context(stack, f"o projeto Nano e o {ARCHIVE_MARK}")
    assert ARCHIVE_MARK not in _context(stack, "que ferramentas usa o projeto Nano?")
    assert ARCHIVE_MARK not in _stored_graph_text(stack)
    # Archived is retained data, not deleted data: the Memória page still has it.
    kept = stack.memories.get(memory["id"])
    assert kept is not None and kept["status"] == "archived"


# ============================================================== D. DEMOTE


def test_demoting_a_memory_to_candidate_removes_its_graph_derived_recall(stack):
    memory = _remember(stack, f"Uso o Visual Studio Code {DEMOTE_MARK} todos os dias.",
                       kind="software")
    assert DEMOTE_MARK in _knowledge_text(stack, f"Visual Studio Code {DEMOTE_MARK}")

    assert stack.memories.update(memory["id"], status="candidate")["ok"]

    assert DEMOTE_MARK not in _context(stack, f"Visual Studio Code {DEMOTE_MARK}")
    assert DEMOTE_MARK not in _stored_graph_text(stack)
    assert "Visual Studio Code" not in _titles(stack)
    assert stack.memories.get(memory["id"])["status"] == "candidate"


def test_restoring_an_archived_memory_brings_its_graph_back(stack):
    """The other half of "inert": archiving is reversible, and so is its effect."""
    memory = _remember(stack, f"Uso o Visual Studio Code {DEMOTE_MARK} todos os dias.",
                       kind="software")
    stack.memories.update(memory["id"], status="archived")
    assert "Visual Studio Code" not in _titles(stack)

    stack.memories.update(memory["id"], status="active")
    assert "Visual Studio Code" in _titles(stack)
    assert DEMOTE_MARK in _knowledge_text(stack, f"Visual Studio Code {DEMOTE_MARK}")


# ================================================================ C. EDIT


def test_editing_a_memory_replaces_the_old_text_everywhere_and_derives_the_new(stack):
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {EDIT_OLD_MARK}.")
    assert EDIT_OLD_MARK in _knowledge_text(stack, f"GTX 1660 Ti {EDIT_OLD_MARK}")

    assert stack.memories.update(memory["id"],
                                 text=f"O meu PC tem uma RTX 5070 {EDIT_NEW_MARK}.")["ok"]

    for query in (f"GTX 1660 Ti {EDIT_OLD_MARK}", "que placa gráfica tem o meu PC?",
                  "o meu PC", f"{EDIT_NEW_MARK} RTX 5070"):
        assert EDIT_OLD_MARK not in _context(stack, query), query
    assert EDIT_OLD_MARK not in _stored_graph_text(stack)
    # Search may still return the memory -- the two markers share the tokens
    # "edit" and "marker" -- but only ever with its NEW text.
    assert not any(EDIT_OLD_MARK in hit["text"]
                   for hit in stack.memories.search(EDIT_OLD_MARK))
    # The old entity was supported by nothing else, so it is gone...
    assert "GTX 1660 Ti" not in _titles(stack)
    # ...and the new sentence is what the graph now says, end to end.
    assert "RTX 5070" in _titles(stack)
    assert EDIT_NEW_MARK in _knowledge_text(stack, f"RTX 5070 {EDIT_NEW_MARK}")
    assert _node(stack, "O meu PC")["summary"].endswith(f"{EDIT_NEW_MARK}.")


def test_editing_keeps_an_entity_that_another_memory_independently_supports(stack):
    edited = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {EDIT_OLD_MARK}.")
    independent = _remember(stack, "Tenho uma GTX 1660 Ti de reserva no armário.")

    stack.memories.update(edited["id"], text=f"O meu PC tem uma RTX 5070 {EDIT_NEW_MARK}.")

    gtx = _node(stack, "GTX 1660 Ti")
    assert gtx["summary"] == independent["text"]
    assert stack.knowledge.links_for(gtx["id"])["memory"] == [independent["id"]]
    assert EDIT_OLD_MARK not in _context(stack, "GTX 1660 Ti de reserva")
    assert EDIT_OLD_MARK not in _stored_graph_text(stack)
    assert "armário" in _knowledge_text(stack, "GTX 1660 Ti de reserva")


# ============================================================ E. CLEAR ALL


def test_clearing_all_memories_leaves_no_derived_recall(stack):
    first = f"O meu PC tem uma GTX 1660 Ti {CLEAR_MARKS[0]}."
    second = f"O projeto Atlas usa Groq e o {CLEAR_MARKS[1]}."
    _remember(stack, first)
    _remember(stack, second, kind="project")
    for marker in CLEAR_MARKS:
        assert marker in _knowledge_text(stack, f"o que sabes sobre {marker}?")

    assert stack.memories.clear()["ok"]

    for marker in CLEAR_MARKS:
        assert marker not in _context(stack, f"o que sabes sobre {marker}?")
        assert marker not in _stored_graph_text(stack)
    assert _titles(stack) == set()


@pytest.mark.parametrize("endpoint", ["clear_memories", "forget_all_memory_facts"])
def test_both_esquecer_tudo_buttons_forget_the_derived_graph(stack, monkeypatch, endpoint):
    """The Memória page and Definições each have an "Esquecer tudo". Both reach
    the backend through the real bridge function here, over this test's own
    database, and neither may leave a derived node behind."""
    import core.main as main

    monkeypatch.setattr(main, "memory_stack", stack)
    monkeypatch.setattr(main, "memory", stack.engine)
    _remember(stack, f"O meu PC tem uma GTX 1660 Ti {CLEAR_MARKS[0]}.")
    assert CLEAR_MARKS[0] in _knowledge_text(stack, CLEAR_MARKS[0])

    result = getattr(main, endpoint)()

    assert result["ok"], result
    assert CLEAR_MARKS[0] not in _context(stack, CLEAR_MARKS[0])
    assert CLEAR_MARKS[0] not in _stored_graph_text(stack)
    assert result["memory"]["knowledge"]["nodes"] == 0


# =================================================== preservation of evidence


def test_a_node_another_memory_still_supports_survives_without_the_deleted_text(stack):
    deleted = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    kept = _remember(stack, f"O meu PC tem um SSD Samsung e o {KEEP_MARK}.")
    pc = _node(stack, "O meu PC")
    assert set(stack.knowledge.links_for(pc["id"])["memory"]) == {deleted["id"], kept["id"]}

    stack.forget(deleted["id"])

    pc = _node(stack, "O meu PC")
    assert pc["summary"] == kept["text"]
    assert pc["mentionCount"] == 1
    assert stack.knowledge.links_for(pc["id"])["memory"] == [kept["id"]]
    assert KEEP_MARK in _knowledge_text(stack, "o meu PC e o SSD Samsung")
    assert DELETE_MARK not in _context(stack, "o meu PC e o SSD Samsung")
    assert "GTX 1660 Ti" not in _titles(stack)


def test_an_edge_another_memory_still_supports_survives_at_its_honest_weight(stack):
    deleted = _remember(stack, f"O projeto Nano usa Groq e o {DELETE_MARK}.", kind="project")
    _remember(stack, "O projeto Nano usa Groq.", kind="project")
    assert _edge(stack, "Projeto Nano", "uses", "Groq") == 1.5  # two memories

    stack.forget(deleted["id"])

    assert _edge(stack, "Projeto Nano", "uses", "Groq") == 1.0
    assert DELETE_MARK not in _titles(stack)


def test_an_edge_whose_only_evidence_was_forgotten_is_removed_between_surviving_nodes(stack):
    """Both ends survive on other evidence; the CONNECTION does not."""
    connecting = _remember(stack, "O projeto Nano usa Groq.", kind="project")
    _remember(stack, "O projeto Nano usa Ollama.", kind="project")
    _remember(stack, "Uso o Groq para respostas rápidas.", kind="software")
    assert _edge(stack, "Projeto Nano", "uses", "Groq") is not None

    stack.forget(connecting["id"])

    assert "Projeto Nano" in _titles(stack) and "Groq" in _titles(stack)
    assert _edge(stack, "Projeto Nano", "uses", "Groq") is None


# ================================================================ manual data


def test_a_manual_node_survives_forgetting_and_keeps_its_own_summary(stack):
    manual = stack.knowledge.upsert_node("Projeto Atlas", node_type="project",
                                         summary=f"As minhas notas {MANUAL_MARK}.",
                                         origin="manual", bump=False)
    memory = _remember(stack, f"O projeto Atlas usa Groq e o {DELETE_MARK}.", kind="project")

    # The memory names the same entity: the node gains evidence, and its
    # summary -- which the user typed -- is not overwritten with the memory.
    node = _node(stack, "Projeto Atlas")
    assert node["id"] == manual["id"]
    assert node["summary"] == f"As minhas notas {MANUAL_MARK}."
    assert memory["id"] in stack.knowledge.links_for(node["id"])["memory"]

    stack.forget(memory["id"])

    node = _node(stack, "Projeto Atlas")
    assert node["id"] == manual["id"]
    assert node["origin"] == "manual"
    assert node["summary"] == f"As minhas notas {MANUAL_MARK}."
    assert stack.knowledge.links_for(node["id"])["memory"] == []
    assert DELETE_MARK not in _context(stack, "o projeto Atlas")
    assert MANUAL_MARK in _knowledge_text(stack, "o projeto Atlas")


def test_a_manual_edge_survives_reconciliation_and_restart(db_path):
    engine, built = _open()
    try:
        built.ensure_active()
        _remember(built, "O meu PC tem uma GTX 1660 Ti.")
        pc, gtx = _node(built, "O meu PC"), _node(built, "GTX 1660 Ti")
        assert built.knowledge.link(gtx["id"], pc["id"], relation="part_of")["ok"]
        built.reconcile_knowledge()
        assert _edge(built, "GTX 1660 Ti", "part_of", "O meu PC") is not None
    finally:
        _close(engine, built)

    engine, built = _open()
    try:
        assert _edge(built, "GTX 1660 Ti", "part_of", "O meu PC") is not None
    finally:
        _close(engine, built)


def test_editing_a_derived_node_summary_makes_it_the_users(stack):
    """The Second Brain's "Editar" on a derived node. What the user typed is
    theirs: forgetting the memory removes the memory's sentence, not the note."""
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    gtx = _node(stack, "GTX 1660 Ti")
    assert stack.knowledge.update_node(gtx["id"],
                                       summary=f"Comprada em 2021 {MANUAL_MARK}.")["ok"]

    stack.forget(memory["id"])

    node = _node(stack, "GTX 1660 Ti")
    assert node["origin"] == "manual"
    assert node["summary"] == f"Comprada em 2021 {MANUAL_MARK}."
    assert DELETE_MARK not in _context(stack, "GTX 1660 Ti")
    assert DELETE_MARK not in _stored_graph_text(stack)


def test_saving_a_derived_summary_unchanged_does_not_adopt_the_node(stack):
    """Opening "Editar" and pressing "Guardar" is not authorship. Treating it
    as authorship would turn the memory's sentence into the user's own note,
    and it would then outlive the memory."""
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    gtx = _node(stack, "GTX 1660 Ti")
    assert stack.knowledge.update_node(gtx["id"], summary=gtx["summary"])["ok"]
    assert _node(stack, "GTX 1660 Ti")["origin"] == "derived"

    stack.forget(memory["id"])

    assert "GTX 1660 Ti" not in _titles(stack)
    assert DELETE_MARK not in _stored_graph_text(stack)


def test_creating_by_hand_a_node_a_memory_derived_adopts_it_without_the_sentence(stack):
    """"Criar nó" with the name of a node a memory already made: the node is now
    the user's, and it does not inherit the memory's sentence as their text."""
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    derived = _node(stack, "GTX 1660 Ti")
    adopted = stack.knowledge.upsert_node("GTX 1660 Ti", node_type="device",
                                          origin="manual", bump=False)
    assert adopted["id"] == derived["id"]
    assert adopted["origin"] == "manual" and adopted["summary"] == ""

    stack.forget(memory["id"])

    assert _node(stack, "GTX 1660 Ti")["id"] == derived["id"]
    assert DELETE_MARK not in _stored_graph_text(stack)
    assert DELETE_MARK not in _context(stack, "GTX 1660 Ti")


def test_a_derived_edge_the_user_removed_stays_removed(stack):
    _remember(stack, "O meu PC tem uma GTX 1660 Ti.")
    pc, gtx = _node(stack, "O meu PC"), _node(stack, "GTX 1660 Ti")
    assert stack.knowledge.unlink(pc["id"], gtx["id"], "has")["removed"] == 1

    stack.reconcile_knowledge()

    assert _edge(stack, "O meu PC", "has", "GTX 1660 Ti") is None
    # The nodes were not what the user removed.
    assert {"O meu PC", "GTX 1660 Ti"} <= _titles(stack)


def test_renaming_a_derived_node_does_not_bring_the_old_name_back(stack):
    _remember(stack, "O meu PC tem uma GTX 1660 Ti.")
    gtx = _node(stack, "GTX 1660 Ti")
    assert stack.knowledge.update_node(gtx["id"], title="Placa gráfica antiga")["ok"]

    stack.reconcile_knowledge()

    titles = _titles(stack)
    assert "Placa gráfica antiga" in titles
    assert "GTX 1660 Ti" not in titles, "the old name was derived again beside the rename"
    assert _node(stack, "Placa gráfica antiga")["origin"] == "manual"


def test_collapsing_a_mirrored_pair_keeps_the_users_edge(stack):
    """A derived edge and a twin the user drew pointing the other way. The
    survivor must be the user's: a derived survivor would be deleted the moment
    the memory behind it is forgotten, taking the user's edge with it."""
    stack.knowledge.upsert_node("Groq", node_type="software")
    stack.knowledge.upsert_node("Ollama", node_type="software")
    memory = _remember(stack, "Uso Groq e Ollama.", kind="software")
    (edge,) = stack.knowledge.graph()["edges"]
    stack.conn.execute(
        "INSERT INTO knowledge_edges (id, source_id, target_id, relation, weight,"
        " created_at, updated_at, origin) VALUES (?,?,?,?,?,?,?,?)",
        ("edge_user_twin", edge["target"], edge["source"], "related_to", 1.0,
         _stamp(), _stamp(), "manual"))
    stack.conn.commit()

    stack.reconcile_knowledge()
    assert stack.conn.execute("SELECT origin FROM knowledge_edges").fetchall() == [("manual",)]

    stack.forget(memory["id"])
    assert _edge(stack, "Groq", "related_to", "Ollama") is not None


# ================================================= long-term memory switched off


def test_forgetting_still_works_with_long_term_memory_switched_off(stack):
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    stack.long_term_enabled = False

    assert stack.forget(memory["id"])["ok"]

    assert _titles(stack) == set()
    assert DELETE_MARK not in _stored_graph_text(stack)


def test_with_long_term_memory_off_reconciliation_builds_nothing_new(stack):
    """Off means Nano builds no memory-derived state. It does not mean that
    forgetting stops -- that is the test above -- so the halves are separate."""
    listener, stack.memories.on_change = stack.memories.on_change, None
    stack.memories.remember("O meu PC tem uma GTX 1660 Ti.", kind="hardware")
    stack.memories.on_change = listener
    stack.long_term_enabled = False

    assert stack.reconcile_knowledge() == (0, 0)
    assert _titles(stack) == set()

    stack.long_term_enabled = True
    stack.reconcile_knowledge()
    assert {"O meu PC", "GTX 1660 Ti"} <= _titles(stack)


# ========================================================== conversation links


def test_deleting_the_source_conversation_is_not_undone_by_reconciliation(stack):
    thread = stack.new_conversation()
    memory = _remember(stack, "O meu PC tem uma GTX 1660 Ti.")
    gtx = _node(stack, "GTX 1660 Ti")
    assert stack.knowledge.links_for(gtx["id"])["conversation"] == [thread["id"]]

    stack.delete_conversation(thread["id"])
    stack.reconcile_knowledge()

    assert stack.knowledge.links_for(gtx["id"])["conversation"] == []
    # The memory itself outlives its conversation, as it always has.
    assert stack.memories.get(memory["id"]) is not None
    assert "GTX 1660 Ti" in _context(stack, "a GTX 1660 Ti do meu PC")


# ================================================== F. queued (real) worker


class _Gate:
    """Parks the memory worker inside a job until the test releases it.

    With ONE worker draining a FIFO queue, anything queued while the gate is
    shut is guaranteed to run after the test's own actions. No sleeps.
    """

    def __init__(self, stack):
        self.entered = threading.Event()
        self.release = threading.Event()
        stack._defer(self._park)
        assert self.entered.wait(10), "the worker never reached the gate"

    def _park(self):
        self.entered.set()
        self.release.wait(10)


def _drain(stack):
    """Wait until everything queued so far has run, by queueing one more job."""
    done = threading.Event()
    stack._defer(done.set)
    assert done.wait(10), "the memory worker did not finish its queue"


@pytest.fixture
def worker(db_path):
    """A stack running the REAL background worker, as production constructs it.

    Yields the stack and a ``park`` function that shuts a gate in front of the
    worker. Every gate is opened again on the way out, so a failing assertion
    cannot leave the worker parked inside a closed database.
    """
    engine, built = _open(background=True)
    gates: list[_Gate] = []

    def park() -> _Gate:
        gate = _Gate(built)
        gates.append(gate)
        return gate

    built.ensure_active()
    try:
        yield built, park
    finally:
        for gate in gates:
            gate.release.set()
        _close(engine, built)


def test_a_queued_promotion_of_a_memory_deleted_before_it_ran_creates_nothing(worker):
    built, park = worker
    assert built._background is True and built._worker.is_alive()
    gate = park()
    memory = _remember(built, f"O meu PC tem uma GTX 1660 Ti {QUEUED_MARK}.")
    assert _titles(built) == set(), "the promotion ran before the gate opened"

    assert built.forget(memory["id"])["ok"]
    gate.release.set()
    _drain(built)

    assert _titles(built) == set()
    assert QUEUED_MARK not in _stored_graph_text(built)
    assert QUEUED_MARK not in _context(built, f"o que sabes sobre {QUEUED_MARK}?")


def test_a_queued_promotion_of_a_memory_archived_before_it_ran_creates_nothing(worker):
    built, park = worker
    gate = park()
    memory = _remember(built, f"O meu PC tem uma GTX 1660 Ti {QUEUED_MARK}.")

    assert built.memories.update(memory["id"], status="archived")["ok"]
    gate.release.set()
    _drain(built)

    assert _titles(built) == set()
    assert QUEUED_MARK not in _stored_graph_text(built)


def test_a_queued_promotion_derives_from_the_text_as_it_is_when_it_runs(worker):
    built, park = worker
    gate = park()
    memory = _remember(built, f"O meu PC tem uma GTX 1660 Ti {EDIT_OLD_MARK}.")

    built.memories.update(memory["id"], text=f"O meu PC tem uma RTX 5070 {EDIT_NEW_MARK}.")
    gate.release.set()
    _drain(built)

    assert "RTX 5070" in _titles(built)
    assert "GTX 1660 Ti" not in _titles(built)
    assert EDIT_OLD_MARK not in _stored_graph_text(built)
    assert EDIT_OLD_MARK not in _context(built, "que placa tem o meu PC?")


def test_the_worker_promotes_a_memory_that_is_still_active(worker):
    """The guard must not turn every queued promotion into a no-op."""
    built, park = worker
    gate = park()
    _remember(built, f"O meu PC tem uma GTX 1660 Ti {QUEUED_MARK}.")
    assert _titles(built) == set()

    gate.release.set()
    _drain(built)

    assert {"O meu PC", "GTX 1660 Ti"} <= _titles(built)
    assert QUEUED_MARK in _knowledge_text(built, f"o que sabes sobre {QUEUED_MARK}?")


# ================================================== G. restart / reconcile


def test_restarting_does_not_resurrect_a_node_the_user_deleted(db_path):
    engine, built = _open()
    try:
        built.ensure_active()
        _remember(built, "O meu PC tem uma GTX 1660 Ti.")
        assert built.knowledge.delete_node(_node(built, "GTX 1660 Ti")["id"])["ok"]
        built.reconcile_knowledge()
        assert "GTX 1660 Ti" not in _titles(built)
    finally:
        _close(engine, built)

    engine, built = _open()
    try:
        assert "GTX 1660 Ti" not in _titles(built), "a restart re-created a deleted node"
        assert "O meu PC" in _titles(built), "the deletion took unrelated evidence with it"
    finally:
        _close(engine, built)


def test_new_evidence_after_a_node_was_deleted_may_create_it_again(stack):
    """A deletion suppresses the evidence the user removed, not the entity
    forever: telling Nano something NEW about it is new evidence."""
    _remember(stack, "O meu PC tem uma GTX 1660 Ti.")
    stack.knowledge.delete_node(_node(stack, "GTX 1660 Ti")["id"])

    _remember(stack, "Tenho uma GTX 1660 Ti de reserva no armário.")

    gtx = _node(stack, "GTX 1660 Ti")
    assert len(stack.knowledge.links_for(gtx["id"])["memory"]) == 1


def test_restarting_does_not_inflate_mention_counts_or_edge_weights(db_path):
    engine, built = _open()
    try:
        built.ensure_active()
        _remember(built, "O meu PC tem uma GTX 1660 Ti.")
        before = (_node(built, "GTX 1660 Ti")["mentionCount"],
                  _edge(built, "O meu PC", "has", "GTX 1660 Ti"))
    finally:
        _close(engine, built)
    assert before == (1, 1.0)

    for _ in range(3):
        engine, built = _open()
        try:
            after = (_node(built, "GTX 1660 Ti")["mentionCount"],
                     _edge(built, "O meu PC", "has", "GTX 1660 Ti"))
        finally:
            _close(engine, built)
        assert after == before, "a restart counted the same evidence again"


def test_reconciling_twice_leaves_the_database_byte_for_byte_identical(stack):
    _remember(stack, "O meu PC tem uma GTX 1660 Ti.")
    _remember(stack, "O projeto Nano usa Groq e Ollama.", kind="project")
    _remember(stack, "Tenho uma GTX 1660 Ti de reserva no armário.")
    stack.reconcile_knowledge()
    first = _graph_state(stack)

    stack.reconcile_knowledge()

    assert _graph_state(stack) == first


def test_restarting_after_a_delete_does_not_bring_the_forgotten_text_back(db_path):
    engine, built = _open()
    try:
        built.ensure_active()
        memory = _remember(built, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
        built.forget(memory["id"])
    finally:
        _close(engine, built)

    engine, built = _open()
    try:
        built.ensure_active()
        assert DELETE_MARK not in _context(built, f"o que sabes sobre {DELETE_MARK}?")
        assert DELETE_MARK not in _stored_graph_text(built)
        assert _titles(built) == set()
    finally:
        _close(engine, built)


# ========================================================= index consistency


def test_no_node_index_entry_survives_its_node_or_keeps_forgotten_text(stack):
    deleted = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    archived = _remember(stack, f"Uso o Visual Studio Code e o {ARCHIVE_MARK} todos os dias.",
                         kind="software")
    edited = _remember(stack, f"O projeto Atlas usa Groq e o {EDIT_OLD_MARK}.", kind="project")

    stack.forget(deleted["id"])
    stack.memories.update(archived["id"], status="archived")
    stack.memories.update(edited["id"], text=f"O projeto Atlas usa Ollama e o {EDIT_NEW_MARK}.")

    for marker in (DELETE_MARK, ARCHIVE_MARK, EDIT_OLD_MARK):
        # The markers share tokens ("marker", "edit"), so a search may return
        # the SURVIVING node; what it may never return is the forgotten text.
        for node in stack.knowledge.search(marker):
            assert marker not in f"{node['title']} {node['summary']} {node['body']}", marker
        for hit in stack.index.search(marker):
            assert marker not in f"{hit.title} {hit.body}", marker
    node_ids = {node["id"] for node in stack.knowledge.list_nodes(limit=300)}
    indexed = {row[0].split(":", 1)[1] for row in stack.conn.execute(
        "SELECT entry_id FROM retrieval_entries WHERE kind='node'")}
    assert indexed == node_ids
    assert EDIT_NEW_MARK in _stored_graph_text(stack)


# ===================================================== the prompt boundary


def test_forgotten_text_never_reaches_the_system_prompt(stack):
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    query = "o que sabes sobre a GTX 1660 Ti do meu PC?"
    assert DELETE_MARK in _system_prompt(stack, query)

    stack.forget(memory["id"])

    assert DELETE_MARK not in _system_prompt(stack, query)


def _provider_brain(stack, monkeypatch, *, mode, preferred):
    """The real Brain and composer, with recording fakes in place of transports.

    Nothing about routing or prompt assembly is stubbed: the system prompt the
    fake receives is the one ``_build_system_prompt`` really produced from this
    stack. Only who is on the other end of the wire is fake.
    """
    from core import model_selection
    from core.brain import Brain
    from core.guardrails import GuardrailsEngine
    from tests.test_multi_provider import (READY, SETUP, FakeGoogleClient,
                                           FakeMistralClient, RecordingExecutor,
                                           cloud, google_text, local)
    from tests.test_provider_fallback import (FakeGroqClient, FakeOllamaClient,
                                              _Chunk, local_text)

    brain = Brain(api_key="test-key", guardrails=GuardrailsEngine(), memory=stack.engine,
                  config={"provider_mode": mode, "preferred_cloud": preferred,
                          "google_fast_model": "gemini-test-fast",
                          "google_complex_model": "gemini-test-strong",
                          "local": {"enabled": True, "model": "qwen3:8b"}},
                  tool_executor=RecordingExecutor(), memory_stack=stack)
    brain.provider_mode = mode
    brain.preferred_cloud = preferred
    brain.client = FakeGroqClient([[_Chunk("ok groq")], [_Chunk("ok groq")]])
    brain.google_client = FakeGoogleClient([google_text("ok google"),
                                            google_text("ok google")])
    brain.google_enabled = True
    brain.mistral_client = FakeMistralClient([])
    brain.mistral_enabled = False
    ollama = FakeOllamaClient([local_text("ok local"), local_text("ok local")])
    monkeypatch.setattr(brain, "_local_http_client", lambda **kw: ollama)

    async def _describe(_mode):
        return ({"google": cloud("google", READY, "gemini-test-fast", "gemini-test-strong"),
                 "groq": cloud("groq", READY, "openai/gpt-oss-20b", "openai/gpt-oss-120b"),
                 "mistral": cloud("mistral", SETUP, "mistral-test-fast")}, local(READY))

    monkeypatch.setattr(brain, "_describe_providers_async", _describe)
    monkeypatch.setattr(model_selection, "select_tools", lambda *a, **k: [])
    monkeypatch.setattr(model_selection, "classify",
                        lambda *_a, **_k: model_selection.TaskClass("ACTION"))
    return brain, ollama


def _received(brain, ollama, provider):
    """Everything the named provider was sent, as one string."""
    if provider == "groq":
        return json.dumps(brain.client.calls, ensure_ascii=False, default=str)
    if provider == "google":
        return json.dumps(brain.google_client.bodies, ensure_ascii=False, default=str)
    return json.dumps(ollama.requests, ensure_ascii=False, default=str)


def _withdraw(stack, memory, action):
    """Take one memory out of recall, the way the Memória page does."""
    if action == "delete":
        assert stack.forget(memory["id"])["ok"]
    elif action in ("archived", "candidate"):
        assert stack.memories.update(memory["id"], status=action)["ok"]
    elif action == "edit":
        assert stack.memories.update(memory["id"], text="O meu PC tem uma RTX 5070.")["ok"]
    elif action == "clear":
        assert stack.memories.clear()["ok"]
    else:  # pragma: no cover - a typo in the parametrisation
        raise AssertionError(action)


@pytest.mark.parametrize("action", ["delete", "archived", "candidate", "edit", "clear"])
@pytest.mark.parametrize("mode,preferred,provider", [
    ("LOCAL", "groq", "ollama"),
    ("CLOUD", "groq", "groq"),
    ("AUTO", "google", "google"),
])
def test_forgotten_text_never_reaches_any_provider_in_any_mode(stack, monkeypatch, action,
                                                               mode, preferred, provider):
    """The request the provider actually received, before and after.

    Every way the Memória page takes a memory out of recall, in each mode,
    against the provider that mode really calls -- the local model in LOCAL,
    the preferred cloud in CLOUD and AUTO.
    """
    from core import provider_failures

    provider_failures.reset_all_cooldowns()
    memory = _remember(stack, f"O meu PC tem uma GTX 1660 Ti {DELETE_MARK}.")
    query = "o que sabes sobre a GTX 1660 Ti do meu PC?"

    async def _turn(brain):
        return "".join([chunk async for chunk in brain.chat(query, stream=True)])

    try:
        brain, ollama = _provider_brain(stack, monkeypatch, mode=mode, preferred=preferred)
        asyncio.run(_turn(brain))
        assert brain.last_provider_used == provider
        assert DELETE_MARK in _received(brain, ollama, provider), (
            "precondition: the provider never received the memory in the first place")

        _withdraw(stack, memory, action)

        brain, ollama = _provider_brain(stack, monkeypatch, mode=mode, preferred=preferred)
        asyncio.run(_turn(brain))
        assert brain.last_provider_used == provider
        assert DELETE_MARK not in _received(brain, ollama, provider)
    finally:
        provider_failures.reset_all_cooldowns()


# ================================================= existing-install repair


def _v2_database():
    """A database exactly as 0.2.0-beta.1 left it: schema v2, its own DDL.

    The stale states below are written as rows, because that is what the
    user's file contains; the code that wrote them no longer exists. The FTS
    table is created by the same RetrievalIndex that build ran, so a legacy
    node is findable through full-text search exactly as it was there.
    """
    engine = MemoryEngine()
    with engine.conn:
        memory_schema._apply_v2(engine.conn)
        engine.conn.execute("PRAGMA user_version = 2")
    engine.legacy_index = RetrievalIndex(engine.conn, engine._lock)
    return engine


def _stamp():
    return "2026-09-01T10:00:00+00:00"


def _legacy_memory(conn, text, *, kind="hardware", status="active"):
    memory_id = f"mem_{uuid.uuid4().hex[:20]}"
    conn.execute(
        "INSERT INTO memories (id, text, normalized, kind, origin, trust, status,"
        " confidence, importance, pinned, tags, created_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (memory_id, text, text_normalize.normalize(text), kind, "explicit", "USER",
         status, 0.9, 3, 0, "[]", _stamp(), _stamp()))
    return memory_id


def _legacy_node(engine, title, *, node_type="device", summary="", origin="derived",
                 mentions=1):
    node_id = f"node_{uuid.uuid4().hex[:20]}"
    engine.conn.execute(
        "INSERT INTO knowledge_nodes (id, slug, title, type, summary, body, tags, pinned,"
        " mention_count, origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (node_id, text_normalize.slugify(title), title, node_type, summary, "", "[]", 0,
         mentions, origin, _stamp(), _stamp()))
    # The old build indexed every node it wrote, summary included.
    engine.legacy_index.upsert(f"node:{node_id}", kind="node", title=title,
                               body=summary or title, created_at=_stamp(),
                               metadata={"nodeId": node_id, "type": node_type})
    return node_id


def _legacy_link(conn, node_id, memory_id):
    conn.execute(
        "INSERT INTO knowledge_links (id, node_id, kind, ref_id, created_at)"
        " VALUES (?,?,?,?,?)",
        (f"link_{uuid.uuid4().hex[:20]}", node_id, "memory", memory_id, _stamp()))


def _legacy_edge(conn, source_id, target_id, relation, weight):
    conn.execute(
        "INSERT INTO knowledge_edges (id, source_id, target_id, relation, weight,"
        " created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (f"edge_{uuid.uuid4().hex[:20]}", source_id, target_id, relation, weight,
         _stamp(), _stamp()))


def test_upgrading_a_v2_database_removes_a_ghost_node_left_by_a_deleted_memory(db_path):
    engine = _v2_database()
    ghost_text = f"O meu PC tem uma GTX 1660 Ti {LEGACY_MARK}."
    with engine.conn:
        # The memory was deleted by the old build, which removed the memory and
        # its links -- and left the nodes, their summaries and their index rows.
        pc = _legacy_node(engine, "O meu PC", summary=ghost_text, mentions=4)
        gtx = _legacy_node(engine, "GTX 1660 Ti", summary=ghost_text, mentions=4)
        _legacy_edge(engine.conn, pc, gtx, "has", 2.5)
    engine.close()

    engine, built = _open()
    try:
        built.ensure_active()
        assert built.migration["ok"], built.migration
        assert LEGACY_MARK not in _context(built, "fala-me da GTX 1660 Ti do meu PC")
        assert LEGACY_MARK not in _stored_graph_text(built)
        assert _titles(built) == set()
        assert built.knowledge.stats()["edges"] == 0
    finally:
        _close(engine, built)


def test_upgrading_keeps_a_node_with_remaining_evidence_and_rewrites_its_summary(db_path):
    engine = _v2_database()
    kept_text = "Tenho uma GTX 1660 Ti de reserva no armário."
    with engine.conn:
        kept = _legacy_memory(engine.conn, kept_text)
        archived = _legacy_memory(engine.conn, f"O meu PC tem uma GTX 1660 Ti {LEGACY_MARK}.",
                                  status="archived")
        # Two pieces of evidence once; the summary still holds the archived one.
        gtx = _legacy_node(engine, "GTX 1660 Ti",
                           summary=f"O meu PC tem uma GTX 1660 Ti {LEGACY_MARK}.", mentions=9)
        _legacy_link(engine.conn, gtx, kept)
        _legacy_link(engine.conn, gtx, archived)
    engine.close()

    engine, built = _open()
    try:
        built.ensure_active()
        node = _node(built, "GTX 1660 Ti")
        assert node["id"] == gtx
        assert node["summary"] == kept_text
        assert node["mentionCount"] == 1
        assert built.knowledge.links_for(gtx)["memory"] == [kept]
        assert LEGACY_MARK not in _stored_graph_text(built)
        assert LEGACY_MARK not in _context(built, "GTX 1660 Ti de reserva")
    finally:
        _close(engine, built)


def test_upgrading_preserves_manual_nodes_and_clears_only_copied_summaries(db_path):
    engine = _v2_database()
    copied = f"O projeto Atlas usa Groq e o {LEGACY_MARK}."
    with engine.conn:
        memory = _legacy_memory(engine.conn, copied, kind="project", status="archived")
        # The old promotion overwrote this manual node's summary with the memory.
        overwritten = _legacy_node(engine, "Projeto Atlas", node_type="project",
                                   summary=copied, origin="manual")
        _legacy_link(engine.conn, overwritten, memory)
        # A manual node the user wrote, untouched by any memory.
        own = _legacy_node(engine, "Receitas", node_type="topic",
                           summary=f"As minhas receitas {MANUAL_MARK}.", origin="manual")
    engine.close()

    engine, built = _open()
    try:
        built.ensure_active()
        atlas = _node(built, "Projeto Atlas")
        assert atlas["id"] == overwritten and atlas["origin"] == "manual"
        assert atlas["summary"] == ""
        recipes = _node(built, "Receitas")
        assert recipes["id"] == own
        assert recipes["summary"] == f"As minhas receitas {MANUAL_MARK}."
        assert LEGACY_MARK not in _stored_graph_text(built)
        assert LEGACY_MARK not in _context(built, "o projeto Atlas")
    finally:
        _close(engine, built)


def test_upgrading_resets_inflated_counts_and_weights_to_the_evidence(db_path):
    engine = _v2_database()
    text = "O meu PC tem uma GTX 1660 Ti."
    with engine.conn:
        memory = _legacy_memory(engine.conn, text)
        pc = _legacy_node(engine, "O meu PC", summary=text, mentions=12)
        gtx = _legacy_node(engine, "GTX 1660 Ti", summary=text, mentions=12)
        _legacy_link(engine.conn, pc, memory)
        _legacy_link(engine.conn, gtx, memory)
        # Eleven launches, each adding 0.5 to the one edge one memory supports.
        _legacy_edge(engine.conn, pc, gtx, "has", 6.5)
    engine.close()

    engine, built = _open()
    try:
        assert _node(built, "O meu PC")["mentionCount"] == 1
        assert _node(built, "GTX 1660 Ti")["mentionCount"] == 1
        assert _edge(built, "O meu PC", "has", "GTX 1660 Ti") == 1.0
    finally:
        _close(engine, built)


def test_upgrading_with_long_term_memory_off_still_removes_ghosts(db_path):
    """The repair is forgetting, not recall: the setting does not defer it."""
    engine = _v2_database()
    with engine.conn:
        _legacy_node(engine, "GTX 1660 Ti", summary=f"O meu PC tem uma GTX 1660 Ti {LEGACY_MARK}.")
    engine.close()

    engine = MemoryEngine()
    built = MemoryStack(engine, background=False, long_term_enabled=False)
    try:
        assert _titles(built) == set()
        assert LEGACY_MARK not in _stored_graph_text(built)
    finally:
        _close(engine, built)


# ============================================================ schema v3 itself


def test_a_fresh_database_starts_at_v3_with_provenance(stack):
    assert memory_schema.current_version(stack.conn) == memory_schema.SCHEMA_VERSION == 3
    columns = {row[1] for row in stack.conn.execute("PRAGMA table_info(knowledge_edges)")}
    assert "origin" in columns
    assert stack.conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='knowledge_suppressions'"
    ).fetchone() is not None


def test_reopening_a_current_database_writes_nothing(db_path):
    """The steady state. A consistent v3 database gives the startup pass
    nothing to do, and it must not touch a row, a timestamp or an index entry."""
    engine, built = _open()
    try:
        built.ensure_active()
        _remember(built, "O meu PC tem uma GTX 1660 Ti.")
        _remember(built, "O projeto Nano usa Groq e Ollama.", kind="project")
        built.knowledge.upsert_node("Receitas", node_type="topic",
                                    summary="As minhas receitas.")
        before = _graph_state(built)
    finally:
        _close(engine, built)

    engine, built = _open()
    try:
        assert built.migration["applied"] == []
        assert _graph_state(built) == before
    finally:
        _close(engine, built)


def _stale_v2_with_a_copied_manual_summary():
    engine = _v2_database()
    copied = f"O projeto Atlas usa Groq e o {LEGACY_MARK}."
    with engine.conn:
        # Archived: the copy outlived the user's decision to stop using it.
        memory = _legacy_memory(engine.conn, copied, kind="project", status="archived")
        atlas = _legacy_node(engine, "Projeto Atlas", node_type="project", summary=copied,
                             origin="manual")
        _legacy_link(engine.conn, atlas, memory)
        _legacy_node(engine, "GTX 1660 Ti", summary="O meu PC tem uma GTX 1660 Ti.")
    return engine


def test_the_v3_upgrade_runs_once_and_a_second_pass_changes_nothing(db_path):
    _stale_v2_with_a_copied_manual_summary().close()

    engine, built = _open()
    try:
        assert [step["version"] for step in built.migration["applied"]] == [3]
        assert built.migration["applied"][0]["manual_summaries_cleared"] == 1
        first = _graph_state(built)
        again = memory_schema.apply(built.conn)
        assert again["ok"] and again["applied"] == []
        built.reconcile_knowledge()
        assert _graph_state(built) == first
    finally:
        _close(engine, built)

    engine, built = _open()
    try:
        assert built.migration["applied"] == []
        assert _graph_state(built) == first
        assert LEGACY_MARK not in _stored_graph_text(built)
    finally:
        _close(engine, built)


def test_a_v3_upgrade_that_fails_part_way_is_retried_and_converges(db_path, monkeypatch):
    engine = _stale_v2_with_a_copied_manual_summary()
    original = memory_schema._clear_copied_manual_summaries

    def fail(_conn):
        raise sqlite3.OperationalError("disk I/O error")

    # Restored with setattr, never with monkeypatch.undo(): undo would also
    # revert the DB_PATH redirection this test's database depends on.
    monkeypatch.setattr(memory_schema, "_clear_copied_manual_summaries", fail)
    failed = memory_schema.apply(engine.conn)
    assert failed["ok"] is False and failed["error"].startswith("v3")
    assert memory_schema.current_version(engine.conn) == 2

    monkeypatch.setattr(memory_schema, "_clear_copied_manual_summaries", original)
    retried = memory_schema.apply(engine.conn)
    assert retried["ok"], retried
    assert [step["version"] for step in retried["applied"]] == [3]
    engine.close()

    engine, built = _open()
    try:
        assert _node(built, "Projeto Atlas")["summary"] == ""
        assert "GTX 1660 Ti" not in _titles(built)
        assert LEGACY_MARK not in _stored_graph_text(built)
    finally:
        _close(engine, built)
