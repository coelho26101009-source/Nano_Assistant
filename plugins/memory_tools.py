"""
H.E.L.I.O.S. Plugin: Memory Tools
Permite ao cérebro gravar, consultar e apagar factos persistentes sobre o Simão
e pesquisar em conversas antigas guardadas no SQLite.
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.memory import get_memory

logger = logging.getLogger("helios.plugins.memory_tools")


def remember_fact(key: str, value: str) -> dict:
    """Grava um facto duradouro sobre o Simão (nome, hábitos, preferências)."""
    key = (key or "").strip()
    if not key:
        return {"error": "Preciso de uma chave para o facto (ex: 'nome', 'cidade')."}
    get_memory().set_fact(key, value)
    logger.info(f"Facto gravado: {key} = {str(value)[:80]}")
    return {"success": True, "fact": {key: value},
            "message": f"Guardei que {key} = {value}."}


def list_facts() -> dict:
    """Lista todos os factos persistentes conhecidos sobre o Simão."""
    facts = get_memory().get_facts()
    return {"facts": facts, "count": len(facts)}


def forget_fact(key: str) -> dict:
    """Apaga um facto persistente."""
    removed = get_memory().forget_fact(key or "")
    return {"success": removed,
            "message": f"Facto '{key}' apagado." if removed else f"Não tinha nada guardado em '{key}'."}


def search_history(query: str, limit: int = 10) -> dict:
    """Pesquisa por texto nas conversas anteriores."""
    query = (query or "").strip()
    if not query:
        return {"error": "Preciso de um termo de pesquisa."}
    memory = get_memory()
    try:
        rows = memory.conn.execute(
            "SELECT role, content, timestamp FROM messages "
            "WHERE content LIKE ? ORDER BY id DESC LIMIT ?",
            (f"%{query}%", int(limit)),
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
                "Guarda permanentemente um facto sobre o Simão (nome, cidade, gostos, "
                "horários, ferramentas que usa). Usa quando ele revela algo duradouro."
            ),
            "parameters": {"type": "object", "required": ["key", "value"], "properties": {
                "key":   {"type": "string", "description": "Identificador curto, ex: 'cidade'"},
                "value": {"type": "string", "description": "Valor do facto, ex: 'Porto'"},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_facts",
            "description": "Lista tudo o que o H.E.L.I.O.S. sabe de forma persistente sobre o Simão.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "forget_fact",
            "description": "Apaga um facto persistente guardado sobre o Simão.",
            "parameters": {"type": "object", "required": ["key"], "properties": {
                "key": {"type": "string"},
            }},
        }},
        {"type": "function", "function": {
            "name": "memory_search_history",
            "description": (
                "Pesquisa por palavras em conversas antigas. Usa quando o Simão pergunta "
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
