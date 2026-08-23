"""Nano PC Control V1 — the tool surface the model is allowed to see.

This module is a THIN declaration layer. Every handler validates its typed
arguments and delegates straight into ``core.pc_control``; none of them build a
command line, and there is no `subprocess` import anywhere in the package. The
model chooses a tool and typed values, and those values reach a Win32 call as
values.

Why a flat module rather than a ``plugins/pc_control/`` package: the loader
imports ``plugins/*.py`` only. The Windows implementation does live in separate
modules -- ``core/pc_control/{applications,windows,audio,files,system,screen}.py``
-- and this file is the seam between them and the tool registry.

Tool names use underscores because they are sent to Groq as function names,
which do not permit dots. Each maps to a dotted CAPABILITY
(``pc_app_launch`` -> ``pc.app.launch``) through PolicyEngine's alias table,
so the policy taxonomy keeps the dotted form used everywhere else.

NOTHING HERE AUTHORIZES ANYTHING. A handler only ever runs after
ToolExecutor -> PolicyEngine -> PermissionManager have all said yes;
``plugin_loader.execute_tool`` refuses to dispatch for any other caller.
"""
from __future__ import annotations

import logging
from typing import Any

from core.pc_control import applications, audio, files, screen, system, windows
from core.pc_control.results import (
    MAX_APP_CANDIDATES,
    MAX_FILE_RESULTS,
    PCControlError,
    fail,
    from_error,
    ok,
)

logger = logging.getLogger("nano.plugins.pc_control")


def _guard(operation: str, function, *args, **kwargs) -> dict:
    """Run one PC operation and convert every outcome into the result contract.

    A PCControlError becomes its declared status. Anything else becomes a
    generic failure and the detail goes to the LOG, not to the model: stack
    traces are noise in a context window and can leak paths.
    """
    try:
        return function(*args, **kwargs)
    except PCControlError as exc:
        return from_error(exc)
    except Exception as exc:
        logger.exception("PC control operation failed: %s", operation)
        return fail("internal_error",
                    "A operação falhou no Windows. Vê os logs para o detalhe.",
                    operation=operation, error_type=type(exc).__name__)


# --------------------------------------------------------------------------
#  Applications
# --------------------------------------------------------------------------


