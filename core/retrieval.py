"""Local retrieval over Nano's own memory: messages, memories, knowledge nodes.

WHAT THIS IS, STATED HONESTLY
----------------------------
This is **lexical** retrieval — SQLite FTS5 with BM25 ranking, plus a
token-overlap signal — not embedding search. That is a deliberate choice, not a
placeholder:

* Nano ships to ordinary consumer Windows machines and must work offline. Every
  embedding option worth having either downloads hundreds of megabytes on first
  use or bills a cloud API per call, and the brief for this phase rules both
  out.
* The queries this serves are short, concrete and in the user's own words
  ("a minha placa gráfica", "o que decidimos sobre o Ollama"). Recovering the
  earlier turn that used the same words is exactly what BM25 is good at.
* There is no second store to keep consistent, no index to rebuild after a
  crash, and no component that can be "unavailable" while the database is up.

So nothing in Nano claims semantic search, and the Memory UI names the mechanism
it actually uses. ``search()`` is the seam a semantic backend would slot into
later: callers pass a query and get scored hits with provenance, and none of
them know how the score was produced.

DEGRADATION
-----------
FTS5 is compiled into essentially every SQLite build, but not provably all of
them. If the virtual table cannot be created, ``search`` falls back to a bounded
token ``LIKE`` scan over the same rows. The results are worse; nothing breaks,
and ``stats()`` reports which mode is live so the UI can say so rather than
imply full retrieval.

THE INDEX IS DERIVED AND DISPOSABLE
-----------------------------------
Every row in ``retrieval_entries`` is a copy of something that already exists in
``messages``, ``memories`` or ``knowledge_nodes``. Losing it costs retrieval
quality until the next write, never data — which is why an indexing failure is
logged and swallowed instead of failing the write that triggered it.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from core import text_normalize

logger = logging.getLogger("nano.retrieval")

#: Below this combined score a hit is noise. Returning it would spend context
#: budget on something the user never mentioned.
DEFAULT_MIN_SCORE = 0.18

#: How many rows to pull from FTS before filtering and re-ranking. Bounded so a
#: broad query cannot turn into a table scan.
_OVERFETCH = 6
_MAX_CANDIDATES = 120

#: Tokens longer than this get a prefix match, so "graficas" finds "grafica".
_PREFIX_MIN = 5
_MAX_QUERY_TOKENS = 12


@dataclass
class RetrievalHit:
    entry_id: str
    kind: str
    scope: str
    title: str
    body: str
    score: float
    created_at: str = ""
    metadata: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "id": self.entry_id,
            "kind": self.kind,
            "scope": self.scope,
            "title": self.title,
            "body": self.body,
            "score": round(self.score, 4),
            "createdAt": self.created_at,
            "metadata": self.metadata,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_match_query(query: str) -> str:
    """Turn free text into an FTS5 MATCH expression, or "" if there is nothing.

    Every token is quoted, so punctuation and FTS operators the user typed
    ("AND", "*", "-", a stray quote) are data rather than syntax. That is a
    correctness point AND a safety one: an unquoted apostrophe in "não" is an
    FTS syntax error, and a message containing `NEAR(` would otherwise change
    the meaning of the search.
    """
    found: list[str] = []
    for token in text_normalize.tokens(query):
        if token in found:
            continue
        found.append(token)
        if len(found) >= _MAX_QUERY_TOKENS:
            break
    if not found:
        return ""
    parts = [f'"{token}"*' if len(token) >= _PREFIX_MIN else f'"{token}"' for token in found]
    return " OR ".join(parts)


class RetrievalIndex:
    """The single lexical index over everything Nano can recall.

    Shares the caller's connection and lock: one database, one WAL, one writer
    discipline. It never opens a connection of its own.
    """

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self.conn = conn
        self._lock = lock
        self.fts_available = False
        self._ensure_fts()

    # ------------------------------------------------------------- lifecycle

    def _ensure_fts(self) -> None:
        try:
            with self._lock:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5("
                    "entry_id UNINDEXED, title, body, "
                    "tokenize=\"unicode61 remove_diacritics 2\")"
                )
                self.conn.commit()
            self.fts_available = True
        except sqlite3.Error:
            # Older SQLite builds reject remove_diacritics 2. Try the plain
            # tokenizer before concluding FTS is unavailable: accent folding is
            # a nicety, full-text search is not.
            try:
                with self._lock:
                    self.conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS retrieval_fts USING fts5("
                        "entry_id UNINDEXED, title, body)"
                    )
                    self.conn.commit()
                self.fts_available = True
            except sqlite3.Error:
                self.fts_available = False
                logger.warning(
                    "SQLite FTS5 indisponível: a pesquisa de memória usa "
                    "correspondência textual simples.")

    def stats(self) -> dict:
        """What the index actually holds. Counts only — never user text."""
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT kind, COUNT(*) FROM retrieval_entries GROUP BY kind"
                ).fetchall()
            by_kind = {str(kind): int(count) for kind, count in rows}
        except sqlite3.Error:
            by_kind = {}
        return {
            "mode": "fts5" if self.fts_available else "text",
            "engine": "SQLite FTS5 (BM25)" if self.fts_available
                      else "correspondência textual simples",
            "entries": sum(by_kind.values()),
            "byKind": by_kind,
        }

    # ----------------------------------------------------------------- write

    def upsert(self, entry_id: str, *, kind: str, title: str = "", body: str = "",
               scope: str = "", metadata: dict | None = None,
               created_at: str | None = None) -> bool:
        """Index one entry. Never raises: indexing must not fail a real write."""
        entry_id = str(entry_id or "").strip()
        if not entry_id:
            return False
        text = str(body or "").strip()
        heading = str(title or "").strip()
        if not text and not heading:
            return self.remove(entry_id)
        stamp = created_at or _now()
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO retrieval_entries"
                    " (entry_id, kind, scope, title, body, created_at, metadata)"
                    " VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(entry_id) DO UPDATE SET"
                    "  kind=excluded.kind, scope=excluded.scope, title=excluded.title,"
                    "  body=excluded.body, metadata=excluded.metadata",
                    (entry_id, str(kind), str(scope or ""), heading, text, stamp, payload),
                )
                if self.fts_available:
                    self.conn.execute("DELETE FROM retrieval_fts WHERE entry_id=?", (entry_id,))
                    self.conn.execute(
                        "INSERT INTO retrieval_fts (entry_id, title, body) VALUES (?,?,?)",
                        (entry_id, heading, text),
                    )
                self.conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("Falha a indexar a entrada '%s'", entry_id)
            return False

    def remove(self, entry_id: str) -> bool:
        try:
            with self._lock:
                self.conn.execute("DELETE FROM retrieval_entries WHERE entry_id=?", (entry_id,))
                if self.fts_available:
                    self.conn.execute("DELETE FROM retrieval_fts WHERE entry_id=?", (entry_id,))
                self.conn.commit()
            return True
        except sqlite3.Error:
            logger.exception("Falha a remover a entrada '%s' do índice", entry_id)
            return False

    def remove_many(self, entry_ids: Iterable[str]) -> int:
        ids = [str(value) for value in entry_ids if value]
        if not ids:
            return 0
        removed = 0
        try:
            with self._lock:
                for chunk_start in range(0, len(ids), 400):
                    chunk = ids[chunk_start:chunk_start + 400]
                    marks = ",".join("?" * len(chunk))
                    cursor = self.conn.execute(
                        f"DELETE FROM retrieval_entries WHERE entry_id IN ({marks})", chunk)
                    removed += cursor.rowcount or 0
                    if self.fts_available:
                        self.conn.execute(
                            f"DELETE FROM retrieval_fts WHERE entry_id IN ({marks})", chunk)
                self.conn.commit()
        except sqlite3.Error:
            logger.exception("Falha a remover entradas do índice")
        return removed

    def remove_scope(self, kind: str, scope: str) -> int:
        """Drop every entry of one kind inside one scope (e.g. a whole thread).

        This is what keeps a deleted conversation from leaving orphaned index
        rows that would still surface in search results.
        """
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT entry_id FROM retrieval_entries WHERE kind=? AND scope=?",
                    (str(kind), str(scope)),
                ).fetchall()
        except sqlite3.Error:
            logger.exception("Falha a listar entradas do scope '%s'", scope)
            return 0
        return self.remove_many(row[0] for row in rows)

    def clear_kind(self, kind: str) -> int:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT entry_id FROM retrieval_entries WHERE kind=?", (str(kind),)
                ).fetchall()
        except sqlite3.Error:
            return 0
        return self.remove_many(row[0] for row in rows)

    # ---------------------------------------------------------------- search

    def search(self, query: str, *, kinds: Sequence[str] | None = None,
               scope: str | None = None, exclude_scope: str | None = None,
               limit: int = 6, min_score: float = DEFAULT_MIN_SCORE,
               exclude_ids: Iterable[str] | None = None) -> list[RetrievalHit]:
        """Best matches for `query`, filtered by kind and conversation scope.

        `scope` is the conversation-isolation control and it is applied in SQL,
        not after ranking: a search scoped to one thread can never return a row
        belonging to another, however well it scores.
        """
        limit = max(1, min(int(limit), 25))
        blocked = {str(value) for value in (exclude_ids or ())}
        candidates = (self._fts_candidates if self.fts_available else self._like_candidates)(
            query, kinds=kinds, scope=scope, exclude_scope=exclude_scope,
            limit=limit * _OVERFETCH)

        hits: list[RetrievalHit] = []
        seen: set[str] = set()
        for row in candidates:
            entry_id, kind, row_scope, title, body, created_at, raw_meta, rank = row
            if entry_id in blocked:
                continue
            text = f"{title} {body}".strip()
            overlap = text_normalize.overlap_score(query, text)
            score = 0.55 * _bm25_to_score(rank) + 0.45 * overlap
            if score < min_score:
                continue
            fingerprint = text_normalize.normalize(body)[:180]
            if fingerprint and fingerprint in seen:
                continue
            if fingerprint:
                seen.add(fingerprint)
            try:
                metadata = json.loads(raw_meta) if raw_meta else {}
            except (ValueError, TypeError):
                metadata = {}
            hits.append(RetrievalHit(
                entry_id=entry_id, kind=str(kind), scope=str(row_scope or ""),
                title=str(title or ""), body=str(body or ""), score=score,
                created_at=str(created_at or ""),
                metadata=metadata if isinstance(metadata, dict) else {}))

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def _filters(self, kinds: Sequence[str] | None, scope: str | None,
                 exclude_scope: str | None) -> tuple[str, list]:
        clauses: list[str] = []
        params: list = []
        if kinds:
            marks = ",".join("?" * len(kinds))
            clauses.append(f"e.kind IN ({marks})")
            params.extend(str(kind) for kind in kinds)
        if scope is not None:
            clauses.append("e.scope = ?")
            params.append(str(scope))
        if exclude_scope is not None:
            clauses.append("e.scope <> ?")
            params.append(str(exclude_scope))
        return (" AND " + " AND ".join(clauses) if clauses else ""), params

    def _fts_candidates(self, query: str, *, kinds, scope, exclude_scope, limit):
        match = build_match_query(query)
        if not match:
            return []
        where, params = self._filters(kinds, scope, exclude_scope)
        sql = (
            "SELECT e.entry_id, e.kind, e.scope, e.title, e.body, e.created_at,"
            "       e.metadata, bm25(retrieval_fts, 2.0, 1.0) AS rank"
            "  FROM retrieval_fts"
            "  JOIN retrieval_entries e ON e.entry_id = retrieval_fts.entry_id"
            f" WHERE retrieval_fts MATCH ?{where}"
            "  ORDER BY rank LIMIT ?"
        )
        try:
            with self._lock:
                return self.conn.execute(
                    sql, [match, *params, min(int(limit), _MAX_CANDIDATES)]).fetchall()
        except sqlite3.Error:
            logger.exception("Pesquisa FTS falhou; a usar correspondência simples")
            return self._like_candidates(query, kinds=kinds, scope=scope,
                                         exclude_scope=exclude_scope, limit=limit)

    def _like_candidates(self, query: str, *, kinds, scope, exclude_scope, limit):
        """Bounded LIKE scan. The honest fallback when FTS5 is not there."""
        query_tokens = text_normalize.tokens(query)[:6]
        if not query_tokens:
            return []
        where, params = self._filters(kinds, scope, exclude_scope)
        likes = " OR ".join(["(e.title LIKE ? OR e.body LIKE ?)"] * len(query_tokens))
        like_params: list = []
        for token in query_tokens:
            like_params.extend([f"%{token}%", f"%{token}%"])
        sql = (
            "SELECT e.entry_id, e.kind, e.scope, e.title, e.body, e.created_at,"
            "       e.metadata, 0.0 AS rank"
            f" FROM retrieval_entries e WHERE ({likes}){where}"
            "  ORDER BY e.created_at DESC LIMIT ?"
        )
        try:
            with self._lock:
                return self.conn.execute(
                    sql, [*like_params, *params, min(int(limit), _MAX_CANDIDATES)]).fetchall()
        except sqlite3.Error:
            logger.exception("Pesquisa textual simples falhou")
            return []


def _bm25_to_score(rank) -> float:
    """Map SQLite's BM25 output onto 0..1, where 1 is the best match.

    bm25() returns a NEGATIVE number and smaller (more negative) is better, so
    it cannot be used as a score directly. The LIKE fallback passes 0.0, which
    lands on a neutral 0.5 and lets the overlap term do the ranking.
    """
    try:
        value = float(rank)
    except (TypeError, ValueError):
        return 0.5
    if value == 0.0:
        return 0.5
    strength = -value
    if strength <= 0:
        return 0.25
    return min(1.0, strength / (strength + 4.0) + 0.25)


__all__ = ["DEFAULT_MIN_SCORE", "RetrievalHit", "RetrievalIndex", "build_match_query"]
