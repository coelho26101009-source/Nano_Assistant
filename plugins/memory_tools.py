"""
Nano Plugin: Memory Tools

Lets the model record, query and delete durable facts about the user, and search
earlier conversations.

WHERE THESE WRITE
-----------------
``remember_fact`` writes to BOTH stores, on purpose:

* the legacy ``preferences`` key/value table, because plugins, older builds and
  ``memory.get_facts()`` still read it, and silently dropping it would make
  facts saved by a previous version disappear;
* the long-term memory store, which is what the Memória page shows, what
  retrieval searches, and what the ContextComposer selects from.

The long-term write goes through the same safety gate as every other one
(``core.memory_safety``). A tool call is the MODEL asking, and the model may be
repeating something it just read on a web page, so provenance and content decide
— not the fact that a tool made the call. A refusal is reported back to the
model as an ordinary tool result rather than swallowed, because a tool that
claims success for half an operation teaches the model to claim things that did
not happen.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import get_memory

logger = logging.getLogger("helios.plugins.memory_tools")


def _stack():
    """The live memory stack, or None when Nano is running without one.

    Imported lazily and defensively: this plugin is also loaded by tests and by
    tooling that never builds a stack, and a missing one must degrade to the
    legacy behaviour rather than raise.
    """
    try:
        import core.main as nano_main

        stack = getattr(nano_main, "memory_stack", None)
        return stack if stack is not None and getattr(stack, "ready", False) else None
    except Exception:
        return None


def _matching_legacy(stack, key: str) -> list[dict]:
    wanted = str(key or "").strip().lower()
    if not wanted:
        return []
    return [row for row in stack.memories.list(limit=300, status=None)
            if str(row.get("legacyKey") or "").strip().lower() == wanted]


def remember_fact(key: str, value: str) -> dict:
    """Record a durable fact about the user (name, habits, preferences)."""
    key = (key or "").strip()
    if not key:
        return {"error": "Preciso de uma chave para o facto (ex: 'nome', 'cidade')."}
    get_memory().set_fact(key, value)

    stack = _stack()
    if stack is None:
        logger.info("Fact recorded (legacy store only): %s", key)
        return {"success": True, "fact": {key: value}, "long_term_stored": False,
                "message": f"Guardei que {key} = {value}."}

    result = stack.remember(f"{key}: {value}", kind="fact", origin="explicit",
                            importance=4, legacy_key=key)
    if not result.get("ok"):
        reason = result.get("detail") or result.get("error")
        logger.warning("Long-term memory refused the fact %r: %s", key, result.get("error"))
        return {"success": True, "fact": {key: value}, "long_term_stored": False,
                "long_term_reason": reason,
                "message": f"Guardei {key} nesta sessão, mas não na memória de "
                           f"longo prazo: {reason}."}
    logger.info("Fact recorded: %s", key)
    return {"success": True, "fact": {key: value}, "long_term_stored": True,
            "message": f"Guardei que {key} = {value}."}


def list_facts() -> dict:
    """List everything Nano persistently knows about the user."""
    facts = get_memory().get_facts()
    stack = _stack()
    memories = [row["text"] for row in stack.memories.list(limit=50)] if stack else []
    return {"facts": facts, "count": len(facts), "memories": memories,
            "memory_count": len(memories)}


def forget_fact(key: str) -> dict:
    """Delete a persistent fact from both stores."""
    removed = get_memory().forget_fact(key or "")
    stack = _stack()
    if stack is not None:
        for row in _matching_legacy(stack, key):
            stack.forget(row["id"])
            removed = True
    return {"success": removed,
            "message": (f"Facto '{key}' apagado." if removed
                        else f"Não tinha nada guardado em '{key}'.")}


def search_history(query: str, limit: int = 10) -> dict:
    """Search previous conversations by text.

    Uses the retrieval index when it is available — ranked and
    accent-insensitive — and otherwise falls back to the bounded LIKE query this
    tool has always used. Both paths are parameterised statements: the search
    term is model-controlled text and never reaches SQL as interpolation.
    """
    query = (query or "").strip()
    if not query:
        return {"error": "Preciso de um termo de pesquisa."}
    memory = get_memory()
    stack = _stack()
    limit = max(1, min(int(limit or 10), 25))

    if stack is not None:
        hits = stack.conversations.search_messages(query, limit=limit)
        if hits:
            return {
                "query": query,
                "results": [{"role": hit.metadata.get("role", "user"),
                             "content": hit.body[:400],
                             "conversation_id": hit.scope,
                             "score": round(hit.score, 3),
                             "timestamp": hit.created_at} for hit in hits],
                "count": len(hits),
                "total_messages": stack.conversations.total_messages(),
            }

    try:
        rows = memory.conn.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
    except Exception as exc:
        return {"error": f"Falha a pesquisar histórico: {exc}"}

    results = [
        {"role": r[0], "content": (r[1] or "")[:400], "timestamp": r[2]}
        for r in rows
    ]
    return {"query": query, "results": results, "count": len(results),
            "total_messages": memory.count_messages()}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "remember_fact",
            "description": (
                "Guarda permanentemente um facto sobre o utilizador (nome, cidade, gostos, "
                "horários, ferramentas que usa). Usa quando ele revela algo duradouro."
            ),
            "parameters": {"type": "object", "required": ["key", "value"], "properties": {
                "key":   {"type": "string", "description": "Identificador curto, ex: 'cidade'"},
                "value": {"type": "string", "description": "Valor do facto, ex: 'Porto'"},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_facts",
            "description": "Lista tudo o que o Nano sabe de forma persistente sobre o utilizador.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "forget_fact",
            "description": "Apaga um facto persistente guardado sobre o utilizador.",
            "parameters": {"type": "object", "required": ["key"], "properties": {
                "key": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "memory_search_history",
            "description": (
                "Pesquisa por palavras em conversas antigas. Usa quando o utilizador pergunta "
                "'o que é que eu disse sobre X?' ou 'lembras-te de...'."
            ),
            "parameters": {"type": "object", "required": ["query"], "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "remember_fact":         lambda a: remember_fact(**a),
    "list_facts":            lambda _: list_facts(),
    "forget_fact":           lambda a: forget_fact(**a),
    "memory_search_history": lambda a: search_history(**a),
}
