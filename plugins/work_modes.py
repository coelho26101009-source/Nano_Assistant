"""
Nano Plugin: Work modes (read-only for the model).

The model can LIST the work modes and READ a mode's plan. It cannot run a mode
in one call: to apply one it calls the narrow PC Control tools in the plan
(``pc_window_batch_close``, ``pc_app_launch``, ``pc_web_open_url``,
``pc_volume_set``, ``pc_settings_open``), each of which carries its own policy,
confirmation and target binding. The one-click "Ativar" lives in the UI
(PC -> Modos), and it too runs every step through ToolExecutor.

See core/work_modes.py for the format and validation, and for why the previous
implementation (context_switcher) was withdrawn.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core import work_modes


def work_mode_list() -> dict:
    modes = work_modes.list_modes()
    return {
        "ok": True,
        "modes": [{"id": m["id"], "name": m["name"], "description": m.get("description", ""),
                   "usable": not m.get("error")} for m in modes],
        "count": len(modes),
    }


def work_mode_plan(mode: str) -> dict:
    try:
        plan = work_modes.load(mode)
    except work_modes.ModeError as exc:
        return {"ok": False, "status": "not_found", "error": "not_found", "message": str(exc)}
    return {
        "ok": True,
        "id": plan["id"],
        "name": plan["name"],
        "message": plan.get("message", ""),
        "instructions": ("Para aplicar o modo, chama cada ferramenta da lista 'steps' com os "
                         "argumentos indicados, pela ordem. Fechar aplicações pede confirmação. "
                         "Diz ao utilizador o que foi feito e o que não foi."),
        "steps": [{"tool": s["tool"], "arguments": s["args"], "label": s["label"]} for s in plan["steps"]],
    }


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "work_mode_list",
            "description": "Lista os modos de trabalho do Nano (ex. Foco, Programar, Relax). Só leitura.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "work_mode_plan",
            "description": ("Mostra os passos de um modo de trabalho (que apps fechar e abrir, sites, "
                            "volume). Não executa nada: para o aplicar chama as ferramentas indicadas."),
            "parameters": {"type": "object", "required": ["mode"], "properties": {
                "mode": {"type": "string", "description": "id do modo, de work_mode_list"},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "work_mode_list": lambda a: work_mode_list(),
    "work_mode_plan": lambda a: work_mode_plan(**a),
}
