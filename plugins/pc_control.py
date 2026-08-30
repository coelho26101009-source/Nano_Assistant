"""Nano PC Control V2 — the tool surface the model is allowed to see.

This module is a THIN declaration layer. Every handler validates its typed
arguments and delegates straight into ``core.pc_control``; none of them build a
command line, and there is no `subprocess` import anywhere in the package. The
model chooses a tool and typed values, and those values reach a Win32 call as
values.

THE V2 PREMISE, IN ONE SENTENCE: broad capability coverage through MANY NARROW
TOOLS, never one generic executor.

That is why there are fifty-odd small tools below instead of a handful of
flexible ones. A single `computer_action({type, args})` would be shorter to
write and impossible to reason about: every refusal elsewhere could be spelled
out inside it. So each capability has its own schema, its own risk, its own
confirmation rule and its own target — and a thing Nano cannot do has nowhere
to be expressed.

The same logic rules out the tempting shortcuts:

* no key-sequence string — text is Unicode characters, chords come from a table
* no `ms-settings:` URI — a section NAME maps to a constant URI in source
* no executable path — `app.launch` takes a name resolved against a catalogue
* no permanent delete — "delete" means the Recycle Bin, and it is verified
* no keystrokes into a console — that composition would be a shell, so the
  input tools refuse a terminal window outright

Why a flat module rather than a ``plugins/pc_control/`` package: the loader
imports ``plugins/*.py`` only. The Windows implementation lives in separate
modules under ``core/pc_control/`` -- this file is the seam between them and
the tool registry.

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

from core.pc_control import (
    applications,
    audio,
    clipboard,
    display,
    fileops,
    files,
    geometry,
    keyboard,
    power,
    screen,
    settings,
    system,
    web,
    winapi,
    windows,
)
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

    `winapi.WindowsUnavailable` gets its own status rather than falling into
    that generic branch. Every `core/pc_control/*.py` entry point is supposed
    to check `winapi.IS_WINDOWS` itself and raise a proper
    `PCControlError("unsupported_platform", ...)` before ever touching a Win32
    call -- but that is an easy check to forget in one of ~50 handlers, and
    when it is, the raw `WindowsUnavailable` used to surface as an opaque
    "internal_error" instead of the same intentional, structured
    "unsupported_platform" every other Windows-only tool already reports off
    Windows. This is a safety net for that one failure mode specifically, not
    a general excuse to skip the per-module check -- it fixes how a missed
    check is REPORTED, not whether the check should exist.
    """
    try:
        return function(*args, **kwargs)
    except PCControlError as exc:
        return from_error(exc)
    except winapi.WindowsUnavailable:
        return fail("unsupported_platform", "Esta ação só funciona no Windows.", operation=operation)
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


def _resolve_app(args: dict) -> applications.AppEntry:
    """One catalogue entry, or a PCControlError describing why not.

    ``app_id`` is looked up in the catalogue; it is a KEY, not a location. If
    neither the name nor the id resolves to a catalogue entry, nothing happens.
    """
    app_id = str(args.get("app_id") or "").strip()
    name = str(args.get("name") or "").strip()

    entry = applications.find_by_app_id(app_id) if app_id else None
    if entry is not None:
        return entry
    if app_id and not name:
        raise PCControlError("not_found",
                             "Essa aplicação já não está na lista de aplicações instaladas.",
                             app_id=app_id)
    if not name:
        raise PCControlError("invalid_input", "É preciso dizer que aplicação.")

    entry, matches = applications.resolve(name)
    if entry is not None:
        return entry
    if not matches:
        raise PCControlError("not_found",
                             f"Não encontrei nenhuma aplicação chamada '{name}'.",
                             query=name, candidates=[])
    # Several equally good matches. Nano asks; it does not pick.
    raise PCControlError("ambiguous",
                         f"Há mais do que uma aplicação que corresponde a '{name}'. Qual queres?",
                         query=name, candidates=[e.as_dict(s) for e, s in matches])


def pc_app_launch(arguments: dict[str, Any]) -> dict:
    """Launch an installed application BY NAME. Never by path."""
    def run() -> dict:
        entry = _resolve_app(arguments or {})
        outcome = applications.launch(entry)
        status = "already_running" if outcome["already_running"] else "launched"
        message = (f"{entry.name} já estava aberto e foi trazido para a frente."
                   if outcome["already_running"] else f"{entry.name} foi aberto.")
        return ok(status, message, app=entry.as_dict(), pid=outcome.get("pid"))

    return _guard("app.launch", run)


def pc_app_switch(arguments: dict[str, Any]) -> dict:
    """Bring an already-running application forward. Never launches anything.

    Deliberately does NOT fall back to launching: "muda para o Discord" when
    Discord is closed is a question ("queres que o abra?"), not a licence to
    start a program the user did not ask for.
    """
    def run() -> dict:
        entry = _resolve_app(arguments or {})
        wanted = applications.executable_names_for(entry)
        open_windows = [w for w in windows.list_windows()
                        if (w.get("process") or "").lower() in wanted]
        if not open_windows:
            return fail("not_found",
                        f"{entry.name} não está aberto neste momento.",
                        app=entry.as_dict())

        # Prefer a window that is not minimised; focusing a minimised one works
        # but restores it, which is a bigger change than the user asked for.
        ordered = sorted(open_windows, key=lambda w: w["state"] == "minimized")
        target = ordered[0]
        result = windows.focus(int(target["window_id"]))
        if not result["focused"]:
            return fail("refused", result["detail"], app=entry.as_dict(), window=target)
        return ok("focused", f"{entry.name} está em primeiro plano.",
                  app=entry.as_dict(), window=target,
                  other_windows=len(open_windows) - 1)

    return _guard("app.switch", run)


