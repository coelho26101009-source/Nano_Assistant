"""Long-term memory: what Nano may carry from one conversation into the next.

CONVERSATION MEMORY AND LONG-TERM MEMORY ARE NOT THE SAME THING
---------------------------------------------------------------
A conversation remembers what happened in *that* chat. Long-term memory holds
the small number of durable things that are true about the user regardless of
which chat they are in: the machine they own, the language they want, the
project they are building, a decision they made and expect Nano to honour later.

The distinction is the whole product argument for this module. Storing every
message as a "memory" produces a store nobody can read, retrieval that returns
noise, and an assistant that recites yesterday's small talk. So the bar for
entry is high and it is enforced in three places: ``core.memory_safety``
(provenance, secrets, injection shape), the extractor
(``core.memory_extraction``, which proposes very little), and the uniqueness
index below, which collapses a fact restated five times into one row.

EXPLICIT VERSUS INFERRED
------------------------
``origin='explicit'`` means the user asked for it — "lembra-te que...", "a
partir de agora...", or a manual edit in the Memória page. Those are stored
active, with high confidence, and they survive.

``origin='inferred'`` means Nano noticed something that looks durable. Those
enter as ``status='candidate'``: they are visible in the UI, they are NOT put
into the model's context, and they only become active if the user promotes them.
An assistant that quietly promotes its own guesses to facts is an assistant that
will one day confidently tell you something you never said.

WHAT MEMORY CANNOT DO
---------------------
It cannot authorise anything. A memory is text; it enters the system prompt as
context and travels no further. Permissions are created only by
``PermissionManager`` through an explicit user decision, so no sentence stored
here — however it is phrased, whoever wrote it — can widen what Nano may do to
the machine.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from core import memory_extraction, memory_safety, text_normalize
from core.memory_schema import new_id
from core.retrieval import RetrievalIndex
from core.trust import TrustLevel

logger = logging.getLogger("nano.long_term_memory")

#: The categories a memory can have. Open enough to be useful, closed enough
#: that the Memória page can offer a real filter rather than a free-text field.
KINDS: tuple[str, ...] = (
    "preference",   # how the user wants Nano to behave
    "fact",         # something true about the user or their world
    "hardware",     # this machine: GPU, RAM, peripherals
    "software",     # tools, apps, versions in use
    "project",      # something being built
    "goal",         # an intention that outlives the chat
    "decision",     # a choice made, to be honoured later
    "person",       # someone the user refers to
    "other",
)

STATUSES: tuple[str, ...] = ("active", "candidate", "archived")
ORIGINS: tuple[str, ...] = ("explicit", "inferred", "manual")

MAX_LIST_LIMIT = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tags(tags: Any) -> list[str]:
    if isinstance(tags, str):
        raw: Iterable = [part for part in tags.split(",")]
    elif isinstance(tags, (list, tuple, set)):
        raw = tags
    else:
        raw = []
    seen: list[str] = []
    for tag in raw:
        value = text_normalize.shorten(str(tag).strip().lstrip("#"), 32)
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= 8:
            break
    return seen


class LongTermMemory:
    """The store. Every write passes ``memory_safety.evaluate`` first."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock,
                 index: RetrievalIndex | None = None):
        self.conn = conn
        self._lock = lock
        self.index = index

    # ------------------------------------------------------------------ write

    def remember(self, text: str, *, kind: str = "fact", origin: str = "explicit",
                 trust: str = TrustLevel.USER.value, confidence: float | None = None,
                 importance: int = 3, tags: Any = None,
                 source_conversation_id: str | None = None,
                 source_message_id: int | None = None,
                 legacy_key: str | None = None,
                 status: str | None = None) -> dict:
        """Store one durable fact, or explain precisely why it was refused.

        Returns ``{"ok": True, "memory": {...}, "created": bool}`` or
        ``{"ok": False, "error": <machine-readable>, "detail": <human text>}``.
        The refusal is structured because the caller has to be able to tell the
        user *why* nothing was saved — "não guardei isso porque parece uma
        chave de API" is useful; a silent no-op is not.
        """
        verdict = memory_safety.evaluate(text, trust=trust)
        if not verdict.allowed:
            logger.info("Memória recusada (%s): %s", verdict.reason,
                        memory_safety.redact(text))
            return {"ok": False, "error": verdict.reason, "detail": verdict.detail}

        clean = " ".join(str(text).split())
        normalized = text_normalize.normalize(clean)
        kind = kind if kind in KINDS else "other"
        origin = origin if origin in ORIGINS else "explicit"
        # An inferred memory is a CANDIDATE unless the caller says otherwise.
        # See the module docstring: Nano does not promote its own guesses.
        resolved_status = status if status in STATUSES else (
            "candidate" if origin == "inferred" else "active")
        score = confidence if confidence is not None else (
            0.55 if origin == "inferred" else 0.92)
        stamp = _now()

        try:
            with self._lock:
                existing = self.conn.execute(
                    "SELECT id, importance, confidence, status FROM memories WHERE normalized=?",
                    (normalized,)).fetchone()
                if existing:
                    memory_id = existing[0]
                    # Restating a fact makes it more certain, never less, and an
                    # explicit restatement promotes a candidate.
                    new_confidence = max(float(existing[2] or 0.0), float(score))
                    new_status = "active" if origin != "inferred" else existing[3]
                    self.conn.execute(
                        "UPDATE memories SET text=?, kind=?, confidence=?, importance=?,"
                        " status=?, updated_at=?, source_conversation_id=COALESCE(?, source_conversation_id),"
                        " source_message_id=COALESCE(?, source_message_id) WHERE id=?",
                        (clean, kind, new_confidence, max(1, min(int(importance), 5)),
                         new_status, stamp, source_conversation_id, source_message_id,
                         memory_id))
                    created = False
                else:
                    memory_id = new_id("mem")
                    self.conn.execute(
                        "INSERT INTO memories (id, text, normalized, kind, origin, trust,"
                        " status, confidence, importance, pinned, legacy_key, tags,"
                        " source_conversation_id, source_message_id, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?)",
                        (memory_id, clean, normalized, kind, origin, TrustLevel.USER.value,
                         resolved_status, float(score), max(1, min(int(importance), 5)),
                         legacy_key, json.dumps(_clean_tags(tags), ensure_ascii=False),
                         source_conversation_id, source_message_id, stamp, stamp))
                    created = True
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a guardar memória")
            return {"ok": False, "error": "write_failed", "detail": str(exc)}

        stored = self.get(memory_id)
        if stored:
            self._index(stored)
        logger.info("Memória %s (%s/%s): %s", "criada" if created else "atualizada",
                    kind, origin, memory_safety.redact(clean))
        return {"ok": True, "created": created, "memory": stored}

    def update(self, memory_id: str, *, text: str | None = None, kind: str | None = None,
               importance: int | None = None, pinned: bool | None = None,
               tags: Any = None, status: str | None = None) -> dict:
        """Edit one memory from the Memória page. Re-runs the safety gate on text."""
        current = self.get(memory_id)
        if current is None:
            return {"ok": False, "error": "unknown_memory"}

        fields: list[str] = []
        params: list = []
        if text is not None:
            verdict = memory_safety.evaluate(text, trust=TrustLevel.USER.value)
            if not verdict.allowed:
                return {"ok": False, "error": verdict.reason, "detail": verdict.detail}
            clean = " ".join(str(text).split())
            normalized = text_normalize.normalize(clean)
            with self._lock:
                clash = self.conn.execute(
                    "SELECT id FROM memories WHERE normalized=? AND id<>?",
                    (normalized, str(memory_id))).fetchone()
            if clash:
                return {"ok": False, "error": "duplicate_memory",
                        "detail": "já existe uma memória com este texto"}
            fields += ["text=?", "normalized=?"]
            params += [clean, normalized]
        if kind is not None:
            fields.append("kind=?")
            params.append(kind if kind in KINDS else "other")
        if importance is not None:
            fields.append("importance=?")
            params.append(max(1, min(int(importance), 5)))
        if pinned is not None:
            fields.append("pinned=?")
            params.append(1 if pinned else 0)
        if tags is not None:
            fields.append("tags=?")
            params.append(json.dumps(_clean_tags(tags), ensure_ascii=False))
        if status is not None:
            fields.append("status=?")
            params.append(status if status in STATUSES else "active")
            # Promoting a candidate is the user vouching for it; record that the
            # memory is no longer only Nano's own inference.
            if status == "active" and current.get("origin") == "inferred":
                fields.append("origin=?")
                params.append("manual")
        if not fields:
            return {"ok": False, "error": "nothing_to_update"}

        fields.append("updated_at=?")
        params.append(_now())
        params.append(str(memory_id))
        try:
            with self._lock:
                self.conn.execute(
                    f"UPDATE memories SET {', '.join(fields)} WHERE id=?", params)
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a atualizar memória")
            return {"ok": False, "error": "write_failed", "detail": str(exc)}

        stored = self.get(memory_id)
        if stored:
            self._index(stored)
        return {"ok": True, "memory": stored}

    def delete(self, memory_id: str) -> dict:
        if self.get(memory_id) is None:
            return {"ok": False, "error": "unknown_memory"}
        try:
            with self._lock:
                self.conn.execute("DELETE FROM memories WHERE id=?", (str(memory_id),))
                self.conn.execute(
                    "DELETE FROM knowledge_links WHERE kind='memory' AND ref_id=?",
                    (str(memory_id),))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a apagar memória")
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        if self.index is not None:
            self.index.remove(f"memory:{memory_id}")
        return {"ok": True, "id": memory_id}

    def clear(self) -> dict:
        """Forget everything. The user confirms this in the UI before it lands."""
        try:
            with self._lock:
                row = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()
                total = int(row[0]) if row else 0
                self.conn.execute("DELETE FROM knowledge_links WHERE kind='memory'")
                self.conn.execute("DELETE FROM memories")
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a limpar a memória")
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}
        removed_index = self.index.clear_kind("memory") if self.index is not None else 0
        logger.info("Memória de longo prazo limpa: %d registo(s)", total)
        return {"ok": True, "removed": total, "indexEntries": removed_index}

    def touch(self, memory_ids: Sequence[str]) -> None:
        """Record that a memory was actually used. Cheap, best-effort, no commit storm."""
        ids = [str(value) for value in memory_ids if value][:20]
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        try:
            with self._lock:
                self.conn.execute(
                    f"UPDATE memories SET last_used_at=?, use_count=use_count+1"
                    f" WHERE id IN ({marks})", [_now(), *ids])
                self.conn.commit()
        except sqlite3.Error:
            logger.debug("Não foi possível registar o uso das memórias", exc_info=True)

    # ------------------------------------------------------------------- read

    def get(self, memory_id: str) -> dict | None:
        if not memory_id:
            return None
        try:
            with self._lock:
                row = self.conn.execute(
                    f"SELECT {_COLUMNS} FROM memories WHERE id=?", (str(memory_id),)
                ).fetchone()
        except sqlite3.Error:
            return None
        return _row_to_memory(row) if row else None

    def list(self, *, limit: int = 100, kind: str | None = None,
             status: str | None = "active", query: str = "",
             include_candidates: bool = True) -> list[dict]:
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status=?")
            params.append(status)
        elif not include_candidates:
            clauses.append("status='active'")
        if kind and kind in KINDS:
            clauses.append("kind=?")
            params.append(kind)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT {_COLUMNS} FROM memories{where}"
                    " ORDER BY pinned DESC, importance DESC, updated_at DESC LIMIT ?",
                    [*params, limit]).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar memórias")
            return []
        memories = [_row_to_memory(row) for row in rows]
        needle = text_normalize.normalize(query)
        if needle:
            memories = [m for m in memories
                        if needle in text_normalize.normalize(f"{m['text']} {' '.join(m['tags'])}")]
        return memories

    def pinned(self, limit: int = 8) -> list[dict]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT {_COLUMNS} FROM memories WHERE pinned=1 AND status='active'"
                    " ORDER BY importance DESC, updated_at DESC LIMIT ?",
                    (max(1, min(int(limit), 20)),)).fetchall()
        except sqlite3.Error:
            return []
        return [_row_to_memory(row) for row in rows]

    #: Score given to a memory matched only by TOPIC. Deliberately below any
    #: real lexical hit, so a word-for-word match always outranks a category
    #: match, and above the noise floor so it is worth including at all.
    TOPIC_SCORE = 0.34

    #: How many topic-matched memories may join a result set. Small: the point
    #: is to rescue the one relevant fact, not to dump a category.
    MAX_TOPIC_MATCHES = 2

    def search(self, query: str, *, limit: int = 5, min_score: float | None = None) -> list[dict]:
        """Relevant ACTIVE memories for a message. Candidates never surface here.

        THREE PASSES, IN DESCENDING CONFIDENCE.

        1. **Lexical.** The FTS5/BM25 index. Best when the user reuses their own
           words, which is most of the time.

        2. **Topic.** Lexical retrieval has one failure that matters here, and
           it is not hypothetical: the memory says "O meu PC tem uma GTX 1660
           Ti" and the question is "a minha placa gráfica chega?". Not one word
           is shared, so BM25 correctly returns nothing — and Nano forgets a
           fact it was told, which is the exact complaint this whole phase
           exists to fix. So the QUESTION is classified with the same rules that
           classify a memory (``memory_extraction.classify_kind``), and memories
           of that category become candidates at a lower score. "placa gráfica"
           classifies as `hardware`; the GTX memory is `hardware`; it is found.

           This is a real, explainable rule, not a similarity guess: it can be
           described to the user in one sentence, and it is bounded to two
           results so a category can never flood the context.

        3. **Substring.** The floor, for when the index itself is unavailable.
           Degraded ranking beats no memory at all.
        """
        if not str(query or "").strip():
            return []
        results: list[dict] = []
        seen: set[str] = set()

        def offer(stored: dict, score: float) -> None:
            if not stored or stored["status"] != "active" or stored["id"] in seen:
                return
            seen.add(stored["id"])
            stored["score"] = round(score, 4)
            results.append(stored)

        if self.index is not None:
            kwargs = {"kinds": ["memory"], "limit": max(1, int(limit)) * 2}
            if min_score is not None:
                kwargs["min_score"] = min_score
            for hit in self.index.search(query, **kwargs):
                memory_id = hit.metadata.get("memoryId") or hit.entry_id.split(":", 1)[-1]
                offer(self.get(str(memory_id)), hit.score)

        if len(results) < limit:
            topic = memory_extraction.classify_kind(query)
            # "fact" is what classify_kind returns when nothing matched, so it
            # carries no signal and must not be used as a category.
            if topic != "fact":
                for stored in self.list(limit=40, kind=topic, status="active")[:self.MAX_TOPIC_MATCHES]:
                    offer(stored, self.TOPIC_SCORE)

        if not results:
            needle = text_normalize.token_set(query)
            for stored in self.list(limit=60, status="active"):
                if needle & text_normalize.token_set(stored["text"]):
                    offer(stored, text_normalize.overlap_score(query, stored["text"]))

        results.sort(key=lambda item: (item.get("score", 0.0), item["importance"]),
                     reverse=True)
        return results[:max(1, int(limit))]

    def stats(self) -> dict:
        try:
            with self._lock:
                by_kind = dict(self.conn.execute(
                    "SELECT kind, COUNT(*) FROM memories WHERE status='active' GROUP BY kind"
                ).fetchall())
                by_status = dict(self.conn.execute(
                    "SELECT status, COUNT(*) FROM memories GROUP BY status").fetchall())
        except sqlite3.Error:
            by_kind, by_status = {}, {}
        return {
            "total": sum(int(value) for value in by_status.values()),
            "active": int(by_status.get("active", 0)),
            "candidates": int(by_status.get("candidate", 0)),
            "archived": int(by_status.get("archived", 0)),
            "byKind": {str(k): int(v) for k, v in by_kind.items()},
        }

    def reindex_all(self) -> int:
        """Rebuild the retrieval rows for every memory. Bounded and idempotent."""
        if self.index is None:
            return 0
        count = 0
        for memory in self.list(limit=MAX_LIST_LIMIT, status=None):
            if self._index(memory):
                count += 1
        return count

    # ---------------------------------------------------------------- private

    def _index(self, memory: dict) -> bool:
        """Only ACTIVE memories are retrievable. A candidate is not a fact yet."""
        if self.index is None:
            return False
        entry_id = f"memory:{memory['id']}"
        if memory.get("status") != "active":
            self.index.remove(entry_id)
            return False
        return self.index.upsert(
            entry_id, kind="memory", scope="", title=memory.get("kind", "fact"),
            body=memory["text"], created_at=memory.get("createdAt") or _now(),
            metadata={"memoryId": memory["id"], "kind": memory.get("kind"),
                      "importance": memory.get("importance"),
                      "sourceConversationId": memory.get("sourceConversationId")})


_COLUMNS = (
    "id, text, kind, origin, trust, status, confidence, importance, pinned,"
    " legacy_key, tags, source_conversation_id, source_message_id, created_at,"
    " updated_at, last_used_at, use_count"
)


def _row_to_memory(row) -> dict:
    try:
        tags = json.loads(row[10]) if row[10] else []
    except (ValueError, TypeError):
        tags = []
    return {
        "id": row[0],
        "text": row[1],
        "kind": row[2],
        "origin": row[3],
        "trust": row[4],
        "status": row[5],
        "confidence": float(row[6] or 0.0),
        "importance": int(row[7] or 3),
        "pinned": bool(row[8]),
        "legacyKey": row[9],
        "tags": tags if isinstance(tags, list) else [],
        "sourceConversationId": row[11],
        "sourceMessageId": row[12],
        "createdAt": row[13],
        "updatedAt": row[14],
        "lastUsedAt": row[15],
        "useCount": int(row[16] or 0),
    }


__all__ = ["KINDS", "ORIGINS", "STATUSES", "LongTermMemory"]
