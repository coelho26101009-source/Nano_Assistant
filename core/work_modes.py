"""Work modes, rebuilt on the narrow PC Control tools.

A work mode ("Foco", "Programar", "Relax") prepares the PC for an activity:
close distracting apps, open the ones you need, open a few sites, set the
volume, and open a Windows Settings page the user may want to change (Focus
Assist / notifications).

The first implementation (the old context_switcher plugin, since deleted)
shelled out to PowerShell, force-killed processes, edited the registry and
resolved the mode file from a model-supplied path. This one does the opposite:

* A mode is DATA, loaded only from ``config/modes/<id>.yaml`` where ``<id>`` is
  one of the files actually in that folder (closed allow-list, no path from
  the model or the UI ever reaches the filesystem).
* A mode compiles into a PLAN: a list of calls to existing narrow tools --
  ``pc_window_batch_close``, ``pc_app_launch``, ``pc_web_open_url``,
  ``pc_volume_set`` and ``pc_settings_open``. Every value is validated with the
  same rules those tools apply (app names only, no paths or executables;
  http/https only; volume 0-100; allow-listed Settings sections).
* Running a plan sends EACH step through ``ToolExecutor`` -- policy,
  permission, confirmation, target binding, verification -- exactly as if the
  user had asked for it. Closing apps still asks every time, and still never
  force-kills. Nothing here touches Windows directly.
"""
from __future__ import annotations

from core.i18n import tr

import logging
import re
from pathlib import Path
from typing import Any

import yaml

from core.app_paths import ROOT
from core.pc_control.results import PCControlError
from core.pc_control import settings as pc_settings
from core.pc_control import web as pc_web

logger = logging.getLogger("nano.work_modes")

MODES_DIR = ROOT / "config" / "modes"
MODE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
MAX_ITEMS = 8
MAX_TEXT = 160
#: A bare application/process name. No separators, no drive letters, no
#: extensions that make it a file: the launch tool takes names, never paths.
APP_NAME = re.compile(r"^[\w .+&'()-]{1,60}$", re.UNICODE)
#: Interpreters the app launcher refuses by name; refused here too, so a mode
#: file cannot even propose them.
REFUSED_APPS = frozenset({
    "cmd", "powershell", "pwsh", "wt", "windows terminal", "bash", "wsl",
    "python", "pythonw", "node", "wscript", "cscript", "mshta", "regedit",
})


class ModeError(ValueError):
    """A mode file or mode id that cannot be used. The message is user-facing."""


def available_ids(modes_dir: Path | None = None) -> list[str]:
    """The closed allow-list: every well-named .yaml directly in the folder."""
    folder = modes_dir or MODES_DIR
    if not folder.is_dir():
        return []
    return sorted(p.stem for p in folder.glob("*.yaml") if p.is_file() and MODE_ID.match(p.stem))


def _clip(value: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(value or "").split())[:limit]


def _app_name(value: Any) -> str:
    name = _clip(value, 60)
    folded = name.casefold()
    if folded.endswith(".exe"):
        name, folded = name[:-4], folded[:-4]
    if not name or not APP_NAME.match(name) or folded in REFUSED_APPS:
        raise ModeError(tr("'{name}' não é um nome de aplicação aceite num modo.", name=_clip(value, 60)))
    return name


def _list(raw: dict, key: str) -> list:
    value = raw.get(key) or []
    if not isinstance(value, list):
        raise ModeError(tr("'{key}' tem de ser uma lista.", key=key))
    if len(value) > MAX_ITEMS:
        raise ModeError(tr("'{key}' tem mais de {limit} entradas.", key=key, limit=MAX_ITEMS))
    return value


def compile_mode(mode_id: str, raw: Any) -> dict:
    """Validate a parsed mode file and turn it into a plan of narrow tool calls."""
    if not isinstance(raw, dict):
        raise ModeError(tr("O ficheiro do modo não tem o formato esperado."))
    steps: list[dict] = []
    for app in _list(raw, "close_apps"):
        name = _app_name(app)
        steps.append({"tool": "pc_window_batch_close", "args": {"app": name},
                      "label": tr("Fechar {name}", name=name), "asks": True})
    for app in _list(raw, "open_apps"):
        name = _app_name(app)
        steps.append({"tool": "pc_app_launch", "args": {"name": name}, "label": tr("Abrir {name}", name=name)})
    for url in _list(raw, "open_urls"):
        try:
            safe = pc_web.normalise_url(url)
        except PCControlError as exc:
            raise ModeError(tr("Endereço recusado: {reason}", reason=exc.message)) from exc
        steps.append({"tool": "pc_web_open_url", "args": {"url": safe}, "label": tr("Abrir {name}", name=safe)})
    if raw.get("volume") is not None:
        volume = raw.get("volume")
        if isinstance(volume, bool) or not isinstance(volume, int) or not 0 <= volume <= 100:
            raise ModeError(tr("'volume' tem de ser um número inteiro entre 0 e 100."))
        steps.append({"tool": "pc_volume_set", "args": {"level": volume}, "label": tr("Volume a {level}%", level=volume)})
    section = raw.get("settings_section")
    if section:
        try:
            key, _uri, label = pc_settings.resolve_section(section)
        except PCControlError as exc:
            raise ModeError(exc.message) from exc
        steps.append({"tool": "pc_settings_open", "args": {"section": key},
                      "label": tr("Abrir as definições de {section}", section=label)})
    if not steps:
        raise ModeError(tr("Este modo não faz nada."))
    return {
        "id": mode_id,
        "name": _clip(raw.get("name") or mode_id, 60),
        "description": _clip(raw.get("description")),
        "message": _clip(raw.get("message")),
        "steps": steps,
    }


