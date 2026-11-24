"""
Nano Plugin: routines (protocols) the person makes and runs by talking.

"Guarda isto como rotina 'setup de estudo'", "cria uma rotina boa noite que
pára a música e bloqueia o PC", "que rotinas tenho?", "apaga a rotina jogo",
"prepara-me para estudar" (runs the routine whose name or phrase fits).

A routine is a protocol (core/protocols.py) with an ordered list of steps
(``actions``), each checked against a closed list of safe, repeatable PC
tools. Saving one runs nothing. ``routine_run`` does not run anything here
either: it returns the routine's steps, and the Brain runs them through
core/task_runner.py with each step going through its own gate (guardrails,
policy, permission, executor), exactly as if the person had asked for each.

``routine_save`` with ``from_last_actions: true`` saves what NANO just did in
the previous request ("guarda isto como rotina"): the Brain fills the steps
from that request's successful actions before this handler sees the call.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import protocols
from core.i18n import tr


def _find(name: str) -> dict | None:
    """The routine called ``name`` (or started by that phrase), or None."""
    wanted = protocols.normalise_phrase(name)
    if not wanted:
        return None
    items = protocols.STORE.list()
    for item in items:
        if protocols.normalise_phrase(item["name"]) == wanted or any(
                protocols.normalise_phrase(p) == wanted for p in item["phrases"]):
            return item
    # "estudo" finds "Setup de estudo" when it is the ONLY routine that fits.
    loose = [item for item in items
             if len(wanted) >= 4 and (wanted in protocols.normalise_phrase(item["name"])
                                      or any(wanted in protocols.normalise_phrase(p)
                                             for p in item["phrases"]))]
    return loose[0] if len(loose) == 1 else None


def _summary(protocol: dict) -> dict:
    plan = protocols.compile_protocol(protocol)
    return {"id": protocol["id"], "name": protocol["name"], "phrases": protocol["phrases"],
            "steps": [step["label"] for step in plan["steps"]],
            "message": protocol.get("message") or ""}


def routine_save(arguments: dict) -> dict:
    args = arguments or {}
    name = " ".join(str(args.get("name") or "").split())[:60]
    if not name:
        return {"ok": False, "status": "invalid_routine", "message": tr("Dá um nome à rotina.")}
    steps = args.get("steps")
    if not isinstance(steps, list) or not steps:
        return {"ok": False, "status": "invalid_routine",
                "message": tr("Uma rotina precisa de pelo menos um passo.")}
    phrases = args.get("phrases") if isinstance(args.get("phrases"), list) else []
    existing = _find(name)
    raw = {"name": name, "phrases": [name, *phrases] if name not in phrases else phrases,
           "actions": steps, "message": args.get("message") or ""}
    if existing is not None and protocols.normalise_phrase(existing["name"]) == protocols.normalise_phrase(name):
        raw["id"] = existing["id"]          # same name: this is an update
    try:
        saved = protocols.STORE.save(raw)
    except protocols.ProtocolError as exc:
        return {"ok": False, "status": "invalid_routine", "message": str(exc)}
    return {"ok": True, "status": "updated" if "id" in raw else "saved",
            "message": tr("Rotina “{name}” guardada. Diz “{phrase}” para a usar.",
                          name=saved["name"], phrase=saved["phrases"][0]),
            "routine": _summary(saved)}


def routine_list(_arguments: dict | None = None) -> dict:
    items = [_summary(item) for item in protocols.STORE.list()]
    return {"ok": True, "status": "listed", "routines": items, "count": len(items)}


def routine_run(arguments: dict) -> dict:
    """The routine's steps, for the Brain to run. Runs nothing itself."""
    name = str((arguments or {}).get("name") or "")
    protocol = _find(name)
    if protocol is None:
        names = ", ".join(item["name"] for item in protocols.STORE.list()) or tr("nenhuma")
        return {"ok": False, "status": "not_found",
                "message": tr("Não tenho nenhuma rotina “{name}”. Rotinas: {names}.",
                              name=name[:60], names=names)}
    plan = protocols.compile_protocol(protocol)
    return {"ok": True, "status": "ready", "id": protocol["id"], "name": protocol["name"],
            "message": plan["message"],
            "steps": [{"tool": s["tool"], "args": s["args"], "label": s["label"]} for s in plan["steps"]]}


def routine_delete(arguments: dict) -> dict:
    name = str((arguments or {}).get("name") or "")
    protocol = _find(name)
    if protocol is None:
        return {"ok": False, "status": "not_found",
                "message": tr("Não tenho nenhuma rotina “{name}”.", name=name[:60])}
    protocols.STORE.delete(protocol["id"])
    return {"ok": True, "status": "deleted",
            "message": tr("Apaguei a rotina “{name}”.", name=protocol["name"])}


_STEP_SCHEMA = {
    "type": "object", "required": ["tool"],
    "properties": {
        "tool": {"type": "string", "enum": sorted(protocols.ROUTINE_TOOLS),
                 "description": "A ferramenta deste passo."},
        "args": {"type": "object", "description": "Os argumentos dessa ferramenta, como numa chamada normal "
                                                  "(ex.: {\"name\": \"Spotify\"}, {\"level\": 30}, "
                                                  "{\"query\": \"Chrome\", \"position\": \"left\"})."},
        "label": {"type": "string", "description": "O passo em linguagem simples (opcional)."},
    },
}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "routine_save",
            "description": ("Guarda uma rotina do utilizador (um 'protocolo'), que ele depois ativa dizendo o "
                            "nome ou uma frase, ex. 'setup de estudo' ou 'boa noite'. Usa quando ele pede para "
                            "criar, guardar ou mudar uma rotina. 'guarda isto como rotina X' = "
                            "from_last_actions true (guarda o que acabaste de fazer). Para alterar uma rotina, "
                            "envia o mesmo nome e a lista COMPLETA de passos. Não executa nada."),
            "parameters": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string", "description": "Nome curto, ex. 'Setup de estudo'."},
                "phrases": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                            "description": "Outras frases que a ativam, ex. ['vou estudar']."},
                "steps": {"type": "array", "maxItems": protocols.MAX_ACTIONS, "items": _STEP_SCHEMA},
                "from_last_actions": {"type": "boolean",
                                      "description": "Guardar as ações do pedido anterior em vez de 'steps'."},
                "message": {"type": "string", "description": "O que o NANO diz no fim (opcional)."},
            }},
        }},
        {"type": "function", "function": {
            "name": "routine_list",
            "description": "Lista as rotinas (protocolos) do utilizador e os seus passos. Só leitura.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "routine_run",
            "description": ("Executa uma rotina do utilizador pelo nome, ex. 'prepara-me para estudar' -> "
                            "'Setup de estudo'. O NANO faz cada passo e verifica-o; os passos sensíveis "
                            "perguntam. Usa routine_list se não souberes o nome."),
            "parameters": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string", "description": "Nome ou frase da rotina."},
            }},
        }},
        {"type": "function", "function": {
            "name": "routine_delete",
            "description": "Apaga uma rotina do utilizador pelo nome. Pede confirmação.",
            "parameters": {"type": "object", "required": ["name"], "properties": {
                "name": {"type": "string"},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "routine_save": routine_save,
    "routine_list": routine_list,
    "routine_run": routine_run,
    "routine_delete": routine_delete,
}
