"""Persistent conversation threads: the messages, the thread, and its summary.

WHY THREADS ARE STORED AND NO LONGER DERIVED
--------------------------------------------
Nano used to keep one flat, rolling message log. The UI recovered
"conversations" from it by splitting on 45 minutes of silence, and said so
honestly — but the consequences were real: only the newest conversation could be
continued, because only its tail was in the Brain's context; reopening an older
one was read-only; and nothing could be renamed or deleted, because there was no
object to rename or delete.

A thread is now a row. It has an id, a title the user may change, its own
messages, its own progressive summary and its own searchable history. Opening an
old thread rebuilds the model context from that thread and nothing else, which
is what makes "continue where we left off" true rather than aspirational.

ISOLATION IS ENFORCED IN SQL
----------------------------
Every read here is filtered by ``conversation_id`` in the query, not after the
fact. A thread cannot leak into another thread's context by scoring well,
because it is never in the candidate set. The one deliberate exception is
long-term memory, which is cross-thread BY DEFINITION and lives in a different
store with a different lifecycle (``core.long_term_memory``).

DELETION IS COMPLETE, AND STOPS WHERE THE USER EXPECTS
------------------------------------------------------
Deleting a thread removes its messages, its summary, its thread-scoped facts and
its retrieval-index rows — no orphans, nothing that could still surface in a
search. It deliberately does NOT remove long-term memories that happened to
originate in that thread: those were saved as durable facts about the user, they
are visible and deletable in Memória › Memórias, and silently destroying them
because a chat was tidied away would be the kind of invisible data loss this
design exists to avoid. The memory keeps a dangling ``source_conversation_id``,
which the UI renders as "conversa apagada".
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Sequence

from core import text_normalize
from core.memory_schema import new_id, title_from_text
from core.retrieval import RetrievalIndex
from core.trust import TrustLevel

logger = logging.getLogger("nano.conversations")

#: Roles that are part of the conversation as the user understands it. `tool`
#: and `system` rows are machinery: stored, never listed, never indexed.
DISPLAY_ROLES = frozenset({"user", "assistant"})
STORABLE_ROLES = frozenset({"user", "assistant", "tool", "system"})

#: A message shorter than this carries no retrieval signal ("ok", "sim"), and
#: indexing it only dilutes the results.
MIN_INDEXABLE_CHARS = 12

#: Hard ceilings so a runaway caller cannot ask for an unbounded read.
MAX_LIST_LIMIT = 200
MAX_MESSAGE_LIMIT = 500

DEFAULT_TITLE = "Nova conversa"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(raw) if raw else default
    except (ValueError, TypeError):
        return default
    return value


def _message_meta(raw: Any) -> dict:
    """The stored per-message diagnostics, re-shaped by the allow-list.

    Imported lazily because ``core.response_meta`` is a leaf that this store has
    no other reason to depend on, and a store that cannot be constructed without
    the provider stack is a store that is painful to test.
    """
    parsed = _loads(raw, {})
    if not isinstance(parsed, dict) or not parsed:
        return {}
    from core import response_meta

    shaped = response_meta.for_message(parsed)
    # A row whose metadata says nothing about who produced it has no technical
    # details to show. `{"source": "voice"}` would otherwise come back as
    # `{"fallback_used": false}` and open an empty panel on every voice turn.
    if not shaped.get("provider") and not shaped.get("model"):
        return {}
    return shaped


def _attachments(raw: Any) -> list[dict]:
    """The files a user row carried, as stored by ``core.attachments``."""
    parsed = _loads(raw, {})
    files = parsed.get("attachments") if isinstance(parsed, dict) else None
    if not isinstance(files, list):
        return []
    return [{"name": str(entry.get("name") or ""), "size": int(entry.get("size") or 0),
             "truncated": bool(entry.get("truncated")), "text": str(entry.get("text") or "")}
            for entry in files if isinstance(entry, dict)]


#: The longest project name the rail keeps.
MAX_PROJECT_CHARS = 40


class ConversationStore:
    """CRUD for threads and their messages. Owns no connection of its own."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock,
                 index: RetrievalIndex | None = None):
        self.conn = conn
        self._lock = lock
        self.index = index

    # ------------------------------------------------------------- threads

    def create(self, title: str | None = None, *, metadata: dict | None = None) -> dict:
        stamp = _now()
        conversation_id = new_id("conv")
        clean_title = (title or "").strip() or DEFAULT_TITLE
        with self._lock:
            self.conn.execute(
                "INSERT INTO conversations (id, title, title_source, created_at,"
                " updated_at, last_message_at, message_count, archived, metadata)"
                " VALUES (?,?,?,?,?,NULL,0,0,?)",
                (conversation_id, clean_title, "user" if title else "auto",
                 stamp, stamp, json.dumps(metadata or {}, ensure_ascii=False)),
            )
            self.conn.commit()
        logger.info("Nova conversa criada: %s", conversation_id)
        return self.get(conversation_id) or {"id": conversation_id, "title": clean_title}

    def get(self, conversation_id: str) -> dict | None:
        if not conversation_id:
            return None
        with self._lock:
            row = self.conn.execute(
                "SELECT id, title, title_source, created_at, updated_at, last_message_at,"
                " message_count, archived, metadata FROM conversations WHERE id=?",
                (str(conversation_id),),
            ).fetchone()
        return self._row_to_thread(row) if row else None

    def exists(self, conversation_id: str) -> bool:
        return self.get(conversation_id) is not None

    def list(self, *, limit: int = 60, include_archived: bool = False,
             query: str = "") -> list[dict]:
        """Threads, most recently active first.

        `query` matches the TITLE only. Searching message bodies is a different
        operation with a different cost, and it belongs to the retrieval index
        (``search_messages``) rather than to a list query that runs on every
        keystroke in the rail.

        WHY THE ORDER IS NOT JUST A TIMESTAMP
        -------------------------------------
        ``datetime.now()`` on Windows is backed by ``GetSystemTimeAsFileTime``,
        whose resolution is 15.625 ms -- measured on this machine, 199 761 of
        200 000 consecutive calls returned the SAME string. Two threads touched
        inside one tick therefore carry identical ``last_message_at`` values,
        and the tie fell through to ``created_at DESC``, which puts the thread
        created LAST on top no matter which one was actually written to last.

        That is not hypothetical: reopening an older thread and posting to it
        left it below the newer one in 8 of 30 runs of the exact sequence.
        A human cannot type twice inside 15 ms, so the defect is invisible in
        use and reliably visible in tests -- which is the shape of bug that
        turns a green Windows CI leg red at random.

        So the tie is broken on things that ARE strictly ordered:

        * a thread with no messages yet sorts first within its tick. Its only
          activity is its own creation, and "nova conversa" must land on top;
        * otherwise the highest ``messages.id``, an AUTOINCREMENT column and so
          a true record of which thread was written to last;
        * then ``rowid``, so the result is total and the list never reshuffles
          between two identical calls.
        """
        limit = max(1, min(int(limit), MAX_LIST_LIMIT))
        clauses, params = [], []
        if not include_archived:
            clauses.append("archived = 0")
        needle = text_normalize.normalize(query)
        if needle:
            clauses.append("LOWER(title) LIKE ?")
            params.append(f"%{needle}%")
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, title, title_source, created_at, updated_at,"
                    " last_message_at, message_count, archived, metadata"
                    f" FROM conversations{where}"
                    " ORDER BY COALESCE(last_message_at, created_at) DESC,"
                    "  (last_message_at IS NULL) DESC,"
                    "  (SELECT MAX(id) FROM messages m"
                    "    WHERE m.conversation_id = conversations.id) DESC,"
                    "  created_at DESC, rowid DESC"
                    " LIMIT ?", [*params, limit]).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar conversas")
            return []
        threads = [self._row_to_thread(row) for row in rows]
        if needle:
            # LIKE on a raw title cannot see past accents; re-filter in Python so
            # "memoria" finds "Memória".
            threads = [t for t in threads
                       if needle in text_normalize.normalize(t["title"])]
        return threads

    def latest(self) -> dict | None:
        threads = self.list(limit=1)
        return threads[0] if threads else None

    def rename(self, conversation_id: str, title: str) -> dict:
        clean = text_normalize.shorten((title or "").strip(), 120)
        if not clean:
            return {"ok": False, "error": "empty_title"}
        if not self.exists(conversation_id):
            return {"ok": False, "error": "unknown_conversation"}
        with self._lock:
            self.conn.execute(
                "UPDATE conversations SET title=?, title_source='user', updated_at=?"
                " WHERE id=?", (clean, _now(), str(conversation_id)))
            self.conn.commit()
        return {"ok": True, "id": conversation_id, "title": clean}

    def organise(self, conversation_id: str, *, pinned: bool | None = None,
                 project: str | None = None) -> dict:
        """Pin a thread, or file it under a project. ``None`` leaves a field alone.

        Both live in the thread's metadata, so organising needs no migration and
        deleting the thread takes them with it. A project is only a name the
        person typed: an empty name takes the thread out of its project.
        """
        if not self.exists(conversation_id):
            return {"ok": False, "error": "unknown_conversation"}
        name = None
        if project is not None:
            if not isinstance(project, str):
                return {"ok": False, "error": "bad_project"}
            name = " ".join(project.split())
            if len(name) > MAX_PROJECT_CHARS or any(ord(c) < 32 for c in name):
                return {"ok": False, "error": "bad_project"}
        if pinned is not None and not isinstance(pinned, bool):
            return {"ok": False, "error": "bad_pinned"}
        with self._lock:
            row = self.conn.execute("SELECT metadata FROM conversations WHERE id=?",
                                    (str(conversation_id),)).fetchone()
            meta = _loads(row[0] if row else None, {})
            meta = meta if isinstance(meta, dict) else {}
            if pinned is not None:
                if pinned:
                    meta["pinned"] = True
                else:
                    meta.pop("pinned", None)
            if name is not None:
                if name:
                    meta["project"] = name
                else:
                    meta.pop("project", None)
            self.conn.execute("UPDATE conversations SET metadata=? WHERE id=?",
                              (json.dumps(meta, ensure_ascii=False), str(conversation_id)))
            self.conn.commit()
        return {"ok": True, "id": conversation_id, "pinned": bool(meta.get("pinned")),
                "project": meta.get("project") or ""}

    def organised(self, *, limit: int = MAX_LIST_LIMIT) -> list[dict]:
        """Every pinned or filed thread, however old.

        The rail lists the most recent threads only; a pinned thread must not
        fall out of it because it is old, which is the whole point of pinning.
        """
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, title, title_source, created_at, updated_at,"
                    " last_message_at, message_count, archived, metadata"
                    " FROM conversations WHERE archived = 0 AND message_count > 0 AND ("
                    "  json_extract(metadata, '$.pinned') = 1"
                    "  OR COALESCE(json_extract(metadata, '$.project'), '') != '')"
                    " ORDER BY COALESCE(last_message_at, created_at) DESC LIMIT ?",
                    (max(1, int(limit)),)).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar conversas organizadas")
            return []
        return [self._row_to_thread(row) for row in rows]

    def set_archived(self, conversation_id: str, archived: bool) -> dict:
        if not self.exists(conversation_id):
            return {"ok": False, "error": "unknown_conversation"}
        with self._lock:
            self.conn.execute(
                "UPDATE conversations SET archived=?, updated_at=? WHERE id=?",
                (1 if archived else 0, _now(), str(conversation_id)))
            self.conn.commit()
        return {"ok": True, "id": conversation_id, "archived": bool(archived)}

    def delete(self, conversation_id: str) -> dict:
        """Remove a thread and everything derived from it. See the module docstring."""
        thread = self.get(conversation_id)
        if thread is None:
            return {"ok": False, "error": "unknown_conversation"}
        removed_messages = 0
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "DELETE FROM messages WHERE conversation_id=?", (str(conversation_id),))
                removed_messages = cursor.rowcount or 0
                # Summaries and thread facts cascade from the conversations row,
                # but only when foreign keys are on. Deleting them explicitly
                # makes the outcome identical either way.
                self.conn.execute("DELETE FROM conversation_summaries WHERE conversation_id=?",
                                  (str(conversation_id),))
                self.conn.execute("DELETE FROM conversation_facts WHERE conversation_id=?",
                                  (str(conversation_id),))
                self.conn.execute("DELETE FROM knowledge_links WHERE kind='conversation' AND ref_id=?",
                                  (str(conversation_id),))
                self.conn.execute("DELETE FROM conversations WHERE id=?", (str(conversation_id),))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a apagar a conversa %s", conversation_id)
            return {"ok": False, "error": "delete_failed", "detail": str(exc)}

        removed_index = 0
        if self.index is not None:
            removed_index = self.index.remove_scope("message", str(conversation_id))
        logger.info("Conversa %s apagada (%d mensagens, %d entradas de índice)",
                    conversation_id, removed_messages, removed_index)
        return {"ok": True, "id": conversation_id, "messages": removed_messages,
                "indexEntries": removed_index}

    def delete_many(self, conversation_ids: Sequence[str]) -> dict:
        """Delete several threads, reporting exactly what happened to each.

        BEST-EFFORT, AND IT SAYS SO. Each thread is deleted through the same
        :meth:`delete` as a single one, so summaries, thread facts, knowledge
        links and retrieval-index rows are cleaned identically -- there is no
        second, faster deletion path that could forget one of them.

        The alternative, one transaction around the whole batch, would make a
        single unreadable row abort a delete of forty. So a failure is recorded
        per id and returned: the caller can tell the user "37 apagadas, 3
        falharam" instead of a lie in either direction. Reporting "done" for a
        batch that partly failed is the specific outcome this shape exists to
        prevent.

        The list is bounded because it arrives from the renderer.
        """
        ids: list[str] = []
        for raw in list(conversation_ids or [])[:MAX_LIST_LIMIT]:
            value = str(raw or "").strip()
            if value and value not in ids:
                ids.append(value)
        if not ids:
            return {"ok": False, "error": "no_conversations", "removed": 0,
                    "deleted": [], "failed": []}

        deleted: list[str] = []
        failed: list[dict] = []
        messages = 0
        index_entries = 0
        for conversation_id in ids:
            result = self.delete(conversation_id)
            if result.get("ok"):
                deleted.append(conversation_id)
                messages += int(result.get("messages") or 0)
                index_entries += int(result.get("indexEntries") or 0)
            else:
                failed.append({"id": conversation_id,
                               "error": str(result.get("error") or "delete_failed")})
        logger.info("Apagadas %d de %d conversas (%d mensagens)",
                    len(deleted), len(ids), messages)
        return {"ok": not failed, "removed": len(deleted), "requested": len(ids),
                "deleted": deleted, "failed": failed, "messages": messages,
                "indexEntries": index_entries}

    def delete_all(self) -> dict:
        """Clear every conversation. Long-term memories are untouched."""
        threads = self.list(limit=MAX_LIST_LIMIT, include_archived=True)
        removed = 0
        for thread in threads:
            if self.delete(thread["id"]).get("ok"):
                removed += 1
        # Legacy rows that were never back-filled into a thread would otherwise
        # survive an "apagar tudo" and reappear in the message count.
        try:
            with self._lock:
                self.conn.execute("DELETE FROM messages WHERE conversation_id IS NULL")
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a limpar mensagens sem conversa")
        return {"ok": True, "removed": removed}

    # ------------------------------------------------------------ messages

    def append(self, conversation_id: str, role: str, content: str, *,
               trust: str = TrustLevel.USER.value,
               metadata: dict | None = None,
               message_uid: str | None = None) -> dict | None:
        """Store one message and keep the thread's counters honest.

        Returns the stored row (with its integer id) or None when the message
        was not storable. A failure here is logged and swallowed: losing a
        message from the log must never lose the answer on screen.
        """
        role = str(role or "").strip()
        text = str(content or "")
        if role not in STORABLE_ROLES or not text.strip():
            return None
        if not self.exists(conversation_id):
            logger.warning("Mensagem descartada: conversa %r desconhecida", conversation_id)
            return None

        stamp = _now()
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "INSERT INTO messages (role, content, timestamp, metadata,"
                    " conversation_id, message_uid, trust) VALUES (?,?,?,?,?,?,?)",
                    (role, text, stamp, payload, str(conversation_id),
                     message_uid or new_id("msg"), str(trust)),
                )
                message_id = int(cursor.lastrowid)
                if role in DISPLAY_ROLES:
                    self.conn.execute(
                        "UPDATE conversations SET message_count = message_count + 1,"
                        " last_message_at=?, updated_at=? WHERE id=?",
                        (stamp, stamp, str(conversation_id)))
                else:
                    self.conn.execute(
                        "UPDATE conversations SET updated_at=? WHERE id=?",
                        (stamp, str(conversation_id)))
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a guardar mensagem na conversa %s", conversation_id)
            return None

        self._auto_title(conversation_id, role, text)
        self._index_message(message_id, conversation_id, role, text, stamp, trust)
        return {"id": message_id, "role": role, "content": text, "timestamp": stamp,
                "conversationId": str(conversation_id), "trust": str(trust)}

    def truncate_last_turn(self, conversation_id: str) -> dict:
        """Remove the thread's LAST user message and everything after it.

        This is what "tentar de novo" and "editar a última mensagem" stand on:
        the turn is taken back out of the thread, so the Brain's window, the
        stored transcript and the retrieval index all agree that it was never
        asked, and the caller can ask it again (the same text, or an edited one).

        ONLY THE LAST TURN, deliberately. Rewriting a message in the middle of a
        thread would leave every later answer replying to a question that no
        longer exists; branching is a different feature with a different model.
        Tool and system rows written during the turn go with it -- they belong to
        the answer being discarded. Long-term memories captured from the message
        are NOT touched: they are separate objects with their own lifecycle and
        their own delete in Memória, exactly as for a deleted thread.
        """
        conversation_id = str(conversation_id or "")
        if not self.exists(conversation_id):
            return {"ok": False, "error": "unknown_conversation"}
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT id, content, metadata FROM messages WHERE conversation_id=?"
                    " AND role='user' ORDER BY id DESC LIMIT 1", (conversation_id,)).fetchone()
                if row is None:
                    return {"ok": False, "error": "no_turn"}
                first_id, text = int(row[0]), str(row[1] or "")
                files = _attachments(row[2])
                removed_ids = [int(r[0]) for r in self.conn.execute(
                    "SELECT id FROM messages WHERE conversation_id=? AND id>=?",
                    (conversation_id, first_id)).fetchall()]
                self.conn.execute("DELETE FROM messages WHERE conversation_id=? AND id>=?",
                                  (conversation_id, first_id))
                count, last = self.conn.execute(
                    "SELECT COUNT(*), MAX(timestamp) FROM messages WHERE conversation_id=?"
                    " AND role IN ('user','assistant')", (conversation_id,)).fetchone()
                self.conn.execute(
                    "UPDATE conversations SET message_count=?, last_message_at=?, updated_at=?"
                    " WHERE id=?", (int(count or 0), last, _now(), conversation_id))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a desfazer a última vez na conversa %s", conversation_id)
            return {"ok": False, "error": "truncate_failed", "detail": str(exc)}
        if self.index is not None and removed_ids:
            self.index.remove_many(f"message:{mid}" for mid in removed_ids)
        return {"ok": True, "text": text, "attachments": files,
                "removed": len(removed_ids)}

    def _auto_title(self, conversation_id: str, role: str, text: str) -> None:
        """Name a thread after the user's first sentence. No model call.

        A generated title is worth exactly what it costs, and a round trip to a
        language model per new conversation is not worth it: the user's own
        opening line is a better title than a paraphrase of it, it is available
        instantly, it cannot fail, and it cannot be wrong. A manual rename wins
        permanently, because ``title_source`` becomes 'user' and this only ever
        updates rows still marked 'auto'.
        """
        if role != "user":
            return
        try:
            with self._lock:
                self.conn.execute(
                    "UPDATE conversations SET title=? WHERE id=? AND title_source='auto'"
                    " AND (title=? OR title='' OR title='Conversa')",
                    (title_from_text(text, fallback=DEFAULT_TITLE),
                     str(conversation_id), DEFAULT_TITLE))
                self.conn.commit()
        except sqlite3.Error:
            logger.debug("Não foi possível nomear a conversa automaticamente", exc_info=True)

    def _index_message(self, message_id: int, conversation_id: str, role: str,
                       text: str, stamp: str, trust: str) -> None:
        """Make one message findable later, scoped to its own thread.

        Only USER and ASSISTANT text is indexed, and only content that came from
        inside the trust boundary. A tool result carrying a fetched web page is
        UNTRUSTED_EXTERNAL: retrievable external text would let a page Nano read
        once keep re-entering the context on later turns, which is precisely the
        injection path the trust boundary exists to close.
        """
        if self.index is None or role not in DISPLAY_ROLES:
            return
        if str(trust) != TrustLevel.USER.value:
            return
        if len(text.strip()) < MIN_INDEXABLE_CHARS:
            return
        self.index.upsert(
            f"message:{message_id}", kind="message", scope=str(conversation_id),
            title="Utilizador" if role == "user" else "Nano", body=text,
            created_at=stamp,
            metadata={"messageId": message_id, "role": role,
                      "conversationId": str(conversation_id)})

    def messages(self, conversation_id: str, *, limit: int = 200,
                 roles: frozenset[str] | None = None,
                 attachment_text: bool = False) -> list[dict]:
        """The tail of one thread, oldest first. Bounded, always.

        A user row that carried files gets ``attachments``: name, size and
        whether it was cut, which is all the window shows. Only the Brain, which
        rebuilds the model's view of the thread, asks for their text too.
        """
        limit = max(1, min(int(limit), MAX_MESSAGE_LIMIT))
        wanted = roles or DISPLAY_ROLES
        marks = ",".join("?" * len(wanted))
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"SELECT id, role, content, timestamp, trust, metadata FROM messages"
                    f" WHERE conversation_id=? AND role IN ({marks})"
                    " ORDER BY id DESC LIMIT ?",
                    (str(conversation_id), *sorted(wanted), limit)).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a ler mensagens da conversa %s", conversation_id)
            return []
        out: list[dict] = []
        for row in reversed(rows):
            message = {"id": row[0], "role": row[1], "content": row[2],
                       "timestamp": row[3], "trust": row[4] or TrustLevel.USER.value}
            # WHICH PROVIDER ANSWERED THIS MESSAGE, months later.
            #
            # The column was always written and never read, so reopening a
            # thread showed no technical details at all -- and the panel that
            # did appear on a live turn described the CURRENT selection, which
            # is a different question. It is re-shaped on the way out as well as
            # on the way in: rows written before the allow-list existed hold
            # whatever the Brain's scratchpad happened to contain that day.
            meta = _message_meta(row[5])
            if meta:
                message["meta"] = meta
            files = _attachments(row[5]) if row[1] == "user" else []
            if files:
                message["attachments"] = files if attachment_text else [
                    {k: v for k, v in entry.items() if k != "text"} for entry in files]
            out.append(message)
        return out

    def message_count(self, conversation_id: str) -> int:
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE conversation_id=? AND role IN"
                    " ('user','assistant')", (str(conversation_id),)).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    def total_messages(self) -> int:
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE role IN ('user','assistant')"
                ).fetchone()
            return int(row[0]) if row else 0
        except sqlite3.Error:
            return 0

    # ------------------------------------------------------------- summary

    def get_summary(self, conversation_id: str) -> dict:
        try:
            with self._lock:
                row = self.conn.execute(
                    "SELECT summary, covered_through, covered_messages, generator,"
                    " updated_at FROM conversation_summaries WHERE conversation_id=?",
                    (str(conversation_id),)).fetchone()
        except sqlite3.Error:
            row = None
        if not row:
            return {"summary": "", "coveredThrough": 0, "coveredMessages": 0,
                    "generator": "", "updatedAt": ""}
        return {"summary": row[0] or "", "coveredThrough": int(row[1] or 0),
                "coveredMessages": int(row[2] or 0), "generator": row[3] or "",
                "updatedAt": row[4] or ""}

    def set_summary(self, conversation_id: str, summary: str, *,
                    covered_through: int, covered_messages: int,
                    generator: str = "extractive") -> bool:
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO conversation_summaries (conversation_id, summary,"
                    " covered_through, covered_messages, generator, updated_at)"
                    " VALUES (?,?,?,?,?,?)"
                    " ON CONFLICT(conversation_id) DO UPDATE SET summary=excluded.summary,"
                    "  covered_through=excluded.covered_through,"
                    "  covered_messages=excluded.covered_messages,"
                    "  generator=excluded.generator, updated_at=excluded.updated_at",
                    (str(conversation_id), str(summary), int(covered_through),
                     int(covered_messages), str(generator), _now()))
                self.conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("Falha a guardar o resumo da conversa %s", conversation_id)
            return False

    def messages_after(self, conversation_id: str, message_id: int, *,
                       limit: int = MAX_MESSAGE_LIMIT) -> list[dict]:
        """Messages newer than a watermark. The input to progressive compaction."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, role, content, timestamp FROM messages"
                    " WHERE conversation_id=? AND id > ? AND role IN ('user','assistant')"
                    " ORDER BY id LIMIT ?",
                    (str(conversation_id), int(message_id),
                     max(1, min(int(limit), MAX_MESSAGE_LIMIT)))).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a ler mensagens novas da conversa %s", conversation_id)
            return []
        return [{"id": r[0], "role": r[1], "content": r[2], "timestamp": r[3]} for r in rows]

    # --------------------------------------------------------- thread facts

    def add_fact(self, conversation_id: str, text: str, *, kind: str = "fact",
                 source_message_id: int | None = None,
                 trust: str = TrustLevel.USER.value) -> bool:
        clean = text_normalize.shorten(text, 300)
        if not clean or not self.exists(conversation_id):
            return False
        try:
            with self._lock:
                cursor = self.conn.execute(
                    "INSERT OR IGNORE INTO conversation_facts (id, conversation_id, text,"
                    " kind, trust, source_message_id, created_at) VALUES (?,?,?,?,?,?,?)",
                    (new_id("cfact"), str(conversation_id), clean, str(kind), str(trust),
                     source_message_id, _now()))
                self.conn.commit()
            return bool(cursor.rowcount)
        except sqlite3.Error:
            logger.exception("Falha a guardar facto da conversa")
            return False

    def facts(self, conversation_id: str, *, limit: int = 40) -> list[dict]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT id, text, kind, trust, source_message_id, created_at"
                    " FROM conversation_facts WHERE conversation_id=?"
                    " ORDER BY created_at DESC LIMIT ?",
                    (str(conversation_id), max(1, min(int(limit), 200)))).fetchall()
        except sqlite3.Error:
            return []
        return [{"id": r[0], "text": r[1], "kind": r[2], "trust": r[3],
                 "sourceMessageId": r[4], "createdAt": r[5]} for r in rows]

    # ------------------------------------------------------------- helpers

    def backfill_index(self, *, limit: int = 2000) -> int:
        """Index messages that predate the retrieval index.

        The migration gives every legacy message a thread, but a thread whose
        messages were never indexed cannot be searched -- so on an existing
        install, retrieval would have started working only for messages sent
        AFTER the upgrade, and "o que é que eu disse sobre X?" would have found
        nothing about anything said before it. That is not a failure anyone
        would see as a failure; it would just look like Nano not remembering.

        Bounded to the most recent `limit` messages: on a very long log the
        oldest turns are worth less than a fast start, and indexing is
        incremental from then on. Idempotent -- upsert keys on the message id.
        """
        if self.index is None:
            return 0
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT m.id, m.role, m.content, m.timestamp, m.conversation_id, m.trust"
                    "  FROM messages m"
                    "  LEFT JOIN retrieval_entries r"
                    "    ON r.entry_id = 'message:' || m.id"
                    " WHERE m.conversation_id IS NOT NULL AND r.entry_id IS NULL"
                    "   AND m.role IN ('user','assistant')"
                    " ORDER BY m.id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar mensagens por indexar")
            return 0

        indexed = 0
        for message_id, role, content, stamp, conversation_id, trust in rows:
            before = indexed
            self._index_message(int(message_id), str(conversation_id), str(role),
                                str(content or ""), str(stamp or _now()),
                                str(trust or TrustLevel.USER.value))
            indexed = before + 1
        if indexed:
            logger.info("Índice de recuperação preenchido com %d mensagem(ns) existentes",
                        indexed)
        return indexed

    def search_messages(self, query: str, *, conversation_id: str | None = None,
                        limit: int = 8, exclude_ids=None):
        """Retrieval over stored messages. Scoped to a thread when one is given."""
        if self.index is None:
            return []
        return self.index.search(query, kinds=["message"], scope=conversation_id,
                                 limit=limit, exclude_ids=exclude_ids)

    @staticmethod
    def _row_to_thread(row) -> dict:
        return {
            "id": row[0],
            "title": row[1] or DEFAULT_TITLE,
            "titleSource": row[2] or "auto",
            "createdAt": row[3],
            "updatedAt": row[4],
            "lastMessageAt": row[5],
            "messageCount": int(row[6] or 0),
            "archived": bool(row[7]),
            "metadata": _loads(row[8], {}),
            "pinned": bool((_loads(row[8], {}) or {}).get("pinned")),
            "project": str((_loads(row[8], {}) or {}).get("project") or ""),
        }


__all__ = ["DEFAULT_TITLE", "DISPLAY_ROLES", "ConversationStore"]
