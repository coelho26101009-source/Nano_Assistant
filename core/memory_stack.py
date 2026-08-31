"""The one object the rest of Nano talks to about memory.

WHAT IT IS
----------
``MemoryStack`` wires the six pieces — schema, retrieval index, conversation
store, long-term memory, knowledge graph, context composer — into a single
facade with a small, verb-shaped API: record a message, compose the context,
open a thread, delete a memory. Nothing outside this module needs to know that
five tables and an FTS index are involved, and nothing outside it opens a
connection or writes SQL.

That matters for more than tidiness. Memory now has ordering constraints (a
message must exist before it can be summarised; a memory must exist before a
node can link to it; deleting a thread must also clear its index rows), and a
facade is where those are stated once instead of being re-derived by every
caller.

THE LATENCY RULE
----------------
Everything on the path between the user pressing Enter and the first token
arriving is synchronous and cheap: one INSERT, one FTS write, a handful of
bounded SELECTs. Everything else — summarisation, memory extraction, promoting
entities into the knowledge graph — runs on a single background worker thread,
because none of it changes the answer to the message that triggered it.

One worker, not a thread per event: a thread per message is unbounded
concurrency against one SQLite writer, and SQLite serialises writers anyway. The
queue is bounded and drops its oldest item under pressure rather than growing —
losing a derived summary is a quality regression, running out of memory is an
outage.

Construct with ``background=False`` to run those jobs inline. Tests do that so a
behaviour is asserted where it happens instead of after a sleep.

FAILURE IS CONTAINED
--------------------
Every public method here is written so that a database problem degrades memory
and leaves the conversation working. If the migration failed, ``ready`` is False
and the stack answers with empty context instead of raising into the chat path.
That is deliberate: memory must never become a single point of failure for
talking to Nano.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

from core import memory_extraction, memory_schema, summarizer
from core.context_composer import ComposedContext, ContextComposer
from core.conversation_store import ConversationStore
from core.knowledge_graph import DEFAULT_RELATION, KnowledgeGraph
from core.long_term_memory import LongTermMemory
from core.retrieval import RetrievalIndex
from core.trust import TrustLevel

logger = logging.getLogger("nano.memory_stack")

#: Bounded so a burst cannot grow without limit. See the module docstring.
_QUEUE_SIZE = 64


class MemoryStack:
    """Threads, memories, knowledge and context — assembled and ready to use."""

    def __init__(self, memory_engine, *, background: bool = True,
                 long_term_enabled: bool = True, capture_enabled: bool = True):
        self.engine = memory_engine
        self.conn = memory_engine.conn
        self._lock = memory_engine._lock
        self.ready = False
        self.migration: dict = {"ok": False, "error": "not_run"}

        self.migration = memory_schema.apply(self.conn)
        self.ready = bool(self.migration.get("ok"))

        self.index = RetrievalIndex(self.conn, self._lock)
        self.conversations = ConversationStore(self.conn, self._lock, self.index)
        self.memories = LongTermMemory(self.conn, self._lock, self.index)
        self.knowledge = KnowledgeGraph(self.conn, self._lock, self.index)
        self.composer = ContextComposer(self.conversations, self.memories, self.knowledge)

        #: Whether cross-conversation memory may be written and read at all.
        self.long_term_enabled = bool(long_term_enabled)
        #: Whether Nano may PROPOSE memories on its own. Explicit "lembra-te
        #: que..." requests are honoured regardless: that is the user asking.
        self.capture_enabled = bool(capture_enabled)

        self._active_id: str | None = None
        self._background = bool(background)
        self._jobs: queue.Queue = queue.Queue(maxsize=_QUEUE_SIZE)
        self._worker: threading.Thread | None = None
        self._stopping = threading.Event()
        if self._background:
            self._start_worker()

        if self.ready:
            # An existing database arrives with rows nothing has ever indexed:
            # the legacy facts the migration imported, and every message written
            # before the index existed. Without this, retrieval would only work
            # for things said AFTER the upgrade -- which does not look like a
            # bug, it just looks like Nano not remembering.
            #
            # Deferred, so a long history never delays the window appearing.
            self._defer(self._backfill)

    def _backfill(self) -> None:
        try:
            memories = self.memories.reindex_all()
            messages = self.conversations.backfill_index()
            if memories or messages:
                logger.info("Índice preenchido: %d memórias, %d mensagens",
                            memories, messages)
        except Exception:
            logger.exception("Falha a preencher o índice de recuperação")

    # ------------------------------------------------------------- lifecycle

    def _start_worker(self) -> None:
        self._worker = threading.Thread(target=self._pump, name="nano-memory",
                                        daemon=True)
        self._worker.start()

    def _pump(self) -> None:
        while not self._stopping.is_set():
            try:
                job = self._jobs.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                job()
            except Exception:  # noqa: BLE001 - a bad job must not kill the worker
                logger.exception("Trabalho de memória em segundo plano falhou")
            finally:
                self._jobs.task_done()

    def _defer(self, job: Callable[[], Any]) -> None:
        if not self._background:
            try:
                job()
            except Exception:
                logger.exception("Trabalho de memória falhou")
            return
        try:
            self._jobs.put_nowait(job)
        except queue.Full:
            # Drop the OLDEST, keep the newest: stale derived work is the least
            # valuable thing in the queue.
            try:
                self._jobs.get_nowait()
                self._jobs.task_done()
                self._jobs.put_nowait(job)
            except (queue.Empty, queue.Full):
                logger.warning("Fila de memória cheia; trabalho derivado descartado")

    def drain(self, timeout: float = 5.0) -> None:
        """Wait for deferred work to finish. For shutdown and for tests."""
        if not self._background:
            return
        deadline = threading.Event()
        timer = threading.Timer(timeout, deadline.set)
        timer.daemon = True
        timer.start()
        try:
            while not self._jobs.empty() and not deadline.is_set():
                deadline.wait(0.05)
        finally:
            timer.cancel()

    def stop(self) -> None:
        self._stopping.set()

    # ------------------------------------------------------ active thread

    @property
    def active_conversation_id(self) -> str | None:
        return self._active_id

    def ensure_active(self) -> str | None:
        """The thread new messages belong to, creating one if there is none.

        On a fresh start this resumes the most recently active thread rather
        than opening a blank one: the user closed Nano mid-conversation and
        expects to find it where they left it.
        """
        if not self.ready:
            return None
        if self._active_id and self.conversations.exists(self._active_id):
            return self._active_id
        latest = self.conversations.latest()
        thread = latest or self.conversations.create()
        self._active_id = thread["id"]
        return self._active_id

    def open_conversation(self, conversation_id: str) -> dict | None:
        thread = self.conversations.get(conversation_id)
        if thread is None:
            return None
        self._active_id = thread["id"]
        return thread

    def new_conversation(self, title: str | None = None) -> dict | None:
        if not self.ready:
            return None
        thread = self.conversations.create(title)
        self._active_id = thread["id"]
        return thread

    def delete_conversation(self, conversation_id: str) -> dict:
        result = self.conversations.delete(conversation_id)
        if result.get("ok") and self._active_id == conversation_id:
            self._active_id = None
        # A node linked to a conversation that no longer exists would draw an
        # edge into nothing in the Second Brain.
        self.knowledge.prune_links("conversation", [str(conversation_id)])
        return result

    # --------------------------------------------------------- record turns

    def record_user_message(self, text: str, *, conversation_id: str | None = None,
                            metadata: dict | None = None) -> dict | None:
        """Persist a user turn. Synchronous part only; the rest is deferred."""
        thread_id = conversation_id or self.ensure_active()
        if not thread_id:
            return None
        stored = self.conversations.append(
            thread_id, "user", text, trust=TrustLevel.USER.value, metadata=metadata)
        if stored is None:
            return None
        self._defer(lambda: self._after_user_message(thread_id, stored))
        return stored

    def record_assistant_message(self, text: str, *, conversation_id: str | None = None,
                                 metadata: dict | None = None) -> dict | None:
        thread_id = conversation_id or self.ensure_active()
        if not thread_id:
            return None
        stored = self.conversations.append(
            thread_id, "assistant", text, trust=TrustLevel.USER.value, metadata=metadata)
        if stored is not None:
            self._defer(lambda: self.compact(thread_id))
        return stored

    def _after_user_message(self, conversation_id: str, stored: dict) -> None:
        self.capture_memories(stored.get("content") or "",
                              conversation_id=conversation_id,
                              message_id=stored.get("id"))
        self.compact(conversation_id)

    # -------------------------------------------------------- long-term memory

    def capture_memories(self, text: str, *, conversation_id: str | None = None,
                         message_id: int | None = None) -> list[dict]:
        """Turn a user message into zero or more memories. Usually zero.

        An EXPLICIT request ("lembra-te que...") is always honoured, because
        that is the user asking directly. Inference is skipped entirely when
        automatic capture is off, so the switch in Definições means what it
        says rather than merely lowering a threshold.
        """
        if not self.ready or not self.long_term_enabled:
            return []
        saved: list[dict] = []
        for candidate in memory_extraction.extract(text):
            if candidate.origin == "inferred" and not self.capture_enabled:
                continue
            result = self.memories.remember(
                candidate.text, kind=candidate.kind, origin=candidate.origin,
                trust=TrustLevel.USER.value, confidence=candidate.confidence,
                importance=candidate.importance,
                source_conversation_id=conversation_id, source_message_id=message_id)
            if result.get("ok") and result.get("memory"):
                memory = result["memory"]
                saved.append(memory)
                if memory.get("status") == "active":
                    self.promote_to_knowledge(memory, conversation_id=conversation_id)
        return saved

    def remember(self, text: str, **kwargs) -> dict:
        """Store one memory on the user's behalf and mirror it into the graph."""
        if not self.ready:
            return {"ok": False, "error": "memory_unavailable"}
        if not self.long_term_enabled:
            return {"ok": False, "error": "long_term_disabled",
                    "detail": "a memória de longo prazo está desligada nas Definições"}
        conversation_id = kwargs.pop("conversation_id", None) or self._active_id
        result = self.memories.remember(text, source_conversation_id=conversation_id,
                                        **kwargs)
        memory = result.get("memory")
        if result.get("ok") and memory and memory.get("status") == "active":
            self._defer(lambda: self.promote_to_knowledge(
                memory, conversation_id=conversation_id))
        return result

    def forget(self, memory_id: str) -> dict:
        return self.memories.delete(memory_id)

    # ------------------------------------------------------- knowledge graph

    def promote_to_knowledge(self, memory: dict, *,
                             conversation_id: str | None = None) -> list[dict]:
        """Derive Second Brain nodes from ONE memory. Conservative on purpose.

        A node is created only when the memory has a kind that names a class of
        thing (a device, a tool, a project, a person) AND contains a
        proper-noun-shaped entity to name it after. "Prefiro respostas curtas"
        creates nothing: there is no entity in it, and a node called "respostas
        curtas" would be clutter. "O meu PC tem uma GTX 1660 Ti" creates one.
        """
        node_type = memory_extraction.NODE_TYPE_FOR_KIND.get(str(memory.get("kind")))
        if not node_type:
            return []
        names = memory_extraction.entities(memory.get("text") or "", limit=2)
        if not names:
            return []
        created: list[dict] = []
        for name in names:
            node = self.knowledge.upsert_node(
                name, node_type=node_type, summary=memory.get("text") or "",
                origin="derived")
            if node is None:
                continue
            self.knowledge.attach(node["id"], "memory", memory["id"])
            if conversation_id:
                self.knowledge.attach(node["id"], "conversation", str(conversation_id))
            created.append(node)
        # Two entities named in the SAME memory are evidence of a relationship.
        # The relation stays `related_to`: the sentence proves they belong
        # together, not what the connection is, and asserting `depends_on` from
        # that would be inventing structure.
        if len(created) == 2:
            self.knowledge.link(created[0]["id"], created[1]["id"],
                                relation=DEFAULT_RELATION)
        return created

    # ------------------------------------------------------------ summaries

    def compact(self, conversation_id: str) -> dict | None:
        """Extend the thread summary if enough new messages have accumulated.

        Never destroys anything: the messages remain the authority, and the
        summary is regenerated from them by ``rebuild_summary`` on demand.
        """
        if not self.ready or not conversation_id:
            return None
        try:
            stored = self.conversations.get_summary(conversation_id)
            pending = self.conversations.messages_after(
                conversation_id, stored.get("coveredThrough", 0))
            if not summarizer.should_compact(pending):
                return None
            compactable = pending[:-summarizer.KEEP_RECENT_MESSAGES]
            result = summarizer.summarize(compactable, previous=stored.get("summary", ""))
            if result.empty:
                return None
            self.conversations.set_summary(
                conversation_id, result.text,
                covered_through=result.covered_through,
                covered_messages=stored.get("coveredMessages", 0) + result.covered_messages,
                generator="extractive")
            for item in result.decisions[:3]:
                self.conversations.add_fact(conversation_id, item, kind="decision")
            for item in result.facts[:3]:
                self.conversations.add_fact(conversation_id, item, kind="fact")
            logger.info("Conversa %s compactada: %d mensagens resumidas",
                        conversation_id, result.covered_messages)
            return {"ok": True, "coveredMessages": result.covered_messages}
        except Exception:
            # A failed summary must never break the chat. The previous summary
            # stays, and the next turn tries again.
            logger.exception("Falha a compactar a conversa %s", conversation_id)
            return {"ok": False, "error": "summary_failed"}

    def rebuild_summary(self, conversation_id: str) -> dict:
        """Recompute a summary from the source messages. The recovery path."""
        if not self.ready:
            return {"ok": False, "error": "memory_unavailable"}
        messages = self.conversations.messages_after(conversation_id, 0)
        if len(messages) <= summarizer.KEEP_RECENT_MESSAGES:
            self.conversations.set_summary(conversation_id, "", covered_through=0,
                                           covered_messages=0, generator="extractive")
            return {"ok": True, "summary": "", "coveredMessages": 0}
        result = summarizer.rebuild(messages[:-summarizer.KEEP_RECENT_MESSAGES])
        self.conversations.set_summary(
            conversation_id, result.text, covered_through=result.covered_through,
            covered_messages=result.covered_messages, generator="extractive")
        return {"ok": True, "summary": result.text,
                "coveredMessages": result.covered_messages}

    # -------------------------------------------------------------- context

    def compose(self, query: str, *, conversation_id: str | None = None,
                recent_messages: list[dict] | None = None) -> ComposedContext:
        thread_id = conversation_id or self.ensure_active() or ""
        if not self.ready:
            return ComposedContext(conversation_id=str(thread_id))
        context = self.composer.compose(
            thread_id, query, recent_messages=recent_messages,
            long_term_enabled=self.long_term_enabled,
            knowledge_enabled=self.long_term_enabled)
        if context.memory_ids:
            self._defer(lambda: self.memories.touch(context.memory_ids))
        return context

    def recent_messages(self, conversation_id: str | None = None, *,
                        limit: int = 20) -> list[dict]:
        thread_id = conversation_id or self.ensure_active()
        if not thread_id:
            return []
        return self.conversations.messages(thread_id, limit=limit)

    # ------------------------------------------------------------- overview

    def overview(self) -> dict:
        """Everything the Memória page needs, in counts and rows. No secrets."""
        threads = self.conversations.list(limit=1) if self.ready else []
        return {
            "ready": self.ready,
            "migration": {"from": self.migration.get("from"),
                          "to": self.migration.get("to"),
                          "ok": bool(self.migration.get("ok")),
                          "error": self.migration.get("error")},
            "longTermEnabled": self.long_term_enabled,
            "captureEnabled": self.capture_enabled,
            "memories": self.memories.stats() if self.ready else {},
            "knowledge": self.knowledge.stats() if self.ready else {},
            "retrieval": self.index.stats(),
            "conversations": len(self.conversations.list(limit=200)) if self.ready else 0,
            "messages": self.conversations.total_messages() if self.ready else 0,
            "activeConversationId": self._active_id,
            "lastConversationAt": threads[0]["lastMessageAt"] if threads else None,
        }

    def purge_everything(self) -> dict:
        """Delete conversations, memories and the graph. Confirmed in the UI first."""
        conversations = self.conversations.delete_all()
        memories = self.memories.clear()
        knowledge = self.knowledge.clear()
        self._active_id = None
        return {"ok": True, "conversations": conversations.get("removed", 0),
                "memories": memories.get("removed", 0),
                "nodes": knowledge.get("removed", 0)}


__all__ = ["MemoryStack"]
