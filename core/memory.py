"""
H.E.L.I.O.S. Memory Engine
SQLite para histórico de conversas.
ChromaDB para RAG sobre PDFs e documentos locais.
"""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("helios.memory")

DB_PATH     = Path(__file__).parent.parent / "data" / "helios.db"
FACT_PREFIX = "fact:"


def _truncate(text: str, max_chars: int) -> str:
    text = text or ""
    return text if len(text) <= max_chars else text[:max_chars] + " […]"


class MemoryEngine:
    def __init__(self):
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._init_db()
        self._chroma = None  # lazy-loaded

    def _init_db(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata  TEXT DEFAULT '{}'
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS preferences (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        self.conn.commit()

    # ─── Mensagens ─────────────────────────────────────────────────────────

    def save_message(self, role: str, content: str, metadata: dict | None = None):
        try:
            self.conn.execute(
                "INSERT INTO messages (role, content, timestamp, metadata) VALUES (?,?,?,?)",
                (role, content, datetime.now().isoformat(), json.dumps(metadata or {}))
            )
            self.conn.commit()
        except Exception as exc:
            logger.error(f"Erro ao guardar mensagem: {exc}")

    def get_recent_messages(self, limit: int = 50) -> list[dict]:
        try:
            rows = self.conn.execute(
                "SELECT role, content, timestamp FROM messages ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [{"role": r[0], "content": r[1], "timestamp": r[2]} for r in reversed(rows)]
        except Exception as exc:
            logger.error(f"Erro ao ler mensagens: {exc}")
            return []

    def get_context_window(self, limit: int = 20, max_chars: int = 8000) -> list[dict]:
        """
        Devolve mensagens formatadas para o LLM (role + content apenas),
        truncadas para caber na janela de contexto:
          - apenas roles 'user'/'assistant' (tool calls antigas não são reconstituíveis)
          - mensagens individuais gigantes são cortadas
          - mensagens mais antigas são descartadas até caber em max_chars
        """
        msgs = [
            {"role": m["role"], "content": _truncate(m["content"], max_chars // 4)}
            for m in self.get_recent_messages(limit)
            if m["role"] in ("user", "assistant") and (m["content"] or "").strip()
        ]

        window: list[dict] = []
        total = 0
        for msg in reversed(msgs):          # do mais recente para o mais antigo
            size = len(msg["content"])
            if total + size > max_chars:
                break
            window.append(msg)
            total += size
        window.reverse()

        # A janela deve começar num 'user' para o histórico fazer sentido
        while window and window[0]["role"] != "user":
            window.pop(0)
        return window

    def count_messages(self) -> int:
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM messages").fetchone()
            return int(row[0]) if row else 0
        except Exception:
            return 0

    # ─── Preferências ──────────────────────────────────────────────────────

    def set_preference(self, key: str, value: Any):
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO preferences (key, value) VALUES (?,?)",
                (key, json.dumps(value))
            )
            self.conn.commit()
        except Exception as exc:
            logger.error(f"Erro ao guardar preferência: {exc}")

    def get_preference(self, key: str, default: Any = None) -> Any:
        try:
            row = self.conn.execute(
                "SELECT value FROM preferences WHERE key=?", (key,)
            ).fetchone()
            return json.loads(row[0]) if row else default
        except Exception:
            return default

    def delete_preference(self, key: str) -> bool:
        try:
            cur = self.conn.execute("DELETE FROM preferences WHERE key=?", (key,))
            self.conn.commit()
            return cur.rowcount > 0
        except Exception as exc:
            logger.error(f"Erro ao apagar preferência: {exc}")
            return False

    # ─── Factos sobre o utilizador ─────────────────────────────────────────
    # Guardados nas preferências com prefixo 'fact:' para poderem ser injectados
    # no system prompt em cada conversa.

    def set_fact(self, key: str, value: Any):
        self.set_preference(f"{FACT_PREFIX}{key.strip().lower()}", value)

    def get_fact(self, key: str, default: Any = None) -> Any:
        return self.get_preference(f"{FACT_PREFIX}{key.strip().lower()}", default)

    def forget_fact(self, key: str) -> bool:
        return self.delete_preference(f"{FACT_PREFIX}{key.strip().lower()}")

    def get_facts(self) -> dict[str, Any]:
        try:
            rows = self.conn.execute(
                "SELECT key, value FROM preferences WHERE key LIKE ? ORDER BY key",
                (f"{FACT_PREFIX}%",)
            ).fetchall()
        except Exception as exc:
            logger.error(f"Erro ao ler factos: {exc}")
            return {}

        facts: dict[str, Any] = {}
        for key, raw in rows:
            try:
                facts[key[len(FACT_PREFIX):]] = json.loads(raw)
            except json.JSONDecodeError:
                facts[key[len(FACT_PREFIX):]] = raw
        return facts

    # ─── RAG (ChromaDB) ────────────────────────────────────────────────────

    def _get_chroma(self):
        if self._chroma is None:
            try:
                import chromadb
                chroma_path = Path(__file__).parent.parent / "data" / "chroma"
                chroma_path.mkdir(parents=True, exist_ok=True)
                self._chroma = chromadb.PersistentClient(path=str(chroma_path))
            except ImportError:
                logger.warning("chromadb não instalado. RAG desactivado.")
        return self._chroma

    def index_document(self, doc_id: str, text: str, metadata: dict | None = None) -> bool:
        """Indexa um documento para pesquisa semântica."""
        client = self._get_chroma()
        if client is None:
            return False
        try:
            col = client.get_or_create_collection("helios_docs")
            # Divide em chunks de ~500 chars
            chunks = [text[i:i+500] for i in range(0, len(text), 400)]
            col.upsert(
                documents=chunks,
                ids=[f"{doc_id}_chunk_{i}" for i in range(len(chunks))],
                metadatas=[{**(metadata or {}), "doc_id": doc_id, "chunk": i} for i in range(len(chunks))],
            )
            logger.info(f"Documento '{doc_id}' indexado ({len(chunks)} chunks)")
            return True
        except Exception as exc:
            logger.error(f"Erro ao indexar documento: {exc}")
            return False

    def search_documents(self, query: str, n_results: int = 5) -> list[dict]:
        """Pesquisa semântica nos documentos indexados."""
        client = self._get_chroma()
        if client is None:
            return []
        try:
            col = client.get_or_create_collection("helios_docs")
            results = col.query(query_texts=[query], n_results=n_results)
            docs = results.get("documents", [[]])[0]
            metas = results.get("metadatas", [[]])[0]
            return [{"text": d, "metadata": m} for d, m in zip(docs, metas)]
        except Exception as exc:
            logger.error(f"Erro na pesquisa RAG: {exc}")
            return []


# ─── Instância partilhada ─────────────────────────────────────────────────────

_shared: MemoryEngine | None = None


def get_memory() -> MemoryEngine:
    """MemoryEngine partilhado entre core e plugins (evita ligações SQLite duplicadas)."""
    global _shared
    if _shared is None:
        _shared = MemoryEngine()
    return _shared
