"""HELIOS memory: SQLite history plus optional ChromaDB RAG."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from core.app_paths import DATA_DIR

logger = logging.getLogger("helios.memory")
DB_PATH = DATA_DIR / "helios.db"
CHROMA_PATH = DATA_DIR / "chroma"
FACT_PREFIX = "fact:"


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[:max_chars] + " […]"


class MemoryEngine:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()
        self._chroma = None

    def _init_db(self):
        self.conn.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, role TEXT NOT NULL, content TEXT NOT NULL, timestamp TEXT NOT NULL, metadata TEXT DEFAULT '{}')")
        self.conn.execute("CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.conn.commit()

    def save_message(self, role: str, content: str, metadata: dict | None = None):
        if not content:
            return
        try:
            self.conn.execute(
                "INSERT INTO messages (role, content, timestamp, metadata) VALUES (?,?,?,?)",
                (role, content, datetime.now(timezone.utc).isoformat(), json.dumps(metadata or {}, ensure_ascii=False)),
            )
            self.conn.commit()
        except Exception:
            logger.exception("Erro ao guardar mensagem")

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        try:
            rows = self.conn.execute("SELECT role, content, timestamp FROM messages ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)).fetchall()
            return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]
        except Exception:
            logger.exception("Erro ao ler mensagens")
            return []

    def get_context_window(self, limit: int = 20, max_chars: int = 8000) -> list[dict]:
        msgs = [{"role": m["role"], "content": _truncate(m["content"], max_chars // 4)} for m in self.get_recent_messages(limit) if m["role"] in ("user", "assistant") and (m["content"] or "").strip()]
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
            row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    def set_preference(self, key: str, value: Any):
        try:
            self.conn.execute("INSERT OR REPLACE INTO preferences (key, value) VALUES (?,?)", (key, json.dumps(value, ensure_ascii=False)))
            self.conn.commit()
        except Exception:
            logger.exception("Erro ao guardar preferência")

    def get_preference(self, key: str, default: Any = None) -> Any:
        try:
            row = self.conn.execute("SELECT value FROM preferences WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default
        except Exception:
            logger.exception("Erro ao ler preferência '%s'", key)
            return default

    def delete_preference(self, key: str) -> bool:
        try:
            cur = self.conn.execute("DELETE FROM preferences WHERE key=?", (key,))
            self.conn.commit()
            return cur.rowcount > 0
        except Exception:
            logger.exception("Erro ao apagar preferência")
            return False

    def set_fact(self, key: str, value: Any):
        self.set_preference(f"{FACT_PREFIX}{key.strip().lower()}", value)

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self.get_preference(f"{FACT_PREFIX}{key.strip().lower()}", default)

    def forget_fact(self, key: str) -> bool:
        return self.delete_preference(f"{FACT_PREFIX}{key.strip().lower()}")

    def get_facts(self) -> dict[str, Any]:
        try:
            rows = self.conn.execute("SELECT key, value FROM preferences WHERE key LIKE ? ORDER BY key", (f"{FACT_PREFIX}%",)).fetchall()
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

    def _get_chroma(self):
        if self._chroma is None:
            try:
                import chromadb
                CHROMA_PATH.mkdir(parents=True, exist_ok=True)
                self._chroma = chromadb.PersistentClient(path=str(CHROMA_PATH))
            except ImportError:
                logger.warning("chromadb não instalado. RAG desactivado.")
        return self._chroma

    def index_document(self, doc_id: str, text: str, metadata: dict | None = None) -> bool:
        client = self._get_chroma()
        if client is None or not text.strip():
            return False
        try:
            col = client.get_or_create_collection("helios_docs")
            chunks = [text[i:i + 800] for i in range(0, len(text), 650)]
            col.upsert(documents=chunks, ids=[f"{doc_id}_chunk_{i}" for i in range(len(chunks))], metadatas=[{**(metadata or {}), "doc_id": doc_id, "chunk": i} for i in range(len(chunks))])
            return True
        except Exception:
            logger.exception("Erro ao indexar documento '%s'", doc_id)
            return False

    def search_documents(self, query: str, n_results: int = 5) -> list[dict]:
        client = self._get_chroma()
        if client is None:
            return []
        try:
            col = client.get_or_create_collection("helios_docs")
            results = col.query(query_texts=[query], n_results=n_results)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"text": d, "metadata": m or {}} for d, m in zip(docs, metas)]
        except Exception:
            logger.exception("Erro na pesquisa RAG")
            return []

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass


_shared: MemoryEngine | None = None


def get_memory() -> MemoryEngine:
    global _shared
    if _shared is None:
        _shared = MemoryEngine()
    return _shared