def pc_app_list_running(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        running = applications.running_applications()
        return ok("listed", f"{len(running)} aplicação(ões) com janelas abertas.",
                  applications=running, count=len(running))

    return _guard("app.list_running", run)


# --------------------------------------------------------------------------
#  Windows — state
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

    ``allow_partial=False``: a destructive verb refuses a loose title match. It
    needs a window_id from pc_window_list, an exact title, or a process name --
    so a misheard word cannot land on a window the user did not mean.
    """
    return _window_action("window.close", arguments, windows.close, "fechada",
                          allow_partial=False)


# --------------------------------------------------------------------------
#  Windows — geometry
# --------------------------------------------------------------------------


def _resolve_window_arg(args: dict, *, allow_partial: bool = True) -> dict:
    window_id = args.get("window_id")
    return windows.resolve_window(
        window_id=window_id if window_id not in (None, "") else None,
        query=str(args.get("query") or "").strip() or None,
        allow_partial=allow_partial,
    )


def _geometry_result(status: str, target: dict, outcome: dict, message: str) -> dict:
    payload = ok(status, message,
                 window={**target, "state": "normal"},
                 geometry=outcome["after"],
                 previous=outcome["before"],
                 monitor=outcome["monitor"])
    if outcome["clamped"]:
        payload["clamped"] = True
        payload["requested"] = outcome["requested"]
        payload["message"] += (" As coordenadas foram ajustadas para a janela "
                               "continuar acessível no ecrã.")
    if not outcome["moved"]:
        # An honest branch: SetWindowPos succeeded and the window did not move,
        # which happens with fixed-size or self-positioning applications.
        return fail("state_unchanged",
                    f"'{target['title']}' não se mexeu. A aplicação pode estar a "
                    "controlar a própria posição.",
                    window=target, geometry=outcome["after"])
    return payload


def pc_window_move(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        hwnd = int(target["window_id"])
        current = geometry.current_rect(hwnd)
        x = geometry.coordinate(args.get("x"), "x")
        y = geometry.coordinate(args.get("y"), "y")
        outcome = geometry.apply_geometry(hwnd, x, y, current["width"], current["height"])
        return _geometry_result("moved", target, outcome,
                                f"'{target['title']}' foi movida.")

    return _guard("window.move", run)


def pc_window_resize(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        hwnd = int(target["window_id"])
        current = geometry.current_rect(hwnd)
        width = geometry.coordinate(args.get("width"), "width")
        height = geometry.coordinate(args.get("height"), "height")
        outcome = geometry.apply_geometry(hwnd, current["x"], current["y"], width, height)
        return _geometry_result("resized", target, outcome,
                                f"'{target['title']}' foi redimensionada.")

    return _guard("window.resize", run)


def pc_window_center(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        hwnd = int(target["window_id"])
        monitor = geometry.monitor_for_window(hwnd)
        current = geometry.current_rect(hwnd)
        left, top, right, bottom = monitor["work_area"]
        width = min(current["width"], monitor["work_width"])
        height = min(current["height"], monitor["work_height"])
        x = left + ((right - left) - width) // 2
        y = top + ((bottom - top) - height) // 2
        outcome = geometry.apply_geometry(hwnd, x, y, width, height, monitor=monitor)
        return _geometry_result("centered", target, outcome,
                                f"'{target['title']}' está centrada no monitor "
                                f"{monitor['number']}.")

    return _guard("window.center", run)


def pc_window_snap(arguments: dict[str, Any]) -> dict:
    """Snap a window to a half or quarter of a monitor's work area.

    No coordinates cross this boundary: the caller names a POSITION and the
    rectangle is computed here from the real work area, which is also why the
    result respects the taskbar.
    """
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        hwnd = int(target["window_id"])
        monitor = (geometry.resolve_monitor(args.get("monitor"))
                   if args.get("monitor") is not None
                   else geometry.monitor_for_window(hwnd))
        x, y, width, height = geometry.snap_rect(args.get("position"), monitor)
        outcome = geometry.apply_geometry(hwnd, x, y, width, height, monitor=monitor)
        position = str(args.get("position")).strip().lower()
        return _geometry_result("snapped", target, outcome,
                                f"'{target['title']}' foi colocada em '{position}' "
                                f"no monitor {monitor['number']}.")

    return _guard("window.snap", run)


def pc_window_move_monitor(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        hwnd = int(target["window_id"])
        monitor = geometry.resolve_monitor(args.get("monitor"))
        current = geometry.current_rect(hwnd)
        source = geometry.monitor_for_window(hwnd)
        if source["number"] == monitor["number"]:
            return fail("state_unchanged",
                        f"'{target['title']}' já está no monitor {monitor['number']}.",
                        window=target, monitor=monitor["number"])
        # Keep the window's position RELATIVE to its current screen, so moving
        # it does not also silently reposition it.
        left, top, right, bottom = monitor["work_area"]
        offset_x = current["x"] - source["work_area"][0]
        offset_y = current["y"] - source["work_area"][1]
        width = min(current["width"], monitor["work_width"])
        height = min(current["height"], monitor["work_height"])
        x = min(max(left, left + offset_x), max(left, right - width))
        y = min(max(top, top + offset_y), max(top, bottom - height))
        outcome = geometry.apply_geometry(hwnd, x, y, width, height, monitor=monitor)
        return _geometry_result("moved", target, outcome,
                                f"'{target['title']}' foi para o monitor {monitor['number']}.")

    return _guard("window.move_monitor", run)


def pc_window_set_topmost(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        target = _resolve_window_arg(args)
        wanted = args.get("topmost")
        if not isinstance(wanted, bool):
            return fail("invalid_input", "É preciso dizer se fica sempre à frente ou não.")
        result = geometry.set_topmost(int(target["window_id"]), wanted)
        if not result["applied"]:
            return fail("failed",
                        f"O Windows não alterou o estado de '{target['title']}'.",
                        window=target)
        state = "sempre à frente" if result["topmost"] else "com prioridade normal"
        return ok("topmost_set", f"'{target['title']}' ficou {state}.",
                  window=target, topmost=result["topmost"],
                  was_topmost=result["was_topmost"])

    return _guard("window.set_topmost", run)


# --------------------------------------------------------------------------
#  Windows — batch
# --------------------------------------------------------------------------


def pc_window_batch_state(arguments: dict[str, Any]) -> dict:
    """Minimise or restore every window of one application.

    Reversible, and therefore not confirmation-gated. `resolve_group` still
    matches by process name or exact title, never by a loose substring: "todas
    as janelas do Discord" must mean the Discord process, not every window with
    that word somewhere in its caption.
    """
    def run() -> dict:
        args = arguments or {}
        state = str(args.get("state") or "").strip().lower()
        if state not in {"minimize", "restore"}:
            return fail("invalid_input", "O estado tem de ser 'minimize' ou 'restore'.",
                        allowed=["minimize", "restore"])
        group = windows.resolve_group(str(args.get("app") or ""))
        action = windows.minimize if state == "minimize" else windows.restore
        changed = []
        unchanged = []
        for window in group:
            outcome = action(int(window["window_id"]))
            (changed if outcome["changed"] else unchanged).append(window["title"])
        verb = "minimizada(s)" if state == "minimize" else "restaurada(s)"
        if not changed:
            return fail("state_unchanged",
                        f"Nenhuma das {len(group)} janelas mudou de estado.",
                        **windows.summarise(group))
        return ok("batch_applied",
                  f"{len(changed)} de {len(group)} janela(s) {verb}.",
                  changed=changed, unchanged=unchanged, **windows.summarise(group))

    return _guard("window.batch_state", run)


def pc_window_batch_close(arguments: dict[str, Any]) -> dict:
    """Ask every window of one application to close. Graceful, never a kill.

    HIGH RISK, ALWAYS CONFIRMED, and the confirmation names the count and the
    titles -- because "fecha tudo do Discord" is a very different decision when
    it is one window than when it is nine.
    """
    def run() -> dict:
        group = windows.resolve_group(str((arguments or {}).get("app") or ""))
        closed, refused = [], []
        for window in group:
            outcome = windows.close(int(window["window_id"]))
            (closed if outcome["closed"] else refused).append(window["title"])
        if not closed:
            return fail("refused",
                        "Nenhuma das janelas fechou. Podem estar a perguntar se "
                        "queres guardar o trabalho — o Nano não força o fecho.",
                        refused=refused, **windows.summarise(group))
        message = f"{len(closed)} de {len(group)} janela(s) fechada(s)."
        if refused:
            message += (f" {len(refused)} continuam abertas; o Nano não força o fecho.")
        return ok("batch_closed", message, closed=closed, refused=refused,
                  **windows.summarise(group))

    return _guard("window.batch_close", run)


# --------------------------------------------------------------------------
#  Volume and media
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


def pc_media_control(arguments: dict[str, Any]) -> dict:
    """Send a media transport key to whatever owns playback.

    THE RESULT IS DELIBERATELY MODEST. Windows routes these keys to the current
    media session, and nothing reports back whether an application acted on
    them. Nano knows it sent the key; it does not know that Spotify paused. So
    the result says exactly that, and `confirmed` is false. Reading the real
    playback state needs the Windows media-session API (GSMTC), which needs a
    WinRT dependency this project does not have -- see PC_CONTROL.md.
    """
    def run() -> dict:
        result = keyboard.press_media((arguments or {}).get("action"))
        if not result["sent"]:
            return fail("failed", "O Windows não aceitou o comando de reprodução.",
                        action=result["action"])
        return ok("sent",
                  f"Enviei o comando '{result['label']}' ao Windows. Não consigo "
                  "confirmar se alguma aplicação respondeu.",
                  action=result["action"], label=result["label"], confirmed=False)

    return _guard("media.control", run)


# --------------------------------------------------------------------------
#  Display
# --------------------------------------------------------------------------


def pc_display_info(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        snapshot = display.info()
        controllable = [m for m in snapshot["monitors"] if m["brightness_supported"]]
        note = ("" if controllable else
                " Nenhum destes monitores permite alterar o brilho por software.")
        return ok("read", f"{snapshot['count']} monitor(es).{note}", **snapshot)

    return _guard("display.info", run)


def pc_display_set_brightness(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = display.set_brightness(args.get("level"), args.get("monitor"))
        return ok("set", f"Brilho do monitor {result['monitor']} definido para "
                         f"{result['level']}%.", **result)

    return _guard("display.set_brightness", run)


def pc_display_change_brightness(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = display.change_brightness(args.get("delta"), args.get("monitor"))
        direction = "aumentado" if result["delta"] >= 0 else "reduzido"
        return ok("changed", f"Brilho do monitor {result['monitor']} {direction} "
                             f"para {result['level']}%.", **result)

    return _guard("display.change_brightness", run)


# --------------------------------------------------------------------------
#  Clipboard
# --------------------------------------------------------------------------


def pc_clipboard_read(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = clipboard.read_text()
        if result["empty"]:
            return ok("read", "A área de transferência está vazia.", **result)
        note = " (cortado)" if result["truncated"] else ""
        return ok("read", f"A área de transferência tem {result['characters']} "
                          f"caracteres{note}.", **result)

    return _guard("clipboard.read", run)


def pc_clipboard_write(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = clipboard.write_text((arguments or {}).get("text"))
        if not result["verified"]:
            return fail("failed",
                        "Escrevi na área de transferência mas não consegui "
                        "confirmar o conteúdo; outra aplicação pode tê-la mudado.")
        return ok("written", f"Copiei {result['characters']} caracteres para a área "
                             "de transferência.", **result)

    return _guard("clipboard.write", run)


def pc_clipboard_clear(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = clipboard.clear()
        if not result["cleared"]:
            return fail("failed", "A área de transferência não ficou vazia.")
        return ok("cleared", "Área de transferência limpa.", **result)

    return _guard("clipboard.clear", run)


# --------------------------------------------------------------------------
#  Keyboard and pointer
# --------------------------------------------------------------------------


def _aim(args: dict) -> dict:
    """Resolve, focus and VERIFY the window an input action is aimed at.

    Focusing happens here, inside the handler, and therefore AFTER any approval
    dialog has closed -- which is the only correct moment. Nano's own
    confirmation window holds the foreground while the user reads it, so a
    target captured before approval, or an implicit "whatever is in front",
    would resolve to Nano itself.
    """
    target = windows.resolve_input_target(
        window_id=args.get("window_id") if args.get("window_id") not in (None, "") else None,
        query=str(args.get("query") or "").strip() or None,
    )
    focus = keyboard.focus_and_verify(int(target["window_id"]))
    if not focus["focused"]:
        raise PCControlError("focus_refused", focus["detail"], window=target)
    return target


def pc_input_type_text(arguments: dict[str, Any]) -> dict:
    """Type text into a NAMED window. Always confirmed, showing the exact text."""
    def run() -> dict:
        args = arguments or {}
        text = keyboard.validate_text(args.get("text"))
        target = _aim(args)
        result = keyboard.type_text(text)
        if not result["complete"]:
            return fail("failed",
                        f"Só {result['sent']} de {result['expected']} teclas foram "
                        "aceites pelo Windows.",
                        window=target, **result)
        return ok("typed", f"Escrevi {result['characters']} caracteres em "
                           f"'{target['title']}'.",
                  window=target, characters=result["characters"])

    return _guard("input.type_text", run)


def pc_input_press_key(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        key, _code = keyboard.validate_key(args.get("key"))
        target = _aim(args)
        result = keyboard.press_key(key)
        if not result["sent"]:
            return fail("failed", "O Windows não aceitou a tecla.", window=target)
        # "sent", not "pressed": see the note on pc_input_hotkey below.
        return ok("sent",
                  f"Enviei a tecla '{key}' para '{target['title']}'. Não consigo "
                  "confirmar se a aplicação reagiu.",
                  window=target, key=key, confirmed=False)

    return _guard("input.press_key", run)


def pc_input_hotkey(arguments: dict[str, Any]) -> dict:
    """Send one allow-listed chord. Editing chords need a named window."""
    def run() -> dict:
        args = arguments or {}
        name, (_code, _modifiers, requires_target, label) = keyboard.validate_hotkey(
            args.get("hotkey"))
        target = None
        if requires_target:
            target = _aim(args)
        elif args.get("window_id") or args.get("query"):
            return fail("invalid_input",
                        f"'{label}' actua no ambiente de trabalho, não numa janela "
                        "específica.")
        result = keyboard.press_hotkey(name)
        if not result["sent"]:
            return fail("failed", f"O Windows não aceitou {label}.")
        where = f" em '{target['title']}'" if target else ""
        # THE HONEST CLAIM IS "SENT". SendInput reports success as soon as the
        # event is injected, which is not the same as the application acting on
        # it -- measured on this machine, Win+D takes effect while Ctrl+C aimed
        # at Windows 11 Notepad does not, and the two are indistinguishable
        # from here. Saying "carreguei" would be reporting an outcome Nano did
        # not observe.
        return ok("sent",
                  f"Enviei {label}{where}. Não consigo confirmar se a aplicação "
                  "reagiu ao atalho.",
                  hotkey=name, label=label, window=target, confirmed=False)

    return _guard("input.hotkey", run)


def pc_pointer_scroll(arguments: dict[str, Any]) -> dict:
    """Scroll inside a NAMED window. There is no coordinate argument anywhere.

    Nano has no click-at-a-pixel tool and no pointer-move tool. See
    docs/architecture/PC_CONTROL.md for why that was deferred rather than
    approximated.
    """
    def run() -> dict:
        args = arguments or {}
        direction = str(args.get("direction") or "down").strip().lower()
        if direction not in {"up", "down", "left", "right"}:
            return fail("invalid_input", "A direcção tem de ser up, down, left ou right.",
                        allowed=["up", "down", "left", "right"])
        raw = args.get("clicks")
        magnitude = keyboard.scroll_magnitude(3 if raw is None else raw)
        target = _aim(args)
        signed = magnitude if direction in {"up", "right"} else -magnitude
        result = keyboard.scroll(signed, horizontal=direction in {"left", "right"})
        if not result["sent"]:
            return fail("failed", "O Windows não aceitou o scroll.", window=target)
        return ok("scrolled", f"Fiz scroll {direction} em '{target['title']}'.",
                  window=target, direction=direction, clicks=abs(result["clicks"]))

    return _guard("pointer.scroll", run)


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


def pc_folder_create(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = fileops.create_folder(args.get("name"), folder=args.get("folder"),
                                       path=args.get("path"))
        return ok("created", f"Criei a pasta '{result['name']}'.", **result)

    return _guard("folder.create", run)


def pc_file_create_text(arguments: dict[str, Any]) -> dict:
    """Create a plain text file. Script and executable extensions are refused."""
    def run() -> dict:
        args = arguments or {}
        result = fileops.create_text_file(args.get("name"), args.get("content") or "",
                                          folder=args.get("folder"), path=args.get("path"))
        return ok("created", f"Criei '{result['name']}' "
                             f"({result['bytes_written']} bytes).", **result)

    return _guard("file.create_text", run)


def pc_file_copy(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = fileops.copy_file(args.get("source"), args.get("destination"))
        return ok("copied", f"Copiei para '{result['destination']['name']}'.", **result)

    return _guard("file.copy", run)


def pc_file_move(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = fileops.move_file(args.get("source"), args.get("destination"))
        return ok("moved", f"Movi para '{result['destination']['path']}'.", **result)

    return _guard("file.move", run)


def pc_file_rename(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = fileops.rename_path(args.get("source"), args.get("new_name"))
        return ok("renamed", f"'{result['previous_name']}' passou a chamar-se "
                             f"'{result['name']}'.", **result)

    return _guard("file.rename", run)


def _recycled(result: dict, kind: str) -> dict:
    item = result["item"]
    if not result["recycled"]:
        # The item is gone but the bin count did not rise. Two different true
        # statements; this is the one that is actually true, and the user needs
        # to know the undo they expect is not there.
        return ok("removed_not_recycled",
                  f"'{item['name']}' foi removido, mas o Windows não o colocou na "
                  "Reciclagem — não vai ser possível recuperá-lo por lá.",
                  **result)
    return ok("recycled", f"Enviei {kind} '{item['name']}' para a Reciclagem. "
                          "Podes recuperá-lo a partir de lá.", **result)


def pc_file_recycle(arguments: dict[str, Any]) -> dict:
    """Send a file to the Recycle Bin. NEVER a permanent delete."""
    def run() -> dict:
        return _recycled(fileops.recycle_file((arguments or {}).get("path")), "o ficheiro")

    return _guard("file.recycle", run)


def pc_folder_recycle(arguments: dict[str, Any]) -> dict:
    """Send a folder to the Recycle Bin. NEVER a permanent delete."""
    def run() -> dict:
        return _recycled(fileops.recycle_folder((arguments or {}).get("path")), "a pasta")

    return _guard("folder.recycle", run)


# --------------------------------------------------------------------------
#  Web and Windows Settings
# --------------------------------------------------------------------------


def pc_web_open_url(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = web.open_url((arguments or {}).get("url"))
        return ok("opened", f"Abri {result['host']} no navegador.", **result)

    return _guard("web.open_url", run)


def pc_web_search(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        args = arguments or {}
        result = web.search(args.get("query"), args.get("engine"))
        return ok("opened", f"Abri a pesquisa por '{result['query']}' no navegador.",
                  **result)

    return _guard("web.search", run)


def pc_settings_open(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        result = settings.open_section((arguments or {}).get("section"))
        return ok("opened", f"Abri as definições de {result['label']}.", **result)

    return _guard("settings.open", run)


# --------------------------------------------------------------------------
#  System information
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


def pc_network_status(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        snapshot = system.network_status()
        state = "ligado" if snapshot["connected"] else "sem ligação"
        kind = f" ({snapshot['connection_type']})" if snapshot["connection_type"] else ""
        return ok("read", f"O computador está {state}{kind}.", **snapshot)

    return _guard("network.status", run)


def pc_storage_info(arguments: dict[str, Any]) -> dict:
    def run() -> dict:
        snapshot = system.storage_info()
        return ok("read", f"{snapshot['free_gb']} GB livres de "
                          f"{snapshot['total_gb']} GB em {snapshot['count']} volume(s).",
                  **snapshot)

    return _guard("storage.info", run)


# --------------------------------------------------------------------------
#  Power and session
# --------------------------------------------------------------------------


def _power(action: str) -> dict:
    def run() -> dict:
        result = power.perform(action)
        return ok("requested", f"Pedi ao Windows para {result['label']}.", **result)

    return _guard(f"power.{action}", run)


def pc_session_lock(arguments: dict[str, Any]) -> dict:
    return _power("lock")


def pc_power_sleep(arguments: dict[str, Any]) -> dict:
    return _power("sleep")


def pc_power_restart(arguments: dict[str, Any]) -> dict:
    return _power("restart")


def pc_power_shutdown(arguments: dict[str, Any]) -> dict:
    return _power("shutdown")


def pc_session_logoff(arguments: dict[str, Any]) -> dict:
    return _power("logoff")


# --------------------------------------------------------------------------
#  Screen
# --------------------------------------------------------------------------


def pc_screenshot_capture(arguments: dict[str, Any]) -> dict:
    """Capture the desktop, the active window, or one named window.

    The image NEVER enters the model's context and is never uploaded: the
    result is a local path, a size and dimensions.
    """
    def run() -> dict:
        args = arguments or {}
        mode = str(args.get("mode") or "desktop").strip().lower()
        window = None
        if mode == "window":
            window = _resolve_window_arg(args)
        result = screen.capture(mode, window=window)
        return ok("captured",
                  f"Captura de {result['subject']} guardada localmente "
                  f"({result['width']}x{result['height']}).",
                  **result)

    return _guard("screenshot.capture", run)


# --------------------------------------------------------------------------
#  Declarations
# --------------------------------------------------------------------------

_WINDOW_TARGET_SCHEMA = {
    "window_id": {"type": "integer", "description": "Identificador devolvido por pc_window_list."},
    "query": {"type": "string", "description": "Parte do título ou nome do processo da janela."},
}

_APP_TARGET_SCHEMA = {
    "name": {"type": "string", "description": "Nome da aplicação."},
    "app_id": {"type": "string", "description": "app_id devolvido por pc_app_search."},
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
        # ---------------------------------------------------------- apps
        _tool("pc_app_search",
              "Procura aplicações instaladas no computador pelo nome. Só devolve "
              "candidatos; não abre nada.",
              {"query": {"type": "string", "description": "Nome da aplicação, ex. 'Spotify'."}},
              ["query"]),
        _tool("pc_app_launch",
              "Abre uma aplicação instalada pelo nome. Não aceita caminhos de "
              "ficheiro nem executáveis: só nomes de aplicações conhecidas.",
              dict(_APP_TARGET_SCHEMA)),
        _tool("pc_app_switch",
              "Traz para a frente uma aplicação que JÁ está aberta. Não abre nada: "
              "se a aplicação estiver fechada, devolve not_found.",
              dict(_APP_TARGET_SCHEMA)),
        _tool("pc_app_list_running",
              "Lista as aplicações com janelas abertas neste momento.", {}),

        # -------------------------------------------------------- windows
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
        _tool("pc_window_move",
              "Move uma janela para uma posição do ecrã. As coordenadas são "
              "ajustadas para a janela continuar visível.",
              {**_WINDOW_TARGET_SCHEMA,
               "x": {"type": "integer", "description": "Coordenada horizontal."},
               "y": {"type": "integer", "description": "Coordenada vertical."}},
              ["x", "y"]),
        _tool("pc_window_resize",
              "Altera o tamanho de uma janela, dentro dos limites do monitor.",
              {**_WINDOW_TARGET_SCHEMA,
               "width": {"type": "integer", "minimum": 1},
               "height": {"type": "integer", "minimum": 1}},
              ["width", "height"]),
        _tool("pc_window_center", "Centra uma janela no monitor onde está.",
              dict(_WINDOW_TARGET_SCHEMA)),
        _tool("pc_window_snap",
              "Encosta uma janela a metade ou a um quarto do monitor. Usa a área "
              "de trabalho real, por isso respeita a barra de tarefas.",
              {**_WINDOW_TARGET_SCHEMA,
               "position": {"type": "string", "enum": list(geometry.SNAP_MODES)},
               "monitor": {"type": "integer", "minimum": 1,
                           "description": "Número do monitor. Opcional."}},
              ["position"]),
        _tool("pc_window_move_monitor",
              "Move uma janela para outro monitor, mantendo a posição relativa.",
              {**_WINDOW_TARGET_SCHEMA,
               "monitor": {"type": "integer", "minimum": 1}},
              ["monitor"]),
        _tool("pc_window_set_topmost",
              "Faz uma janela ficar sempre à frente das outras, ou volta ao normal.",
              {**_WINDOW_TARGET_SCHEMA, "topmost": {"type": "boolean"}},
              ["topmost"]),
        _tool("pc_window_batch_state",
              "Minimiza ou restaura todas as janelas de uma aplicação. A aplicação "
              "é identificada pelo nome do processo ou pelo título exacto.",
              {"app": {"type": "string", "description": "Nome do processo, ex. 'discord'."},
               "state": {"type": "string", "enum": ["minimize", "restore"]}},
              ["app", "state"]),
        _tool("pc_window_batch_close",
              "Pede o fecho de TODAS as janelas de uma aplicação. Acção sensível: "
              "pede sempre confirmação e mostra quantas janelas serão afectadas.",
              {"app": {"type": "string", "description": "Nome do processo, ex. 'notepad'."}},
              ["app"]),

        # --------------------------------------------------- volume/media
        _tool("pc_volume_get", "Lê o volume actual do sistema e se está sem som.", {}),
        _tool("pc_volume_set", "Define o volume do sistema para um valor entre 0 e 100.",
              {"level": {"type": "integer", "minimum": 0, "maximum": 100}}, ["level"]),
        _tool("pc_volume_change",
              "Aumenta ou reduz o volume. Sem valor, usa 10 pontos.",
              {"delta": {"type": "integer", "minimum": -100, "maximum": 100}}),
        _tool("pc_volume_mute", "Desliga o som do sistema.", {}),
        _tool("pc_volume_unmute", "Volta a ligar o som do sistema.", {}),
        _tool("pc_media_control",
              "Envia um comando de reprodução ao Windows (play/pausa, faixa "
              "seguinte, anterior, parar). O Nano não consegue confirmar se "
              "alguma aplicação respondeu.",
              {"action": {"type": "string", "enum": sorted(keyboard.MEDIA_KEYS)}},
              ["action"]),

        # -------------------------------------------------------- display
        _tool("pc_display_info",
              "Lista os monitores, o seu tamanho e se permitem alterar o brilho.",
              {}),
        _tool("pc_display_set_brightness",
              "Define o brilho de um monitor (0-100). Só funciona em monitores que "
              "suportam controlo por software.",
              {"level": {"type": "integer", "minimum": 0, "maximum": 100},
               "monitor": {"type": "integer", "minimum": 1}},
              ["level"]),
        _tool("pc_display_change_brightness",
              "Aumenta ou reduz o brilho de um monitor. Sem valor, usa 10 pontos.",
              {"delta": {"type": "integer", "minimum": -100, "maximum": 100},
               "monitor": {"type": "integer", "minimum": 1}}),

        # ------------------------------------------------------ clipboard
        _tool("pc_clipboard_read",
              "Lê o texto que está na área de transferência. Acção sensível para a "
              "privacidade: pede sempre confirmação.",
              {}),
        _tool("pc_clipboard_write",
              "Copia texto para a área de transferência, substituindo o que lá "
              "estiver.",
              {"text": {"type": "string", "maxLength": clipboard.MAX_WRITE_CHARS}},
              ["text"]),
        _tool("pc_clipboard_clear", "Limpa a área de transferência.", {}),

        # ---------------------------------------------------------- input
        _tool("pc_input_type_text",
              "Escreve texto numa janela indicada. É obrigatório identificar a "
              "janela (window_id de pc_window_list, ou título/processo exacto). "
              "O Nano nunca escreve numa consola nem em janelas do próprio Nano.",
              {**_WINDOW_TARGET_SCHEMA,
               "text": {"type": "string", "maxLength": keyboard.MAX_TEXT_CHARS}},
              ["text"]),
        _tool("pc_input_press_key",
              "Carrega uma tecla numa janela indicada. Só teclas da lista.",
              {**_WINDOW_TARGET_SCHEMA,
               "key": {"type": "string", "enum": sorted(keyboard.KEY_ALLOWLIST)}},
              ["key"]),
        _tool("pc_input_hotkey",
              "Envia um atalho de teclado da lista. Os atalhos de edição precisam "
              "de uma janela indicada; switch_window e show_desktop actuam no "
              "ambiente de trabalho e não aceitam janela.",
              {**_WINDOW_TARGET_SCHEMA,
               "hotkey": {"type": "string", "enum": sorted(keyboard.HOTKEY_ALLOWLIST)}},
              ["hotkey"]),
        _tool("pc_pointer_scroll",
              "Faz scroll dentro de uma janela indicada. Não existe clique por "
              "coordenadas no Nano.",
              {**_WINDOW_TARGET_SCHEMA,
               "direction": {"type": "string", "enum": ["up", "down", "left", "right"]},
               "clicks": {"type": "integer", "minimum": 1,
                          "maximum": keyboard.MAX_SCROLL_CLICKS}},
              ["direction"]),

        # ---------------------------------------------------------- files
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
        _tool("pc_folder_create",
              "Cria uma pasta. Indica 'folder' (pasta conhecida) ou 'path' (caminho "
              "completo) para onde vai, e 'name' para o nome da nova pasta.",
              {"name": {"type": "string"},
               "folder": {"type": "string", "description": "Pasta conhecida onde criar."},
               "path": {"type": "string", "description": "Caminho completo da pasta onde criar."}},
              ["name"]),
        _tool("pc_file_create_text",
              "Cria um ficheiro de texto simples. Só extensões de texto "
              f"({', '.join(sorted(fileops.TEXT_EXTENSIONS))}); nunca scripts nem "
              "executáveis. Não substitui ficheiros existentes.",
              {"name": {"type": "string"},
               "content": {"type": "string", "maxLength": fileops.MAX_TEXT_BYTES},
               "folder": {"type": "string", "description": "Pasta conhecida onde criar."},
               "path": {"type": "string", "description": "Caminho completo da pasta onde criar."}},
              ["name", "content"]),
        _tool("pc_file_copy",
              "Copia um ficheiro. O destino pode ser uma pasta existente ou um "
              "caminho novo. Nunca substitui ficheiros existentes.",
              {"source": {"type": "string"}, "destination": {"type": "string"}},
              ["source", "destination"]),
        _tool("pc_file_move",
              "Move um ficheiro. O destino pode ser uma pasta existente ou um "
              "caminho novo. Nunca substitui ficheiros existentes.",
              {"source": {"type": "string"}, "destination": {"type": "string"}},
              ["source", "destination"]),
        _tool("pc_file_rename",
              "Muda o nome de um ficheiro ou pasta, no mesmo sítio.",
              {"source": {"type": "string"}, "new_name": {"type": "string"}},
              ["source", "new_name"]),
        _tool("pc_file_recycle",
              "Envia um ficheiro para a Reciclagem do Windows. NÃO apaga "
              "permanentemente — o ficheiro pode ser recuperado.",
              {"path": {"type": "string"}}, ["path"]),
        _tool("pc_folder_recycle",
              "Envia uma pasta e o seu conteúdo para a Reciclagem do Windows. NÃO "
              "apaga permanentemente.",
              {"path": {"type": "string"}}, ["path"]),

        # ------------------------------------------------------ web/settings
        _tool("pc_web_open_url",
              "Abre um endereço http ou https no navegador predefinido. Não lê a "
              "página nem interage com ela.",
              {"url": {"type": "string"}}, ["url"]),
        _tool("pc_web_search",
              "Abre uma pesquisa no navegador predefinido.",
              {"query": {"type": "string"},
               "engine": {"type": "string", "enum": sorted(web.SEARCH_ENGINES)}},
              ["query"]),
        _tool("pc_settings_open",
              "Abre uma secção das Definições do Windows. Só as secções da lista.",
              {"section": {"type": "string", "enum": sorted(settings.SECTIONS)}},
              ["section"]),

        # --------------------------------------------------------- system
        _tool("pc_system_info",
              "Estado do computador: SO, CPU, RAM, disco, GPU, bateria, uptime.", {}),
        _tool("pc_network_status",
              "Diz se o computador tem ligação e de que tipo. Não devolve "
              "endereços IP, MAC nem nomes de rede.", {}),
        _tool("pc_storage_info",
              "Espaço livre e usado em cada volume fixo.", {}),

        # ---------------------------------------------------------- power
        _tool("pc_session_lock",
              "Bloqueia a sessão do Windows. Pede sempre confirmação.", {}),
        _tool("pc_power_sleep",
              "Suspende o computador. Pede sempre confirmação.", {}),
        _tool("pc_power_restart",
              "Reinicia o computador. Acção crítica: pede sempre confirmação "
              "explícita e nunca força o fecho de aplicações.", {}),
        _tool("pc_power_shutdown",
              "Desliga o computador. Acção crítica: pede sempre confirmação "
              "explícita e nunca força o fecho de aplicações.", {}),
        _tool("pc_session_logoff",
              "Termina a sessão do Windows. Acção crítica: pede sempre "
              "confirmação explícita.", {}),

        # --------------------------------------------------------- screen
        _tool("pc_screenshot_capture",
              "Captura o ecrã, a janela activa, ou uma janela indicada, para um "
              "ficheiro local. A imagem NÃO é enviada ao modelo nem para a nuvem.",
              {**_WINDOW_TARGET_SCHEMA,
               "mode": {"type": "string", "enum": list(screen.CAPTURE_MODES),
                        "description": "desktop (predefinido), active_window ou window."}}),
    ]


TOOL_HANDLERS = {
    "pc_app_search": pc_app_search,
    "pc_app_launch": pc_app_launch,
    "pc_app_switch": pc_app_switch,
    "pc_app_list_running": pc_app_list_running,
    "pc_window_list": pc_window_list,
    "pc_window_focus": pc_window_focus,
    "pc_window_minimize": pc_window_minimize,
    "pc_window_maximize": pc_window_maximize,
    "pc_window_restore": pc_window_restore,
    "pc_window_close": pc_window_close,
    "pc_window_move": pc_window_move,
    "pc_window_resize": pc_window_resize,
    "pc_window_center": pc_window_center,
    "pc_window_snap": pc_window_snap,
    "pc_window_move_monitor": pc_window_move_monitor,
    "pc_window_set_topmost": pc_window_set_topmost,
    "pc_window_batch_state": pc_window_batch_state,
    "pc_window_batch_close": pc_window_batch_close,
    "pc_volume_get": pc_volume_get,
    "pc_volume_set": pc_volume_set,
    "pc_volume_change": pc_volume_change,
    "pc_volume_mute": pc_volume_mute,
    "pc_volume_unmute": pc_volume_unmute,
    "pc_media_control": pc_media_control,
    "pc_display_info": pc_display_info,
    "pc_display_set_brightness": pc_display_set_brightness,
    "pc_display_change_brightness": pc_display_change_brightness,
    "pc_clipboard_read": pc_clipboard_read,
    "pc_clipboard_write": pc_clipboard_write,
    "pc_clipboard_clear": pc_clipboard_clear,
    "pc_input_type_text": pc_input_type_text,
    "pc_input_press_key": pc_input_press_key,
    "pc_input_hotkey": pc_input_hotkey,
    "pc_pointer_scroll": pc_pointer_scroll,
    "pc_folder_open": pc_folder_open,
    "pc_file_search": pc_file_search,
    "pc_file_open": pc_file_open,
    "pc_folder_create": pc_folder_create,
    "pc_file_create_text": pc_file_create_text,
    "pc_file_copy": pc_file_copy,
    "pc_file_move": pc_file_move,
    "pc_file_rename": pc_file_rename,
    "pc_file_recycle": pc_file_recycle,
    "pc_folder_recycle": pc_folder_recycle,
    "pc_web_open_url": pc_web_open_url,
    "pc_web_search": pc_web_search,
    "pc_settings_open": pc_settings_open,
    "pc_system_info": pc_system_info,
    "pc_network_status": pc_network_status,
    "pc_storage_info": pc_storage_info,
    "pc_session_lock": pc_session_lock,
    "pc_power_sleep": pc_power_sleep,
    "pc_power_restart": pc_power_restart,
    "pc_power_shutdown": pc_power_shutdown,
    "pc_session_logoff": pc_session_logoff,
    "pc_screenshot_capture": pc_screenshot_capture,
}
