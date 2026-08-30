"""Nano memory: SQLite history, facts and lightweight local RAG."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from core.app_paths import DATA_DIR

logger = logging.getLogger("helios.memory")
DB_PATH, FACT_PREFIX = DATA_DIR / "helios.db", "fact:"


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[:max_chars] + " […]"


def _fts_query(query: str) -> str:
    tokens = re.findall(r"[\wÀ-ÿ]+", query.lower(), flags=re.UNICODE)
    tokens = list(dict.fromkeys(t for t in tokens if len(t) > 1))[:16]
    return " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)


class MemoryEngine:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=10)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=10000")
        self._fts_available = True
        self._init_db()

    def _init_db(self):
        with self._lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS messages ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, "
                "content TEXT NOT NULL, timestamp TEXT NOT NULL, metadata TEXT DEFAULT '{}')"
            )
            self.conn.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp)")
            try:
                self.conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5("
                    "doc_key UNINDEXED, text, metadata UNINDEXED)"
                )
            except sqlite3.OperationalError:
                self._fts_available = False
                logger.warning("SQLite FTS5 indisponível; RAG usa pesquisa textual simples.")
            self.conn.commit()

    def save_message(self, role: str, content: str, metadata: dict | None = None):
        if not content or role not in {"user", "assistant", "tool", "system"}:
            return
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT INTO messages (role, content, timestamp, metadata) VALUES (?,?,?,?)",
                    (role, content, datetime.now(timezone.utc).isoformat(), json.dumps(metadata or {}, ensure_ascii=False)),
                )
                self.conn.commit()
        except Exception:
            logger.exception("Erro ao guardar mensagem")

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT role, content, timestamp FROM messages ORDER BY id DESC LIMIT ?",
                    (max(1, int(limit)),),
                ).fetchall()
            return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]
        except Exception:
            logger.exception("Erro ao ler mensagens")
            return []

    def get_context_window(self, limit: int = 20, max_chars: int = 8000) -> list[dict]:
        msgs = [
            {"role": m["role"], "content": _truncate(m["content"], max_chars // 4)}
            for m in self.get_recent_messages(limit)
            if m["role"] in ("user", "assistant") and (m["content"] or "").strip()
        ]
        window, total = [], 0
        for msg in reversed(msgs):
            size = len(msg["content"])
            if total + size > max_chars:
                break
            window.append(msg)
            total += size
        window.reverse()
        while window and window[0]["role"] != "user":
            window.pop(0)
        return window

    def count_messages(self) -> int:
        try:
            with self._lock:
                row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def set_preference(self, key: str, value: Any):
        try:
            with self._lock:
                self.conn.execute(
                    "INSERT OR REPLACE INTO preferences (key, value) VALUES (?,?)",
                    (key, json.dumps(value, ensure_ascii=False)),
                )
                self.conn.commit()
        except Exception:
            logger.exception("Erro ao guardar preferência")

    def get_preference(self, key: str, default: Any = None) -> Any:
        try:
            with self._lock:
                row = self.conn.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        except Exception:
            logger.exception("Erro ao ler preferência '%s'", key)
            return default

    def delete_preference(self, key: str) -> bool:
        try:
            with self._lock:
                cur = self.conn.execute("DELETE FROM preferences WHERE key=?", (key,))
                self.conn.commit()
            return cur.rowcount > 0
        except Exception:
            logger.exception("Erro ao apagar preferência")
            return False

    def set_fact(self, key: str, value: Any):
        self.set_preference(f"{FACT_PREFIX}{key.strip().lower()}", value)

    def get_fact(self, key: str, default: Any = None):
        return self.get_preference(f"{FACT_PREFIX}{key.strip().lower()}", default)

    def forget_fact(self, key: str) -> bool:
        return self.delete_preference(f"{FACT_PREFIX}{key.strip().lower()}")

    def get_facts(self) -> dict[str, Any]:
        try:
            with self._lock:
                rows = self.conn.execute(
                    "SELECT key, value FROM preferences WHERE key LIKE ? ORDER BY key",
                    (f"{FACT_PREFIX}%",),
                ).fetchall()
            facts = {}
            for key, raw in rows:
                try:
                    facts[key[len(FACT_PREFIX):]] = json.loads(raw)
                except json.JSONDecodeError:
                    facts[key[len(FACT_PREFIX):]] = raw
            return facts
        except Exception:
            logger.exception("Erro ao ler factos")
            return {}

    def set_user_profile(self, profile: dict[str, Any]) -> None:
        self.set_preference("nano.user_profile", profile)

    def get_user_profile(self) -> dict[str, Any]:
        profile = self.get_preference("nano.user_profile", {})
        if not isinstance(profile, dict):
            return {}
        return profile

    def remember_preference(self, key: str, value: Any, source: str = "user") -> None:
        profile = self.get_user_profile()
        profile[key] = {"value": value, "source": source, "updated_at": datetime.now(timezone.utc).isoformat()}
        self.set_user_profile(profile)

    def search_memory(self, query: str, limit: int = 5) -> list[dict]:
        if not query or not query.strip():
            return []
        query_text = query.strip().lower()
        facts = self.get_facts()
        hits: list[dict] = []
        for key, value in facts.items():
            key_text = str(key).lower()
            value_text = str(value).lower()
            if query_text in key_text or query_text in value_text:
                hits.append({"type": "fact", "key": key, "content": f"{key}: {value}", "score": 1})
        if len(hits) >= limit:
            return hits[:limit]
        recent_messages = self.get_recent_messages(limit=max(limit, 10))
        for msg in recent_messages:
            content = str(msg.get("content") or "")
            if query_text in content.lower():
                hits.append({"type": "message", "role": msg.get("role"), "content": content, "score": 0.5})
        return hits[:limit]

    def index_document(self, doc_id: str, text: str, metadata: dict | None = None) -> bool:
        """Index a document locally without a heavyweight vector database."""
        if not doc_id or not text.strip():
            return False
        chunks = [text[i:i + 800].strip() for i in range(0, len(text), 650)]
        chunks = [chunk for chunk in chunks if chunk]
        metadata_base = dict(metadata or {})
        metadata_base["doc_id"] = doc_id
        try:
            with self._lock:
                if self._fts_available:
                    self.conn.execute("DELETE FROM documents_fts WHERE doc_key LIKE ?", (f"{doc_id}:%",))
                    for i, chunk in enumerate(chunks):
                        meta = {**metadata_base, "chunk": i}
                        self.conn.execute(
                            "INSERT INTO documents_fts (doc_key, text, metadata) VALUES (?,?,?)",
                            (f"{doc_id}:{i}", chunk, json.dumps(meta, ensure_ascii=False)),
                        )
                else:
                    self.conn.execute(
                        "CREATE TABLE IF NOT EXISTS documents_fallback ("
                        "doc_key TEXT PRIMARY KEY, text TEXT NOT NULL, metadata TEXT NOT NULL)"
                    )
                    self.conn.execute("DELETE FROM documents_fallback WHERE doc_key LIKE ?", (f"{doc_id}:%",))
                    for i, chunk in enumerate(chunks):
                        meta = {**metadata_base, "chunk": i}
                        self.conn.execute(
                            "INSERT INTO documents_fallback (doc_key, text, metadata) VALUES (?,?,?)",
                            (f"{doc_id}:{i}", chunk, json.dumps(meta, ensure_ascii=False)),
                        )
                self.conn.commit()
            return bool(chunks)
        except Exception:
            logger.exception("Erro ao indexar documento '%s'", doc_id)
            return False

    def search_documents(self, query: str, n_results: int = 5) -> list[dict]:
        search = _fts_query(query)
        if not search:
            return []
        limit = max(1, min(int(n_results), 20))
        try:
            with self._lock:
                if self._fts_available:
                    rows = self.conn.execute(
                        "SELECT text, metadata FROM documents_fts WHERE documents_fts MATCH ? ORDER BY bm25(documents_fts) LIMIT ?",
                        (search, limit),
                    ).fetchall()
                else:
                    like = f"%{search.split(' OR ')[0].strip(chr(34))}%"
                    rows = self.conn.execute(
                        "SELECT text, metadata FROM documents_fallback WHERE text LIKE ? LIMIT ?",
                        (like, limit),
                    ).fetchall()
            return [
                {"text": text, "metadata": json.loads(metadata) if metadata else {}}
                for text, metadata in rows
            ]
        except Exception:
            logger.exception("Erro na pesquisa RAG")
            return []

    def close(self):
        try:
            with self._lock:
                self.conn.close()
        except Exception:
            pass


_shared: MemoryEngine | None = None


def get_memory() -> MemoryEngine:
    global _shared
    if _shared is None:
        _shared = MemoryEngine()
    return _shared