def load(mode_id: Any, modes_dir: Path | None = None) -> dict:
    """Load one mode by id. The id must be on the allow-list; it is never a path."""
    wanted = str(mode_id or "").strip().casefold()
    allowed = available_ids(modes_dir)
    if wanted not in allowed:
        raise ModeError(tr("Não conheço o modo '{mode}'. Modos: {modes}.", mode=_clip(mode_id, 40), modes=", ".join(allowed) or tr("nenhum")))
    folder = modes_dir or MODES_DIR
    path = (folder / f"{wanted}.yaml").resolve()
    if path.parent != folder.resolve():  # belt and braces; the allow-list already holds
        raise ModeError(tr("Modo inválido."))
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ModeError(tr("Não consegui ler o modo '{mode}'.", mode=wanted)) from exc
    return compile_mode(wanted, raw)


def list_modes(modes_dir: Path | None = None) -> list[dict]:
    """Every usable mode, with its plan. A broken file is reported, not hidden."""
    modes = []
    for mode_id in available_ids(modes_dir):
        try:
            modes.append(load(mode_id, modes_dir))
        except ModeError as exc:
            modes.append({"id": mode_id, "name": mode_id, "error": str(exc), "steps": []})
    return modes


def _step_outcome(step: dict, result: Any) -> dict:
    output = result.get("output") if isinstance(result, dict) else None
    message = ""
    if isinstance(output, dict):
        message = _clip(output.get("message"), 200)
    if not message and isinstance(result, dict):
        message = _clip(result.get("message") or result.get("error"), 200)
    ok = bool(isinstance(result, dict) and result.get("success"))
    denied = isinstance(result, dict) and result.get("status") in {"permission_denied", "denied", "user_denied"}
    return {"label": step["label"], "tool": step["tool"], "ok": ok,
            "status": "ok" if ok else ("denied" if denied else "failed"), "message": message}


async def run(mode_id: Any, executor: Any, modes_dir: Path | None = None) -> dict:
    """Run a mode's plan, one narrow tool at a time, through the executor.

    Each step is independent: a refused close ("guardar alterações?") or a
    declined confirmation does not stop the volume being set. The summary says
    exactly what happened to each step; nothing is reported as done unless the
    executor verified it.
    """
    try:
        mode = load(mode_id, modes_dir)
    except ModeError as exc:
        return {"ok": False, "error": "unknown_mode", "message": str(exc), "steps": []}
    return await run_plan(mode, executor)


async def run_plan(mode: dict, executor: Any) -> dict:
    """Run an already compiled plan (a mode, or a protocol: core/protocols.py)."""
    running = await _running_processes(executor) if any(
        step["tool"] == "pc_window_batch_close" for step in mode["steps"]) else None
    outcomes = []
    for step in mode["steps"]:
        if step["tool"] == "pc_window_batch_close" and running is not None \
                and _process_key(step["args"]["app"]) not in running:
            # Not open: nothing to close, and no confirmation to sit through.
            outcomes.append({"label": step["label"], "tool": step["tool"], "ok": True,
                             "status": "skipped", "message": tr("Não estava aberto.")})
            continue
        try:
            result = await executor.execute_tool_async(step["tool"], dict(step["args"]))
        except Exception:
            logger.exception("Modo %s: passo %s falhou", mode["id"], step["tool"])
            result = {"success": False, "error": "tool_exception"}
        outcomes.append(_step_outcome(step, result))
    done = sum(1 for o in outcomes if o["ok"] and o["status"] != "skipped")
    skipped = sum(1 for o in outcomes if o["status"] == "skipped")
    counted = len(outcomes) - skipped
    message = tr("{mode}: {done} de {total} passos feitos.", mode=mode["name"], done=done, total=counted)
    if done and mode.get("message"):
        message = tr("{message} ({done} de {total} passos feitos.)",
                     message=mode["message"], done=done, total=counted)
    return {"ok": done > 0 or skipped == len(outcomes), "id": mode["id"], "name": mode["name"],
            "message": message, "done": done, "skipped": skipped, "total": len(outcomes),
            "steps": outcomes}


def _process_key(name: str) -> str:
    key = str(name or "").casefold()
    return key[:-4] if key.endswith(".exe") else key


async def _running_processes(executor: Any) -> set[str] | None:
    """Processes that have a window, via the read-only listing tool.

    None means "could not tell" (not on Windows, listing refused): then every
    close step is attempted and the close tool itself reports not_found.
    """
    try:
        result = await executor.execute_tool_async("pc_window_list", {})
    except Exception:
        return None
    output = result.get("output") if isinstance(result, dict) else None
    if not (isinstance(result, dict) and result.get("success") and isinstance(output, dict)):
        return None
    windows = output.get("windows")
    if not isinstance(windows, list):
        return None
    return {_process_key(w.get("process")) for w in windows if isinstance(w, dict) and w.get("process")}
