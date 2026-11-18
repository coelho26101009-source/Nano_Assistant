"""One-click "Apagar todos os dados": what it erases, what it keeps, and who can call it.

HOW THE CONTRACT IS ASSERTED
----------------------------
Every test plants unique markers in each store first and proves they are really
there, so a final "the marker is gone" cannot pass vacuously. The erase is then
checked where it matters: the composed context the model would receive, every
row of every personal table, and -- because a DELETE only marks pages free --
the raw bytes of the database file itself.

Each test builds a real MemoryStack over a real SQLite file in ``tmp_path``.
The user's database is never involved: ``core.memory.DB_PATH`` is redirected
before any engine is constructed, and tests/conftest.py isolates the profile.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import pytest

import core.memory as memory_module
from core.memory import MemoryEngine
from core.memory_stack import MemoryStack
from core.task_engine import TaskEngine

ROOT = Path(__file__).resolve().parent.parent

CHAT_MARK = "ERASE_CHAT_MARKER_5A1C"
MEMORY_MARK = "ERASE_MEMORY_MARKER_9D3E"
FACT_MARK = "ERASE_FACT_MARKER_2B7F"
NODE_MARK = "ERASE_NODE_MARKER_6C40"
DOC_MARK = "ERASE_DOC_MARKER_8E21"
PROFILE_MARK = "ERASE_PROFILE_MARKER_4F02"
ALL_MARKS = (CHAT_MARK, MEMORY_MARK, FACT_MARK, NODE_MARK, DOC_MARK, PROFILE_MARK)

PERSONAL_TABLES = ("conversations", "messages", "conversation_summaries",
                   "conversation_facts", "memories", "knowledge_nodes",
                   "knowledge_edges", "knowledge_links", "knowledge_suppressions",
                   "retrieval_entries")


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


@pytest.fixture
def tasks(tmp_path):
    return TaskEngine(db_path=tmp_path / "nano_tasks.db")


@pytest.fixture
def bridge(stack, tasks, monkeypatch):
    """core.main's real endpoint, pointed at this test's own stores."""
    import core.main as main

    monkeypatch.setattr(main, "memory_stack", stack)
    monkeypatch.setattr(main, "memory", stack.engine)
    monkeypatch.setattr(main, "task_engine", tasks)
    monkeypatch.setattr(main.brain, "memory_stack", stack, raising=False)
    return main


# ====================================================================== helpers


def _plant_everything(stack, *, threads: int = 2) -> None:
    for index in range(threads):
        thread = stack.new_conversation(f"Conversa {index}")
        stack.record_user_message(f"uma pergunta sobre o {CHAT_MARK} numero {index}",
                                  conversation_id=thread["id"])
        stack.record_assistant_message(f"uma resposta sobre o {CHAT_MARK} numero {index}",
                                       conversation_id=thread["id"])
    saved = stack.remember(f"O meu PC tem uma GTX 1660 Ti {MEMORY_MARK}.", kind="hardware")
    assert saved["ok"], saved
    node = stack.knowledge.upsert_node(f"Nota {NODE_MARK}", node_type="topic",
                                       summary=f"escrita à mão {NODE_MARK}",
                                       origin="manual", bump=False)
    assert node is not None
    stack.engine.set_fact("cor favorita", f"azul {FACT_MARK}")
    stack.engine.remember_preference("alcunha", PROFILE_MARK)
    stack.engine.index_document("doc1", f"um documento indexado {DOC_MARK}")


def _all_personal_text(stack) -> str:
    parts: list[str] = []
    for table in PERSONAL_TABLES:
        for row in stack.conn.execute(f"SELECT * FROM {table}"):
            parts.extend(str(value) for value in row)
    for row in stack.conn.execute("SELECT key, value FROM preferences"):
        parts.extend(str(value) for value in row)
    for name in ("retrieval_fts", "documents_fts"):
        if stack.conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone():
            for row in stack.conn.execute(f"SELECT * FROM {name}"):
                parts.extend(str(value) for value in row)
    return "\n".join(parts)


def _context(stack, query) -> str:
    return stack.compose(query).render()


# ============================================================== the purge


def test_purge_erases_every_personal_store(stack):
    _plant_everything(stack)
    planted = _all_personal_text(stack)
    for marker in ALL_MARKS:
        assert marker in planted, f"{marker} was never stored; the test would be vacuous"
    assert MEMORY_MARK in _context(stack, f"o que sabes sobre {MEMORY_MARK}?")

    result = stack.purge_everything()

    assert result["ok"], result
    assert result["conversations"] >= 2
    assert result["memories"] == 1
    assert result["nodes"] >= 1
    assert result["facts"] >= 1
    assert stack.active_conversation_id is None
    remaining = _all_personal_text(stack)
    for marker in ALL_MARKS:
        assert marker not in remaining, f"{marker} survived the erase"
    for table in PERSONAL_TABLES:
        count = stack.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        assert count == 0, f"{table} still has {count} row(s)"
    for marker in ALL_MARKS:
        assert marker not in _context(stack, f"o que sabes sobre {marker}?")


def test_purge_is_not_bounded_by_the_renderer_list_limit(stack):
    """ConversationStore.delete_all lists at most 200 threads. An erase that
    went through it would leave the 201st conversation behind."""
    from core.conversation_store import MAX_LIST_LIMIT

    for _ in range(MAX_LIST_LIMIT + 5):
        stack.conversations.create("Conversa")
    assert stack.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] > MAX_LIST_LIMIT

    assert stack.purge_everything()["ok"]

    assert stack.conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0] == 0


def test_erased_text_does_not_stay_in_the_database_file(stack, db_path):
    """A DELETE only marks pages free. Without the VACUUM the sentences would
    still be readable with a hex editor."""
    _plant_everything(stack)
    stack.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    assert MEMORY_MARK.encode() in db_path.read_bytes()

    result = stack.purge_everything()

    assert result["compacted"] is True
    raw = db_path.read_bytes()
    wal = db_path.with_name(db_path.name + "-wal")
    if wal.exists():
        raw += wal.read_bytes()
    for marker in ALL_MARKS:
        assert marker.encode() not in raw, f"{marker} is still in the file's bytes"


def test_purge_keeps_preferences_that_are_not_personal(stack):
    stack.engine.set_preference("ui.some_setting", "valor")
    stack.engine.set_fact("nome", "Ana")

    assert stack.purge_everything()["ok"]

    assert stack.engine.get_preference("ui.some_setting") == "valor"
    assert stack.engine.get_facts() == {}
    assert stack.engine.get_user_profile() == {}


def test_a_failed_purge_changes_nothing_and_says_so(stack, monkeypatch):
    """All or nothing. A half-erased profile reported as erased is the one
    outcome this must never produce."""
    _plant_everything(stack)
    before = _all_personal_text(stack)
    monkeypatch.setattr(MemoryStack, "_PURGE_TABLES",
                        MemoryStack._PURGE_TABLES + ("tabela_que_nao_existe",))

    result = stack.purge_everything()

    assert result["ok"] is False
    assert result["error"] == "purge_failed"
    assert _all_personal_text(stack) == before
    assert stack.conn.in_transaction is False


def test_queued_background_work_cannot_write_erased_text_back(db_path):
    """A summary or extraction queued before the erase holds the text it was
    given. It must be discarded, and an in-flight job must finish first."""
    engine = MemoryEngine()
    built = MemoryStack(engine, background=True)
    try:
        built.drain()
        started, release = threading.Event(), threading.Event()
        ran_after: list[bool] = []

        def slow_job():
            started.set()
            release.wait(5)

        built._defer(slow_job)
        assert started.wait(5)
        for _ in range(3):
            built._defer(lambda: ran_after.append(
                built.remember(f"reescrito {MEMORY_MARK}", kind="fact")["ok"]))

        result: dict = {}
        purger = threading.Thread(target=lambda: result.update(built.purge_everything()))
        purger.start()
        purger.join(0.3)
        assert purger.is_alive(), "the purge did not wait for the in-flight job"
        release.set()
        purger.join(10)

        assert result["ok"], result
        built.drain()
        # Whatever the scheduling, nothing queued before the erase may exist
        # after it: each job either ran first and was erased, or was dropped.
        assert MEMORY_MARK not in _all_personal_text(built)
        assert built.conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 0
    finally:
        built.stop()
        if built._worker is not None:
            built._worker.join(timeout=5)
        engine.close()


def test_a_job_dequeued_before_the_purge_is_skipped_after_it(db_path):
    """The worker can take a job off the queue and then wait for the gate the
    purge holds. That job must not run once it gets the gate."""
    engine = MemoryEngine()
    built = MemoryStack(engine, background=True)
    try:
        built.drain()
        ran: list[str] = []
        built._job_gate.acquire()
        try:
            built._defer(lambda: ran.append("ran"))
            built.drain()  # the worker has dequeued it and is waiting on the gate
            assert built._jobs.empty()
            # What purge_everything does while it holds the gate.
            built._purge_epoch += 1
        finally:
            built._job_gate.release()
        built._defer(lambda: ran.append("new"))
        built.drain()
        threading.Event().wait(0.2)
        assert ran == ["new"]
    finally:
        built.stop()
        if built._worker is not None:
            built._worker.join(timeout=5)
        engine.close()


def test_purge_refuses_when_memory_is_unavailable(stack):
    stack.ready = False
    assert stack.purge_everything() == {"ok": False, "error": "memory_unavailable"}


# ============================================================== the tasks


def test_only_finished_tasks_are_cleared(tasks):
    done = tasks.create_task("tarefa terminada")
    tasks.mark_complete(done["id"], result={"texto": "resultado"})
    failed = tasks.create_task("tarefa falhada")
    tasks.mark_failed(failed["id"], "erro")
    running = tasks.create_task("tarefa a correr")
    tasks.mark_running(running["id"])
    queued = tasks.create_task("tarefa em fila")

    assert tasks.clear_finished() == 2

    assert tasks.get_task(done["id"]) is None
    assert tasks.get_task(failed["id"]) is None
    assert tasks.get_task(running["id"]) is not None
    assert tasks.get_task(queued["id"]) is not None


# ============================================================ the endpoint


@pytest.mark.parametrize("confirmation", [None, "", "apagar", "APAGAR", "sim", 1, ["APAGAR TUDO"]])
def test_the_endpoint_refuses_without_the_typed_confirmation(bridge, stack, confirmation):
    _plant_everything(stack)
    before = _all_personal_text(stack)

    result = bridge.erase_all_data(confirmation)

    assert result == {"ok": False, "error": "confirmation_required"}
    assert _all_personal_text(stack) == before


def test_the_endpoint_erases_everything_and_opens_a_fresh_thread(bridge, stack, tasks):
    _plant_everything(stack)
    finished = tasks.create_task(f"pedido antigo {CHAT_MARK}")
    tasks.mark_complete(finished["id"])

    result = bridge.erase_all_data(bridge.ERASE_ALL_CONFIRMATION)

    assert result["ok"], result
    assert result["tasks"] == 1
    remaining = _all_personal_text(stack)
    for marker in ALL_MARKS:
        assert marker not in remaining
    # One new, empty thread so the next message has somewhere to go.
    threads = stack.conversations.list(limit=10)
    assert [thread["id"] for thread in threads] == [result["activeId"]]
    assert stack.conversations.messages(result["activeId"]) == []
    assert result["memory"]["memories"] == []
    assert result["memory"]["facts"] == []
    assert result["memory"]["profile"] == {}
    assert result["memory"]["knowledge"]["nodes"] == 0
    assert bridge.brain.conversation == []


def test_the_endpoint_reports_a_task_failure_instead_of_claiming_success(bridge, stack,
                                                                          monkeypatch):
    _plant_everything(stack)

    def broken():
        raise OSError("disco cheio")

    monkeypatch.setattr(bridge.task_engine, "clear_finished", broken)
    result = bridge.erase_all_data(bridge.ERASE_ALL_CONFIRMATION)

    assert result["ok"] is False
    assert result["error"] == "tasks_not_cleared"
    assert MEMORY_MARK not in _all_personal_text(stack)


# ======================================================== not a model tool


def test_erasing_is_not_reachable_by_the_model():
    """The model can neither see nor call it: no plugin, tool definition or
    capability mentions it. The only caller is the Settings page."""
    offenders = []
    for folder in ("plugins", "core"):
        for path in (ROOT / folder).rglob("*.py"):
            if path.name == "main.py" and folder == "core":
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            if re.search(r"erase_all_data|purge_everything", text) and \
                    path.name != "memory_stack.py":
                offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"erase is referenced outside the bridge: {offenders}"

    main_source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    callers = re.findall(r"purge_everything\(", main_source)
    assert len(callers) == 1, "purge_everything must have exactly one caller in main.py"

    frontend = ROOT / "frontend"
    users = [path.relative_to(ROOT).as_posix()
             for pattern in ("components/*.tsx", "pages/*.tsx", "lib/*.ts")
             for path in frontend.glob(pattern)
             if "erase_all_data" in path.read_text(encoding="utf-8")]
    assert users == ["frontend/pages/index.tsx"], users