def pc_app_search(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        query = str((arguments or {}).get("query") or "").strip()
        matches = applications.search(query, limit=MAX_APP_CANDIDATES)
        if not matches:
            return fail("not_found", f"Não encontrei nenhuma aplicação para '{query}'.",
                        query=query, candidates=[])
        candidates = [entry.as_dict(score) for entry, score in matches]
        return ok("found", f"Encontrei {len(candidates)} aplicação(ões) para '{query}'.",
                  query=query, candidates=candidates, count=len(candidates))

    return _guard("app.search", run)


def pc_app_launch(arguments: dict[str, Any]) -> dict:
    """Launch an installed application BY NAME. Never by path.

    ``app_id`` is looked up in the catalogue; it is a key, not a location. If
    neither the name nor the id resolves to a catalogue entry, nothing runs.
    """
    def run() -> dict:
        args = arguments or {}
        app_id = str(args.get("app_id") or "").strip()
        name = str(args.get("name") or "").strip()

        entry = applications.find_by_app_id(app_id) if app_id else None
        if entry is None and app_id and not name:
            return fail("not_found", "Essa aplicação já não está na lista de aplicações instaladas.",
                        app_id=app_id)
        if entry is None:
            if not name:
                return fail("invalid_input", "É preciso dizer que aplicação abrir.")
            entry, matches = applications.resolve(name)
            if entry is None:
                if not matches:
                    return fail("not_found", f"Não encontrei nenhuma aplicação chamada '{name}'.",
                                query=name, candidates=[])
                # Several equally good matches. Nano asks; it does not pick.
                return fail("ambiguous",
                            f"Há mais do que uma aplicação que corresponde a '{name}'. Qual queres?",
                            query=name,
                            candidates=[e.as_dict(s) for e, s in matches])

        outcome = applications.launch(entry)
        status = "already_running" if outcome["already_running"] else "launched"
        message = (f"{entry.name} já estava aberto e foi trazido para a frente."
                   if outcome["already_running"] else f"{entry.name} foi aberto.")
        return ok(status, message, app=entry.as_dict(), pid=outcome.get("pid"))

    return _guard("app.launch", run)


# --------------------------------------------------------------------------
#  Windows
# --------------------------------------------------------------------------


def pc_window_list(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        found = windows.list_windows()
        return ok("listed", f"{len(found)} janela(s) aberta(s).",
                  windows=found, count=len(found))

    return _guard("window.list", run)


def _window_action(operation: str, arguments: dict[str, Any], action, verb: str,
                   *, allow_partial: bool = True) -> dict:
    def run() -> dict:
        args = arguments or {}
        window_id = args.get("window_id")
        target = windows.resolve_window(
            window_id=window_id if window_id not in (None, "") else None,
            query=str(args.get("query") or "").strip() or None,
            allow_partial=allow_partial,
        )
        result = action(int(target["window_id"]))
        title = target["title"]

        if operation == "window.focus":
            if not result["focused"]:
                return fail("focus_refused", result["detail"], window=target)
            return ok("focused", f"'{title}' está em primeiro plano.", window=target)

        if operation == "window.close":
            if not result["closed"]:
                return fail("refused", result["detail"], window=target)
            return ok("closed", f"'{title}' foi fechada.", window=target)

        if not result["changed"]:
            return fail("state_unchanged",
                        f"'{title}' não mudou para o estado pedido (está '{result['state']}').",
                        window=target, state=result["state"])
        return ok(result["expected"], f"'{title}' foi {verb}.",
                  window={**target, "state": result["state"]})

    return _guard(operation, run)


def pc_window_focus(arguments: dict[str, Any]) -> dict:
    return _window_action("window.focus", arguments, windows.focus, "focada")


def pc_window_minimize(arguments: dict[str, Any]) -> dict:
    return _window_action("window.minimize", arguments, windows.minimize, "minimizada")


def pc_window_maximize(arguments: dict[str, Any]) -> dict:
    return _window_action("window.maximize", arguments, windows.maximize, "maximizada")


def pc_window_restore(arguments: dict[str, Any]) -> dict:
    return _window_action("window.restore", arguments, windows.restore, "restaurada")


def pc_window_close(arguments: dict[str, Any]) -> dict:
    """Ask a window to close. Graceful only -- never a process kill.

    ``allow_partial=False``: the only destructive verb in PC Control V1 refuses
    a loose title match. It needs a window_id from pc_window_list, an exact
    title, or a process name -- so a misheard word cannot land on a window the
    user did not mean.
    """
    return _window_action("window.close", arguments, windows.close, "fechada",
                          allow_partial=False)


# --------------------------------------------------------------------------
#  Volume
# --------------------------------------------------------------------------


def pc_volume_get(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        state = audio.get_state()
        suffix = " (sem som)" if state["muted"] else ""
        return ok("read", f"O volume está a {state['level']}%{suffix}.", **state)

    return _guard("volume.get", run)


def pc_volume_set(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = audio.set_level((arguments or {}).get("level"))
        return ok("set", f"Volume definido para {result['level']}%.", **result)

    return _guard("volume.set", run)


def pc_volume_change(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = audio.change_level((arguments or {}).get("delta"))
        direction = "aumentado" if result["delta"] >= 0 else "reduzido"
        return ok("changed", f"Volume {direction} para {result['level']}%.", **result)

    return _guard("volume.change", run)


def pc_volume_mute(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = audio.set_mute(True)
        return ok("muted", "Som desligado.", **result)

    return _guard("volume.mute", run)


def pc_volume_unmute(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = audio.set_mute(False)
        return ok("unmuted", f"Som ligado, a {result['level']}%.", **result)

    return _guard("volume.unmute", run)


# --------------------------------------------------------------------------
#  Files and folders
# --------------------------------------------------------------------------


def pc_folder_open(arguments: dict[str, Any]) -> dict:
    """Open a folder: either a known name, or an explicit validated path.

    THE TWO ARGUMENTS ARE NOT REDUNDANT.

    ToolExecutor centrally resolves every argument called `path` against the
    workspace root before a handler ever sees it -- which is exactly what we
    want for a real path, and exactly wrong for the word "Downloads", which it
    turned into <workspace>/Downloads and reported as missing. So known folder
    NAMES arrive as `folder` (untouched by that resolution and looked up in the
    user's own profile), and real paths arrive as `path` and keep the central
    validation. `folder` is preferred when both are present.
    """
    def run() -> dict:
        args = arguments or {}
        target = str(args.get("folder") or "").strip() or str(args.get("path") or "").strip()
        result = files.open_folder(target)
        return ok("opened", f"Abri a pasta {result['name']}.", **result)

    return _guard("folder.open", run)


def pc_file_search(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        roots = args.get("roots")
        result = files.search_files(
            str(args.get("query") or ""),
            roots=[str(r) for r in roots] if isinstance(roots, list) else None,
            max_results=int(args.get("max_results") or 20),
        )
        if not result["count"]:
            return fail("not_found",
                        f"Não encontrei ficheiros com '{result['query']}'.", **result)
        note = " (lista truncada)" if result["truncated"] else ""
        return ok("found", f"Encontrei {result['count']} ficheiro(s){note}.", **result)

    return _guard("file.search", run)


def pc_file_open(arguments: dict[str, Any]) -> dict:
    """Open a DOCUMENT. Executable and script types are refused, not gated."""
    def run() -> dict:
        result = files.open_file(str((arguments or {}).get("path") or ""))
        return ok("opened", f"Abri '{result['filename']}'.", **result)

    return _guard("file.open", run)


# --------------------------------------------------------------------------
#  System and screen
# --------------------------------------------------------------------------


def pc_system_info(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        snapshot = system.info()
        return ok("read",
                  f"{snapshot['os']} · RAM {snapshot['ram_used_gb']}/"
                  f"{snapshot['ram_total_gb']} GB ({snapshot['ram_percent']}%) · "
                  f"CPU {snapshot['cpu_percent']}%",
                  **snapshot)

    return _guard("system.info", run)


def pc_screenshot_capture(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = screen.capture()
        return ok("captured",
                  f"Captura guardada localmente ({result['width']}x{result['height']}).",
                  **result)

    return _guard("screenshot.capture", run)


# --------------------------------------------------------------------------
#  Declarations
# --------------------------------------------------------------------------

_WINDOW_TARGET_SCHEMA = {
    "window_id": {"type": "integer", "description": "Identificador devolvido por pc_window_list."},
    "query": {"type": "string", "description": "Parte do título ou nome do processo da janela."},
}


def _tool(name: str, description: str, properties: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required or [],
            },
        },
    }


def get_tools() -> list[dict]:
    return [
        _tool("pc_app_search",
              "Procura aplicações instaladas no computador pelo nome. Só devolve "
              "candidatos; não abre nada.",
              {"query": {"type": "string", "description": "Nome da aplicação, ex. 'Spotify'."}},
              ["query"]),
        _tool("pc_app_launch",
              "Abre uma aplicação instalada pelo nome. Não aceita caminhos de "
              "ficheiro nem executáveis: só nomes de aplicações conhecidas.",
              {"name": {"type": "string", "description": "Nome da aplicação a abrir."},
               "app_id": {"type": "string", "description": "app_id devolvido por pc_app_search."}}),
        _tool("pc_window_list",
              "Lista as janelas abertas visíveis, com título, processo e estado.",
              {}),
        _tool("pc_window_focus", "Traz uma janela aberta para primeiro plano.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_window_minimize", "Minimiza uma janela aberta.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_window_maximize", "Maximiza uma janela aberta.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_window_restore", "Restaura uma janela minimizada ou maximizada.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_window_close",
              "Pede o fecho de uma janela, como carregar no X. A aplicação pode "
              "perguntar se quer guardar. Nunca força o fecho do processo.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_volume_get", "Lê o volume actual do sistema e se está sem som.", {}),
        _tool("pc_volume_set", "Define o volume do sistema para um valor entre 0 e 100.",
              {"level": {"type": "integer", "minimum": 0, "maximum": 100}}, ["level"]),
        _tool("pc_volume_change",
              "Aumenta ou reduz o volume. Sem valor, usa 10 pontos.",
              {"delta": {"type": "integer", "minimum": -100, "maximum": 100}}),
        _tool("pc_volume_mute", "Desliga o som do sistema.", {}),
        _tool("pc_volume_unmute", "Volta a ligar o som do sistema.", {}),
        _tool("pc_folder_open",
              "Abre uma pasta no Explorador. Usa 'folder' para pastas conhecidas "
              "(Downloads, Documentos, Ambiente de Trabalho, Imagens, Música, "
              "Vídeos) e 'path' apenas para um caminho completo.",
              {"folder": {"type": "string",
                          "description": "Nome de pasta conhecida, ex. 'Downloads'."},
               "path": {"type": "string",
                        "description": "Caminho completo de uma pasta."}}),
        _tool("pc_file_search",
              "Procura ficheiros pelo nome no Ambiente de Trabalho, Documentos e "
              "Transferências. Devolve apenas metadados, nunca o conteúdo.",
              {"query": {"type": "string"},
               "roots": {"type": "array", "items": {"type": "string"},
                         "description": "Pastas onde procurar. Opcional."},
               "max_results": {"type": "integer", "minimum": 1, "maximum": MAX_FILE_RESULTS}},
              ["query"]),
        _tool("pc_file_open",
              "Abre um documento existente na aplicação predefinida do Windows. "
              "Recusa executáveis e scripts (.exe, .bat, .ps1, .msi, ...).",
              {"path": {"type": "string"}}, ["path"]),
        _tool("pc_system_info",
              "Estado do computador: SO, CPU, RAM, disco, GPU, bateria, uptime.", {}),
        _tool("pc_screenshot_capture",
              "Captura o ecrã para um ficheiro local. A imagem NÃO é enviada ao "
              "modelo nem para a nuvem.", {}),
    ]


TOOL_HANDLERS = {
    "pc_app_search": pc_app_search,
    "pc_app_launch": pc_app_launch,
    "pc_window_list": pc_window_list,
    "pc_window_focus": pc_window_focus,
    "pc_window_minimize": pc_window_minimize,
    "pc_window_maximize": pc_window_maximize,
    "pc_window_restore": pc_window_restore,
    "pc_window_close": pc_window_close,
    "pc_volume_get": pc_volume_get,
    "pc_volume_set": pc_volume_set,
    "pc_volume_change": pc_volume_change,
    "pc_volume_mute": pc_volume_mute,
    "pc_volume_unmute": pc_volume_unmute,
    "pc_folder_open": pc_folder_open,
    "pc_file_search": pc_file_search,
    "pc_file_open": pc_file_open,
    "pc_system_info": pc_system_info,
    "pc_screenshot_capture": pc_screenshot_capture,
}
