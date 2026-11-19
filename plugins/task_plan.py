"""
Nano Plugin: show a plan before a task of several steps.

"Prepara a apresentação de amanhã": look for the file, open it, open the
notes, set the volume... For a request that takes three or more actions the
model first calls ``task_plan`` with the steps it intends to take. NANO shows
that plan to the person ONCE ("Vou fazer isto: 1. ... 2. ... Avanço?"); on yes
the model carries the steps out with the ordinary tools.

This plugin executes nothing. Every step is still its own tool call through
ToolExecutor, with its own policy and permission: approving the plan only
spares the person the guardrail question for the steps it listed
(core/brain.py, ``_turn_plan_tools``); anything the policy always asks about
(closing apps, power, deleting, sending) still asks.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.i18n import tr

MAX_STEPS = 6
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def validate(arguments: dict) -> dict:
    """The plan, cleaned and bounded, or an error the model can act on."""
    args = arguments or {}
    goal = " ".join(str(args.get("goal") or "").split())[:160]
    raw = args.get("steps")
    if not goal or not isinstance(raw, list) or not raw:
        return {"ok": False, "error": "invalid_plan",
                "message": tr("Um plano precisa de um objetivo e de pelo menos um passo.")}
    if len(raw) > MAX_STEPS:
        return {"ok": False, "error": "plan_too_long",
                "message": tr("No máximo {count} passos por plano. Divide a tarefa.", count=MAX_STEPS)}
    steps = []
    for item in raw:
        if not isinstance(item, dict):
            return {"ok": False, "error": "invalid_plan", "message": tr("Cada passo precisa de 'label' e 'tool'.")}
        label = " ".join(str(item.get("label") or "").split())[:120]
        tool = str(item.get("tool") or "").strip()
        if not label or not _TOOL_NAME.match(tool) or tool == "task_plan":
            return {"ok": False, "error": "invalid_plan",
                    "message": tr("Cada passo precisa de 'label' e de uma ferramenta válida.")}
        steps.append({"label": label, "tool": tool})
    return {"ok": True, "goal": goal, "steps": steps}


def describe(plan: dict) -> str:
    """The question shown to the person."""
    lines = [tr("Vou fazer isto para “{goal}”:", goal=plan["goal"])]
    lines += [f"{index}. {step['label']}" for index, step in enumerate(plan["steps"], start=1)]
    lines.append(tr("Avanço? O que mexer em coisas importantes ainda te pergunto à parte."))
    return "\n".join(lines)


def task_plan(arguments: dict) -> dict:
    plan = validate(arguments)
    if not plan["ok"]:
        return plan
    return {**plan, "approved": True,
            "instructions": ("O utilizador aprovou este plano. Executa os passos pela ordem, um a um, "
                             "com as ferramentas indicadas. Se um passo falhar, para e diz o que ficou "
                             "feito e o que não ficou.")}


def get_tools() -> list[dict]:
    return [{"type": "function", "function": {
        "name": "task_plan",
        "description": ("Para pedidos que precisam de 3 ou mais ações (ex.: 'prepara a apresentação de "
                        "amanhã', 'arruma o PC para estudar e põe música'), chama isto PRIMEIRO com os "
                        "passos que vais fazer. O NANO mostra o plano ao utilizador uma vez; se ele "
                        "aceitar, executa cada passo com a ferramenta indicada. Não executa nada sozinho."),
        "parameters": {"type": "object", "required": ["goal", "steps"], "properties": {
            "goal": {"type": "string", "description": "O que o utilizador pediu, numa frase curta."},
            "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
                "type": "object", "required": ["label", "tool"], "properties": {
                    "label": {"type": "string", "description": "O passo, em linguagem simples."},
                    "tool": {"type": "string", "description": "A ferramenta que vais usar nesse passo."},
                }}},
        }},
    }}]


TOOL_HANDLERS: dict = {"task_plan": task_plan}
