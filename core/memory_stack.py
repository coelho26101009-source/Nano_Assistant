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

FORGETTING IS NOT DEFERRED
--------------------------
The rule above has one deliberate exception. Deleting, archiving, demoting,
editing or clearing a memory takes everything the Second Brain derived from it
along before the call returns: "later" would mean the model can still be told
what the user just removed. A queued promotion re-reads its memory when it
runs, so work queued before the forget cannot write the memory back.

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

from core import memory_extraction, memory_schema, summarizer, text_normalize
from core.context_composer import ComposedContext, ContextComposer
from core.conversation_store import ConversationStore
from core.knowledge_graph import (DEFAULT_RELATION, SYMMETRIC_RELATIONS, DerivedGraph,
                                  DerivedNode, KnowledgeGraph, edge_target, node_target)
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
        self.memories = LongTermMemory(self.conn, self._lock, self.index,
                                       on_change=self._memory_changed)
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
            # Derived state is brought in line with its source BEFORE anything
            # can be composed: an install upgraded from a build that left
            # forgotten sentences in the Second Brain must not serve them even
            # once, and the deferred queue below may take seconds to reach it.
            # It costs reads and regular-expression passes; nothing is written
            # when the graph is already right.
            self.reconcile_knowledge()
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

    def reconcile_knowledge(self) -> tuple[int, int]:
        """Rebuild the derived Second Brain from the ACTIVE memories, exactly.

        WHY THIS EXISTS. Nodes and edges are derived from memories, so a
        better derivation rule only shows on an install with history if the
        current rule is run over the memories that already exist -- and an
        install upgraded from a build that copied memory text into nodes and
        never took it back is repaired by the same pass: a derived node no
        active memory supports is removed, and a supported one shows the text
        of a memory that still exists.

        SAFE TO RUN REPEATEDLY, AND IDEMPOTENT. Counts and weights are
        recomputed from the evidence rather than incremented, so the second of
        two passes over unchanged memories writes nothing at all. A node or
        edge the user removed stays removed (``knowledge_suppressions``), and
        manual nodes and edges are never deleted. It reads ACTIVE memories
        only, which have already passed the safety gate.

        With long-term memory switched off it still removes what is no longer
        supported -- forgetting does not wait for a setting -- but creates
        nothing new.

        Returns how many nodes and edges the pass added (negative when it
        removed some), so a caller can log a measured number.
        """
        if not self.ready:
            return (0, 0)
        try:
            with self._lock:
                # Collapse mirrored copies of a symmetric relation FIRST. Storing
                # one canonical direction is a rule about writes, so a database
                # written by an earlier build still holds both rows of every pair
                # it saw twice -- and that database is the only one that has the
                # problem. Done before the snapshot so the returned numbers stay
                # "what this pass changed"; the removal logs its own count.
                self.knowledge.dedupe_symmetric_edges()
                before = self.knowledge.stats()
                synced = self.sync_knowledge()
                repaired = self.knowledge.repair_index()
                after = self.knowledge.stats()
        except Exception:
            logger.exception("Falha a reconciliar o Second Brain")
            return (0, 0)
        if repaired.get("updated") or repaired.get("removed"):
            logger.info("Índice do Second Brain corrigido: %d entrada(s) reescrita(s),"
                        " %d removida(s)", repaired["updated"], repaired["removed"])
        if not synced.get("ok"):
            return (0, 0)
        return (after["nodes"] - before["nodes"], after["edges"] - before["edges"])

    def sync_knowledge(self) -> dict:
        """Make the derived half of the Second Brain what the ACTIVE memories support.

        Read, derive and write happen under the store's lock in one hold, so a
        memory deleted on another thread is either already gone when this reads
        or deleted after it finished -- never in between, which is the window a
        stale write would need. See ``KnowledgeGraph.sync_derived`` for what is
        written, and ``_derive`` for what one memory supports.
        """
        if not self.ready:
            return {"ok": False, "error": "memory_unavailable"}
        with self._lock:
            try:
                desired = self._desired_graph(self.memories.derivation_sources(),
                                              self.knowledge.suppressions())
            except Exception:
                logger.exception("Falha a derivar o Second Brain das memórias")
                return {"ok": False, "error": "derive_failed"}
            report = self.knowledge.sync_derived(desired, grow=self.long_term_enabled)
        changed = {key: value for key, value in report.items()
                   if isinstance(value, int) and not isinstance(value, bool) and value}
        if changed:
            # Counts only: what changed, never what it said.
            logger.info("Second Brain sincronizado com as memórias: %s", changed)
        return report

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

    def delete_conversations(self, conversation_ids: list[str]) -> dict:
        """Delete several threads in one operation. See ConversationStore.delete_many.

        The facade adds the two things the store cannot know about: the links
        from Second Brain nodes back into those threads, pruned in ONE pass
        rather than per id, and the active-thread pointer, which has to be
        released if the conversation the Brain is holding was in the batch.

        Long-term memories that originated in these threads survive, exactly as
        they do for a single delete. That lifecycle rule is not relaxed because
        more rows were selected: a memory is a separate object with its own row
        in Memória and its own delete.
        """
        if not self.ready:
            return {"ok": False, "error": "memory_unavailable", "removed": 0}
        result = self.conversations.delete_many(conversation_ids)
        deleted = [str(value) for value in result.get("deleted") or []]
        if deleted:
            self.knowledge.prune_links("conversation", deleted)
        if self._active_id in deleted:
            self._active_id = None
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

        WHY THE STATUS COMES FROM THE EXTRACTOR AND NOT FROM HERE.
        A well-evidenced inferred fact may now be stored ACTIVE rather than as
        an inert candidate (see core.memory_extraction). The decision belongs to
        the extractor because that is where the evidence is; this method's job
        is to check the two things the extractor cannot see -- whether long-term
        memory is on at all, and whether the user allowed Nano to propose
        memories by itself -- and then to hand the result to the store, which
        applies the safety gate one final time.

        ``memory_auto_capture`` is the single switch. When it is off, NOTHING
        inferred is written: not active, not candidate. A switch that only
        downgraded automatic memories to candidates would still be filling a
        list the user asked Nano not to fill.
        """
        if not self.ready or not self.long_term_enabled:
            return []
        saved: list[dict] = []
        for candidate in memory_extraction.extract(text):
            if candidate.origin == "inferred" and not self.capture_enabled:
                continue
            # SAME PIPELINE AS AN EXPLICIT MEMORY, deliberately. An automatically
            # captured fact is not a second class of thing living in its own
            # silo: the store announces the write (see _memory_changed) and an
            # active memory becomes Second Brain nodes through the identical
            # derivation, so the graph, the retrieval index and the
            # ContextComposer see it exactly like anything the user typed.
            result = self.memories.remember(
                candidate.text, kind=candidate.kind, origin=candidate.origin,
                trust=TrustLevel.USER.value, confidence=candidate.confidence,
                importance=candidate.importance, status=candidate.status,
                source_conversation_id=conversation_id, source_message_id=message_id)
            if result.get("ok") and result.get("memory"):
                saved.append(result["memory"])
        return saved

    def remember(self, text: str, **kwargs) -> dict:
        """Store one memory on the user's behalf. The graph follows on the worker."""
        if not self.ready:
            return {"ok": False, "error": "memory_unavailable"}
        if not self.long_term_enabled:
            return {"ok": False, "error": "long_term_disabled",
                    "detail": "a memória de longo prazo está desligada nas Definições"}
        conversation_id = kwargs.pop("conversation_id", None) or self._active_id
        return self.memories.remember(text, source_conversation_id=conversation_id,
                                      **kwargs)

    def forget(self, memory_id: str) -> dict:
        """Delete a memory and, before returning, everything derived from it."""
        return self.memories.delete(memory_id)

    def _memory_changed(self, change: str, before: dict | None,
                        after: dict | None) -> None:
        """Keep the Second Brain in step with one write to the memory store.

        A change that can take content OUT of recall is applied before the
        write that caused it returns: a delete, a clear, a memory leaving
        ``active``, an active memory whose text or kind changed. A change that
        can only add -- a new active memory, a candidate promoted, an archive
        restored -- is deferred like promotion always was. It withholds nothing
        the user asked Nano to stop using, and the worker keeps it off the path
        between Enter and the first token.
        """
        if not self.ready:
            return
        if self._withdraws(change, before, after):
            self.sync_knowledge()
        elif after is not None and after.get("status") == "active":
            memory_id = str(after["id"])
            self._defer(lambda: self.promote_to_knowledge(memory_id))

    @staticmethod
    def _withdraws(change: str, before: dict | None, after: dict | None) -> bool:
        if change in ("delete", "clear"):
            return True
        if before is None or before.get("status") != "active":
            return False
        if after is None or after.get("status") != "active":
            return True
        return before.get("text") != after.get("text") or before.get("kind") != after.get("kind")

    # ------------------------------------------------------- knowledge graph

    #: How many entities one memory may name. Three, because "trabalho com
    #: Python e Docker no Windows" is a real sentence with three; more than that
    #: is a list, and a list produces a hairball.
    MAX_ENTITIES_PER_MEMORY = 3

    def promote_to_knowledge(self, memory: dict | str) -> list[dict]:
        """Bring the Second Brain in line with one memory AS IT IS NOW.

        This is what a queued promotion runs, possibly long after it was
        queued. Nothing captured at queue time is trusted: the memory is read
        again, under the same lock hold as the sync that follows, and a memory
        that was deleted, archived or demoted in the meantime is a no-op. An
        edited one is derived from its CURRENT text, because the sync reads the
        store rather than the copy the job was given.

        Returns the nodes the memory supports afterwards.
        """
        memory_id = str((memory.get("id") if isinstance(memory, dict) else memory) or "")
        if not self.ready or not memory_id:
            return []
        with self._lock:
            current = self.memories.get(memory_id)
            if current is None or current.get("status") != "active":
                logger.info("Promoção ignorada: a memória %s já não está ativa", memory_id)
                return []
            self.sync_knowledge()
            return self.knowledge.nodes_for_ref("memory", memory_id)

    def _derive(self, memory: dict) -> tuple[list[tuple[str, str, str]],
                                             list[tuple[str, str, str]]]:
        """The nodes and EDGES one memory supports: ``(slug, title, type)`` and
        ``(slug, slug, relation)``. Pure -- it reads the sentence and nothing else.

        WHY THE GRAPH USED TO BE DOTS
        -----------------------------
        Derivation created a node per proper noun and drew an edge only when a
        single sentence happened to contain exactly two of them. "O meu PC tem
        uma GTX 1660 Ti" contains one, so it produced one node and no edge, and
        the graph honestly reported "2 nós · 0 ligações" -- honest, and useless.

        The missing half was the SUBJECT. A sentence like that has two ends: the
        thing being described and the thing it is described with. The left end
        is not a proper noun, so the entity extractor could never see it, but
        the grammar names it outright. ``memory_extraction.subject`` reads it,
        and the machine collapses onto one canonical node so every hardware fact
        lands on the same "O meu PC" instead of on three synonyms.

        WHAT IS EVIDENCE AND WHAT WOULD BE INVENTION
        --------------------------------------------
        * subject + entity  -> an edge, direction subject -> entity, with the
          relation the sentence's own verb supports ("tem" -> has, "usa" ->
          uses). The verb is in the text; nothing is guessed.
        * no subject, two or more entities -> ``related_to`` between them. They
          co-occur in one stored fact, which is evidence that they belong
          together and NOT evidence of what the connection is.
        * no verb this module recognises -> ``related_to``, always. An invented
          ``depends_on`` is worse than an honest generic, because it reads as
          something the user said.

        Nothing here creates a node from raw message text: the input is an
        ACTIVE memory that has already passed the safety gate.
        """
        text = str(memory.get("text") or "")
        node_type = memory_extraction.NODE_TYPE_FOR_KIND.get(str(memory.get("kind")))
        names = memory_extraction.entities(text, limit=self.MAX_ENTITIES_PER_MEMORY)
        subject_node = memory_extraction.subject(text)

        # A memory with no subject AND no usable entity names nothing that can
        # be drawn. "Prefiro respostas curtas" is exactly that, and a node
        # called "respostas curtas" is the clutter this design exists to avoid.
        if not subject_node and (not node_type or not names):
            return [], []

        nodes: list[tuple[str, str, str]] = []

        def _add(title: str, kind: str) -> str | None:
            # Identity is the slug of the cleaned title, exactly as upsert_node
            # recognises a node, so two spellings of one name are one node.
            clean = text_normalize.shorten(str(title or "").strip(), 90)
            if not clean:
                return None
            slug = text_normalize.slugify(clean)
            if all(slug != known for known, _title, _kind in nodes):
                nodes.append((slug, clean, str(kind or "topic")))
            return slug

        head = _add(*subject_node) if subject_node else None
        entity_slugs: list[str] = []
        if node_type:
            for name in names:
                slug = _add(name, node_type)
                if slug and slug not in entity_slugs:
                    entity_slugs.append(slug)

        relation = memory_extraction.relation_for(text)
        edges: list[tuple[str, str, str]] = []
        if head is not None:
            edges = [(head, slug, relation) for slug in entity_slugs if slug != head]
        elif len(entity_slugs) >= 2:
            edges = [(left, right, DEFAULT_RELATION)
                     for index, left in enumerate(entity_slugs)
                     for right in entity_slugs[index + 1:]]
        return nodes, edges

    def _desired_graph(self, sources: list[dict],
                       suppressed: dict[str, set[str]]) -> DerivedGraph:
        """Everything ``sources`` -- the active memories, best first -- support.

        A node's representative is the first memory that derives it, so a node
        several memories share shows the sentence of the one the user ranks
        highest, and its title and type come from that memory if the node has
        to be created. What the user removed from the graph is left out, per
        memory: a suppressed node takes the edges that memory draws to it.
        """
        desired = DerivedGraph()
        support: dict[tuple[str, str, str], set[str]] = {}
        for memory in sources:
            memory_id = str(memory["id"])
            nodes, edges = self._derive(memory)
            desired.targets[memory_id] = (
                {node_target(slug) for slug, _title, _kind in nodes}
                | {edge_target(left, relation, right) for left, right, relation in edges})
            blocked = suppressed.get(memory_id, set())
            kept = {slug for slug, _title, _kind in nodes
                    if node_target(slug) not in blocked}
            summary = text_normalize.shorten(str(memory.get("text") or ""), 400)
            source = memory.get("sourceConversationId")
            for slug, title, kind in nodes:
                if slug not in kept:
                    continue
                entry = desired.nodes.get(slug)
                if entry is None:
                    entry = desired.nodes[slug] = DerivedNode(
                        title=title, node_type=kind, summary=summary)
                if memory_id not in entry.evidence:
                    entry.evidence.append(memory_id)
                if source:
                    entry.conversations.add(str(source))
            for left, right, relation in edges:
                if (left not in kept or right not in kept
                        or edge_target(left, relation, right) in blocked):
                    continue
                if relation in SYMMETRIC_RELATIONS and right < left:
                    left, right = right, left
                support.setdefault((left, right, relation), set()).add(memory_id)
        desired.edges = {key: len(memory_ids) for key, memory_ids in support.items()}
        return desired

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
