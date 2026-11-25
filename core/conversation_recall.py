"""Memory M3: remembering old conversations and the decisions taken in them.

WHAT THIS ADDS
--------------
Long-term memory (``core.long_term_memory``) holds single facts the user asked
NANO to keep. That answers "what do you know about me?", but not "what did we
decide about the gaming PC last week?" -- the thread where that happened is
closed, and old turns are only ever retrieved from the conversation that is
open now (see ``ContextComposer._add_old_messages`` for why).

A RECAP closes that gap. Every conversation gets one row: what it was about,
the decisions taken in it and its topics, with its date and title as the
source. Recaps are indexed, so a new message that bears on an old conversation
brings that conversation's recap into the context -- labelled with where and
when it came from, so NANO can say "na conversa «PC novo», a 12 de novembro,
decidiste…" instead of presenting it as something it simply knows.

HOW A RECAP IS MADE
-------------------
With the same extractive summariser the thread summary uses
(``core.summarizer``), for the same reasons: it costs no tokens, cannot fail on
a rate limit, cannot invent anything, and only ever keeps sentences the USER
wrote -- never the assistant's prose, never anything inside an untrusted-content
fence, never anything that looks like a secret.

THE USER OWNS IT
----------------
Memória → Conversas antigas lists every recap. Each can be edited (the edit is
kept: a later refresh never overwrites what the user wrote), forgotten (the
recap is cleared and the conversation is never recapped again, until the user
says otherwise) or restored. Deleting the conversation deletes its recap, and
"Apagar todos os dados" empties the table with everything else.

Nothing here leaves the computer: it is a table and an index entry in nano.db.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from core import memory_safety, summarizer, text_normalize
from core.retrieval import RetrievalIndex

logger = logging.getLogger("nano.recall")

#: The retrieval-index kind for a recap. Its scope is the conversation id, so a
#: search can exclude the conversation that is already open.
RECAP_KIND = "recap"

#: A conversation needs at least this many user messages to be worth recalling.
#: "olá" / "obrigado" is not a conversation anyone will ask about.
MIN_USER_MESSAGES = 2

#: Read at most this many messages to build one recap. The newest win: a very
#: long thread is recalled for what it ended up deciding.
MAX_MESSAGES_READ = 600

MAX_DECISIONS = 6
MAX_TOPICS = 8
MAX_SUMMARY_CHARS = 420
MAX_DECISION_CHARS = 220

#: Questions that ask about the past. When one matches, recall searches harder
#: (more recaps, a lower bar, and the exact old messages too).
_RECALL_QUESTION = re.compile(
    r"\b(lembras?-?te|te lembras|recordas?-?te|recordas|lembra-te do que|"
    r"(da|na) (ultima|última|outra) vez|"
    r"o que (e que |é que )?(decidimos|decidi|combinamos|combinámos|combinei|falamos|"
    r"falámos|dissemos|disse|escolhemos|escolhi|ficou decidido|tinhamos|tínhamos)|"
    r"ja (falamos|falámos)|já (falamos|falámos)|"
    r"(conversa|conversamos|conversámos) (anterior|passada|antiga|de ontem|da semana)|"
    r"(semana|mes|mês) passad[oa]|qual foi a decis[aã]o|o que ficou (decidido|combinado)|"
    r"do you remember|remember when|last time|what did (we|i) (decide|say|choose|pick|"
    r"talk about|discuss|agree)|we (talked|spoke) about|(previous|earlier|past|last) "
    r"(conversation|chat)|what was decided|what we decided)\b",
    re.I)


#: Capitalised words that start sentences, not topics: "Quero", "Decidi",
#: "Certo". The summariser's topic line is a capitalisation heuristic, which is
#: fine inside a thread summary and noise in a list the user reads.
_NOT_TOPICS = frozenset("""
quero queria tenho tinha decidi decidimos vamos vou certo otimo ótimo boa bom sim
nao não ok okay obrigado obrigada preciso gosto como qual quais que onde quando
porque porquê sera será pode podes consegues ajuda ajuda-me olá ola entao então
agora hoje amanha amanhã ontem tambem também mas e ou se isto isso este esta
fica ficamos escolhi optei prefiro uso estou the i we my can could what how why
when where let's lets yes no thanks please also but and so ok great sure
""".split())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_recall_question(text: str) -> bool:
    """Whether the message asks about an earlier conversation."""
    return bool(_RECALL_QUESTION.search(str(text or "")))


def _loads(raw: Any) -> list[str]:
    try:
        value = json.loads(raw) if raw else []
    except (ValueError, TypeError):
        return []
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def _clean_lines(items: Any, *, limit: int, chars: int) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if isinstance(items, str):
        items = items.splitlines()
    for raw in list(items or []):
        line = " ".join(str(raw or "").split()).lstrip("-• ").strip()
        if not line:
            continue
        key = text_normalize.normalize(line)
        if key in seen:
            continue
        seen.add(key)
        out.append(text_normalize.shorten(line, chars))
        if len(out) >= limit:
            break
    return out


def build_recap(messages: list[dict]) -> dict:
    """Summary, decisions and topics of one conversation. Pure and deterministic.

    Returns empty fields when the conversation has nothing worth recalling.
    """
    user_turns = [m for m in messages if str(m.get("role")) == "user"
                  and str(m.get("content") or "").strip()]
    result = summarizer.rebuild(messages) if messages else None
    summary_parts: list[str] = []
    decisions: list[str] = []
    topics: list[str] = []
    if result is not None:
        decisions = _clean_lines(result.decisions, limit=MAX_DECISIONS,
                                 chars=MAX_DECISION_CHARS)
        summary_parts.extend(result.goals[:2])
        summary_parts.extend(result.facts[:2])
        # Topics from what the USER said only, without sentence starters.
        raw_topics = summarizer._topics(user_turns, [])  # noqa: SLF001 - same heuristic
        topics = _clean_lines(
            [topic for topic in raw_topics
             if (text_normalize.normalize(topic).split() or [""])[0] not in _NOT_TOPICS],
            limit=MAX_TOPICS, chars=60)
        if not summary_parts and result.questions:
            summary_parts.append(result.questions[0])
    if not summary_parts and user_turns:
        # Nothing classified -- fall back to what the person opened with, which
        # is also what the automatic title was made from. Skipped when it holds
        # fenced external content or a secret, like everything else here.
        for turn in user_turns:
            sentences = summarizer._sentences(turn.get("content"))  # noqa: SLF001 - same rules
            safe = [s for s in sentences if not memory_safety.secret_reason(s)]
            if safe:
                if text_normalize.shorten(safe[0], MAX_DECISION_CHARS) not in decisions:
                    summary_parts.append(safe[0])
                break
    summary = text_normalize.shorten(" ".join(summary_parts), MAX_SUMMARY_CHARS)
    return {"summary": summary, "decisions": decisions, "topics": topics,
            "userMessages": len(user_turns)}


def _short_date(value: str | None) -> str:
    text = str(value or "")
    return text[:10] if len(text) >= 10 else text


class ConversationRecaps:
    """The recap table and its index entries. Never raises into the chat path."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock,
                 index: RetrievalIndex | None = None):
        self.conn = conn
        self._lock = lock
        self.index = index

    # ------------------------------------------------------------- reading

    _SELECT = (
        "SELECT r.conversation_id, c.title, r.summary, r.decisions, r.topics,"
        " r.message_count, c.created_at, c.last_message_at, r.edited, r.excluded,"
        " r.updated_at FROM conversation_recaps r"
        " JOIN conversations c ON c.id = r.conversation_id")

    @staticmethod
    def _row(row) -> dict:
        return {
            "conversationId": row[0],
            "title": row[1] or "",
            "summary": row[2] or "",
            "decisions": _loads(row[3]),
            "topics": _loads(row[4]),
            "messageCount": int(row[5] or 0),
            "startedAt": row[6] or "",
            "lastMessageAt": row[7] or row[6] or "",
            "edited": bool(row[8]),
            "forgotten": bool(row[9]),
            "updatedAt": row[10] or "",
        }

    def get(self, conversation_id: str) -> dict | None:
        try:
            with self._lock:
                row = self.conn.execute(
                    f"{self._SELECT} WHERE r.conversation_id=?",
                    (str(conversation_id),)).fetchone()
        except sqlite3.Error:
            logger.exception("Falha a ler a recordação da conversa %s", conversation_id)
            return None
        return self._row(row) if row else None

    def list(self, *, query: str = "", include_forgotten: bool = True,
             limit: int = 200) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if not include_forgotten:
            clauses.append("r.excluded = 0")
        needle = str(query or "").strip()
        if needle:
            clauses.append("(c.title LIKE ? OR r.summary LIKE ? OR r.decisions LIKE ?"
                           " OR r.topics LIKE ?)")
            params.extend([f"%{needle}%"] * 4)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        try:
            with self._lock:
                rows = self.conn.execute(
                    f"{self._SELECT}{where}"
                    " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?",
                    [*params, max(1, min(int(limit), 500))]).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar recordações")
            return []
        return [self._row(row) for row in rows]

    def counts(self) -> dict:
        # Counted in Python rather than with json_array_length: JSON1 is not
        # guaranteed in every SQLite a Windows Python ships with.
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT excluded, decisions FROM conversation_recaps").fetchall()
        except sqlite3.Error:
            return {"recaps": 0, "forgotten": 0, "decisions": 0}
        forgotten = sum(1 for excluded, _ in rows if excluded)
        decisions = sum(len(_loads(raw)) for excluded, raw in rows if not excluded)
        return {"recaps": len(rows) - forgotten, "forgotten": forgotten,
                "decisions": decisions}

    def search(self, query: str, *, exclude_conversation: str | None = None,
               limit: int = 3, min_score: float | None = None):
        """Recaps relevant to `query`, best first, never from the open thread."""
        if self.index is None:
            return []
        kwargs: dict[str, Any] = {"kinds": [RECAP_KIND], "limit": limit}
        if exclude_conversation:
            kwargs["exclude_scope"] = str(exclude_conversation)
        if min_score is not None:
            kwargs["min_score"] = float(min_score)
        return self.index.search(query, **kwargs)

    # ------------------------------------------------------------- writing

    def refresh(self, conversation_id: str, messages: list[dict], *,
                title: str = "") -> dict | None:
        """Recompute one conversation's recap from its messages.

        A forgotten recap stays forgotten, and an edited one keeps the user's
        text: only its message count moves. Returns the stored recap, or None
        when there is nothing worth recalling.
        """
        conversation_id = str(conversation_id or "")
        if not conversation_id:
            return None
        current = self.get(conversation_id)
        if current and current["forgotten"]:
            return current
        count = len(messages)
        if current and current["messageCount"] == count and not current["edited"]:
            return current
        recap = build_recap(messages[-MAX_MESSAGES_READ:])
        if recap["userMessages"] < MIN_USER_MESSAGES or not (
                recap["summary"] or recap["decisions"]):
            if current and not current["edited"]:
                self._delete_row(conversation_id)
            return None
        covered = max((int(m.get("id") or 0) for m in messages), default=0)
        stamp = _now()
        try:
            with self._lock:
                if current and current["edited"]:
                    self.conn.execute(
                        "UPDATE conversation_recaps SET message_count=?, covered_through=?"
                        " WHERE conversation_id=?", (count, covered, conversation_id))
                else:
                    self.conn.execute(
                        "INSERT INTO conversation_recaps (conversation_id, summary, decisions,"
                        " topics, message_count, covered_through, edited, excluded, updated_at)"
                        " VALUES (?,?,?,?,?,?,0,0,?)"
                        " ON CONFLICT(conversation_id) DO UPDATE SET summary=excluded.summary,"
                        "  decisions=excluded.decisions, topics=excluded.topics,"
                        "  message_count=excluded.message_count,"
                        "  covered_through=excluded.covered_through,"
                        "  updated_at=excluded.updated_at"
                        # An edit or a "forget" that landed while this
                        # background refresh was reading wins over it.
                        " WHERE conversation_recaps.edited = 0 AND conversation_recaps.excluded = 0",
                        (conversation_id, recap["summary"],
                         json.dumps(recap["decisions"], ensure_ascii=False),
                         json.dumps(recap["topics"], ensure_ascii=False),
                         count, covered, stamp))
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a guardar a recordação da conversa %s", conversation_id)
            return None
        stored = self.get(conversation_id)
        if stored and not stored["forgotten"]:
            self._index(stored, title=title)
        return stored

    def update(self, conversation_id: str, *, summary: str | None = None,
               decisions: Any = None) -> dict:
        """The user's edit from Memória → Conversas antigas. Kept from then on."""
        current = self.get(conversation_id)
        if current is None:
            return {"ok": False, "error": "unknown_recap"}
        new_summary = current["summary"] if summary is None else text_normalize.shorten(
            " ".join(str(summary).split()), MAX_SUMMARY_CHARS * 2)
        new_decisions = current["decisions"] if decisions is None else _clean_lines(
            decisions, limit=MAX_DECISIONS * 2, chars=MAX_DECISION_CHARS)
        for text in [new_summary, *new_decisions]:
            reason = memory_safety.secret_reason(text)
            if reason:
                return {"ok": False, "error": "secret", "detail": reason}
        try:
            with self._lock:
                self.conn.execute(
                    "UPDATE conversation_recaps SET summary=?, decisions=?, edited=1,"
                    " excluded=0, updated_at=? WHERE conversation_id=?",
                    (new_summary, json.dumps(new_decisions, ensure_ascii=False), _now(),
                     str(conversation_id)))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a editar a recordação %s", conversation_id)
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        stored = self.get(conversation_id)
        if stored:
            if stored["summary"] or stored["decisions"]:
                self._index(stored)
            elif self.index is not None:
                self.index.remove(f"{RECAP_KIND}:{conversation_id}")
        return {"ok": True, "recap": stored}

    def forget(self, conversation_id: str) -> dict:
        """Clear one recap and never recap that conversation again (until restored)."""
        if self.get(conversation_id) is None:
            return {"ok": False, "error": "unknown_recap"}
        try:
            with self._lock:
                self.conn.execute(
                    "UPDATE conversation_recaps SET summary='', decisions='[]', topics='[]',"
                    " edited=0, excluded=1, updated_at=? WHERE conversation_id=?",
                    (_now(), str(conversation_id)))
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a esquecer a recordação %s", conversation_id)
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        if self.index is not None:
            self.index.remove(f"{RECAP_KIND}:{conversation_id}")
        return {"ok": True, "recap": self.get(conversation_id)}

    def restore(self, conversation_id: str) -> dict:
        """Allow a forgotten conversation to be recapped again. The caller refreshes."""
        if self.get(conversation_id) is None:
            return {"ok": False, "error": "unknown_recap"}
        try:
            with self._lock:
                self.conn.execute(
                    "UPDATE conversation_recaps SET excluded=0, edited=0, message_count=-1,"
                    " updated_at=? WHERE conversation_id=?", (_now(), str(conversation_id)))
                self.conn.commit()
        except sqlite3.Error as exc:
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        return {"ok": True}

    def forget_all(self) -> dict:
        """Forget every existing conversation. Conversations started later are recalled.

        Every conversation that exists now is marked, not only the ones that
        already had a recap: one with no recap yet (a single message, made
        while recall was off, or past the back-fill's 200) was otherwise
        quoted on the next "lembras-te…?" and recapped by the next back-fill.
        """
        try:
            with self._lock:
                stamp = _now()
                cursor = self.conn.execute(
                    "UPDATE conversation_recaps SET summary='', decisions='[]', topics='[]',"
                    " edited=0, excluded=1, updated_at=? WHERE excluded=0", (stamp,))
                forgotten = cursor.rowcount or 0
                cursor = self.conn.execute(
                    "INSERT INTO conversation_recaps (conversation_id, summary, decisions, topics,"
                    " message_count, covered_through, edited, excluded, updated_at)"
                    " SELECT c.id, '', '[]', '[]', 0, 0, 0, 1, ? FROM conversations c"
                    " LEFT JOIN conversation_recaps r ON r.conversation_id = c.id"
                    " WHERE r.conversation_id IS NULL", (stamp,))
                forgotten += cursor.rowcount or 0
                self.conn.commit()
        except sqlite3.Error as exc:
            logger.exception("Falha a esquecer as recordações")
            return {"ok": False, "error": "write_failed", "detail": str(exc)}
        if self.index is not None:
            self.index.clear_kind(RECAP_KIND)
        return {"ok": True, "forgotten": forgotten}

    def delete(self, conversation_id: str) -> None:
        """The conversation itself is gone: its recap goes with it."""
        self._delete_row(conversation_id)

    def _delete_row(self, conversation_id: str) -> None:
        try:
            with self._lock:
                self.conn.execute("DELETE FROM conversation_recaps WHERE conversation_id=?",
                                  (str(conversation_id),))
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a apagar a recordação %s", conversation_id)
        if self.index is not None:
            self.index.remove(f"{RECAP_KIND}:{conversation_id}")

    def excluded_ids(self) -> set[str]:
        """Every conversation the user told NANO not to recall."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT conversation_id FROM conversation_recaps WHERE excluded = 1").fetchall()
        except sqlite3.Error:
            logger.exception("Falha a ler as conversas esquecidas")
            return set()
        return {row[0] for row in rows}

    def missing(self, *, limit: int = 200) -> list[str]:
        """Conversations with messages and no recap yet. For the back-fill.

        Includes one restored ("Voltar a lembrar") while recall was off: its
        row is empty and waits for a rebuild (message_count -1)."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT c.id FROM conversations c"
                    " LEFT JOIN conversation_recaps r ON r.conversation_id = c.id"
                    " WHERE (r.conversation_id IS NULL OR (r.excluded = 0 AND r.message_count < 0))"
                    " AND c.message_count >= ?"
                    " ORDER BY COALESCE(c.last_message_at, c.created_at) DESC LIMIT ?",
                    (MIN_USER_MESSAGES * 2 - 1, max(1, int(limit)))).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a procurar conversas sem recordação")
            return []
        return [row[0] for row in rows]

    def _index(self, recap: dict, *, title: str = "") -> None:
        if self.index is None:
            return
        body_parts = [recap["summary"], *recap["decisions"], " ".join(recap["topics"])]
        self.index.upsert(
            f"{RECAP_KIND}:{recap['conversationId']}", kind=RECAP_KIND,
            title=title or recap["title"],
            body="\n".join(part for part in body_parts if part),
            scope=recap["conversationId"],
            metadata={"conversationId": recap["conversationId"],
                      "date": _short_date(recap["lastMessageAt"])})


def describe(recap: dict) -> str:
    """One line of recalled context, with its source first."""
    date = _short_date(recap.get("lastMessageAt"))
    parts: list[str] = [f"«{recap.get('title') or '—'}» ({date})"]
    if recap.get("decisions"):
        parts.append("decisões: " + "; ".join(recap["decisions"][:3]))
    if recap.get("summary"):
        parts.append("resumo: " + text_normalize.shorten(recap["summary"], 220))
    return " — ".join(parts)


__all__ = [
    "RECAP_KIND",
    "ConversationRecaps",
    "build_recap",
    "describe",
    "is_recall_question",
]
