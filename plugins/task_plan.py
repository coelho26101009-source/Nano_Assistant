"""
Nano Plugin: the plan of a task of several steps.

"Prepara a apresentação de amanhã": look for the file, open it, open the
notes, set the volume... For a request that takes three or more actions the
model first calls ``task_plan`` with the steps it intends to take.

TWO KINDS OF PLAN
-----------------
* **Every step has its ``args``** (the usual case: "abre o Notion e o Spotify,
  põe o volume a 30 e encosta o Chrome à esquerda"). NANO runs the plan
  ITSELF, step by step, through core/task_runner.py: each step is checked,
  a launch waits for its window before the next step places it, hiccups are
  retried, and the result says exactly what was done. The model only has to
  get the plan right once, instead of keeping a chain of calls together.
* **Some step depends on an earlier result** (search a file, THEN open what
  was found): the steps go without ``args`` and the model carries them out
  one by one; at the end NANO says which planned steps never happened.

WHEN THE PERSON IS ASKED
------------------------
Only when it matters (core/brain_tasks.py, ``_plan_needs_question``): when a
step is one the guardrails always ask about (a calendar event, a page
action...), or when this request already read something from outside (a page,
the screen, a document), so a plan written by that content cannot run
unseen. A plan of safe steps (open apps and sites, volume, windows) just
runs. Steps the policy always asks about (closing apps, locking, deleting)
still ask on their own when they are reached.

This plugin executes nothing. Every step is still its own tool call through
ToolExecutor, with its own policy and permission.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.i18n import tr

MAX_STEPS = 8
MAX_ARG_KEYS = 8
_TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
#: A plan cannot contain a plan, or run/change a routine (no recursion).
NOT_A_STEP = frozenset({"task_plan", "routine_run", "routine_save", "routine_delete"})


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
        if not label or not _TOOL_NAME.match(tool) or tool in NOT_A_STEP:
            return {"ok": False, "error": "invalid_plan",
                    "message": tr("Cada passo precisa de 'label' e de uma ferramenta válida.")}
        step = {"label": label, "tool": tool}
        if "args" in item or "arguments" in item:
            args = item.get("args", item.get("arguments"))
            if args is None:
                args = {}
            if not isinstance(args, dict) or len(args) > MAX_ARG_KEYS:
                return {"ok": False, "error": "invalid_plan",
                        "message": tr("Os argumentos do passo “{label}” têm de ser um objeto simples.",
                                      label=label)}
            step["args"] = args
        steps.append(step)
    return {"ok": True, "goal": goal, "steps": steps}


def runnable(plan: dict) -> bool:
    """Whether NANO can run the whole plan itself: every step has its args."""
    return bool(plan.get("ok") and plan.get("steps")
                and all(isinstance(step.get("args"), dict) for step in plan["steps"]))


def describe(plan: dict) -> str:
    """The question shown to the person."""
    lines = [tr("Vou fazer isto para “{goal}”:", goal=plan["goal"])]
    lines += [f"{index}. {step['label']}" for index, step in enumerate(plan["steps"], start=1)]
    lines.append(tr("Avanço?"))
    return "\n".join(lines)


def task_plan(arguments: dict) -> dict:
    plan = validate(arguments)
    if not plan["ok"]:
        return plan
    return {**plan, "approved": True,
            "instructions": ("Plano aceite. Executa os passos pela ordem, um a um, com as ferramentas "
                             "indicadas. Se um passo falhar, diz o que ficou feito e o que não ficou.")}


def get_tools() -> list[dict]:
    return [{"type": "function", "function": {
        "name": "task_plan",
        "description": ("Para pedidos que precisam de 2 ou mais ações (ex.: 'abre o Notion e o Spotify, "
                        "põe o volume a 30 e encosta o Chrome à esquerda'), chama isto PRIMEIRO, sozinho, "
                        "com todos os passos. Dá a cada passo 'args' completos sempre que puderes: assim "
                        "o NANO executa e verifica o plano inteiro e devolve o resultado de cada passo. "
                        "Só deixa 'args' de fora se o passo depender do resultado de outro (ex.: abrir o "
                        "ficheiro que a pesquisa encontrar); aí executas tu os passos, um a um."),
        "parameters": {"type": "object", "required": ["goal", "steps"], "properties": {
            "goal": {"type": "string", "description": "O que o utilizador pediu, numa frase curta."},
            "steps": {"type": "array", "maxItems": MAX_STEPS, "items": {
                "type": "object", "required": ["label", "tool"], "properties": {
                    "label": {"type": "string", "description": "O passo, em linguagem simples."},
                    "tool": {"type": "string", "description": "A ferramenta que vais usar nesse passo."},
                    "args": {"type": "object",
                             "description": "Os argumentos dessa ferramenta, como numa chamada normal."},
                }}},
        }},
    }}]


TOOL_HANDLERS: dict = {"task_plan": task_plan}
