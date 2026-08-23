"""Listing and controlling top-level windows.

CLOSING IS THE ONLY CONSEQUENTIAL VERB HERE, AND IT IS A REQUEST.

`close` posts WM_CLOSE -- the same message the X button sends. The application
decides what happens next: it may prompt to save, it may refuse. There is no
TerminateProcess anywhere in this module and no fallback that reaches for one.
An application that declines to close is reported as having declined, because
"I killed it" and "it stayed open" are both honest answers and "closed" is not.

Everything else (list, focus, minimize, maximize, restore) is reversible and
cheap, and each one verifies the OS state afterwards rather than assuming the
call worked -- SetForegroundWindow in particular is allowed to refuse.
"""
from __future__ import annotations

import logging
import time

from core.pc_control import winapi
from core.pc_control.results import MAX_WINDOWS, PCControlError, clamp_text

logger = logging.getLogger("nano.pc_control.windows")

#: Window classes that are part of the desktop shell rather than an application
#: the user would say they have "open".
_SHELL_CLASSES = frozenset({
    "progman", "workerw", "shell_traywnd", "shell_secondarytraywnd",
    "button", "windows.ui.core.corewindow", "applicationframewindow_ghost",
})

#: How long to wait for an application to honour WM_CLOSE before reporting what
#: actually happened. Long enough for a normal window to go away, short enough
#: that a "do you want to save?" dialog does not hold the tool open.
_CLOSE_OBSERVE_SECONDS = 1.5
_CLOSE_POLL_SECONDS = 0.1


def _process_name(pid: int) -> str | None:
    if not pid:
        return None
    try:
        import psutil

        return psutil.Process(pid).name()
    except Exception:
        return None


def _is_user_window(hwnd: int) -> bool:
    """Whether a handle is a window the user would recognise as open.

    Windows keeps a large population of invisible, owned, cloaked and tool
    windows alive -- 148 handles on this machine for 5 real applications. A
    list full of those is worse than useless: the model would try to act on
    entries the user cannot see.
    """
    if not winapi.is_window_visible(hwnd):
        return False
    if not winapi.window_title(hwnd).strip():
        return False
    if winapi.is_cloaked(hwnd):
        return False
    if winapi.window_owner(hwnd):
        return False
    if winapi.window_ex_style(hwnd) & winapi.WS_EX_TOOLWINDOW:
        return False
    if winapi.window_class(hwnd).strip().lower() in _SHELL_CLASSES:
        return False
    return True


def describe(hwnd: int) -> dict:
    """Bounded, non-sensitive metadata for one window."""
    return {
        "window_id": int(hwnd),
        "title": clamp_text(winapi.window_title(hwnd), 200),
        "process": _process_name(winapi.window_pid(hwnd)),
        "visible": winapi.is_window_visible(hwnd),
        "state": winapi.window_placement_state(hwnd),
        "focused": hwnd == winapi.foreground_window(),
    }


def list_windows(*, limit: int = MAX_WINDOWS) -> list[dict]:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "O controlo de janelas só funciona no Windows.")

    handles: list[int] = []
    winapi.enum_top_level_windows(handles.append)

    described: list[dict] = []
    for hwnd in handles:
        if len(described) >= limit:
            break
        try:
            if _is_user_window(hwnd):
                described.append(describe(hwnd))
        except Exception:
            logger.debug("could not describe window %s", hwnd, exc_info=True)
    return described


def resolve_window(*, window_id: int | None = None, query: str | None = None,
                   allow_partial: bool = True) -> dict:
    """Find exactly one window, by handle or by an unambiguous title match.

    A query that matches several windows raises ``ambiguous`` carrying the
    candidates. This is the window-side half of the rule that a vague voice
    command must never pick a target on the user's behalf -- especially for
    `close`, where the wrong choice loses somebody's work.

    ``allow_partial=False`` additionally refuses a substring title match, so a
    destructive verb has to land on an exact title, a process name, or a
    window_id that came from ``list_windows``. THIS IS THE VOICE-AMBIGUITY
    RULE, and it is built on a signal that genuinely exists -- "the target was
    matched loosely" -- rather than on a confidence number Whisper does not
    provide. The real benchmark produced "Procuro fechar o relatório" for
    "Procura o ficheiro relatório"; a loose match plus a destructive verb is
    precisely the combination that must stop and ask.
    """
    if window_id is not None:
        try:
            hwnd = int(window_id)
        except (TypeError, ValueError):
            raise PCControlError("invalid_input", "Identificador de janela inválido.") from None
        if not winapi.is_window(hwnd):
            raise PCControlError("not_found", "Essa janela já não existe.")
        return describe(hwnd)

    text = str(query or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar a janela.")

    needle = text.casefold()
    windows = list_windows()
    exact = [w for w in windows if w["title"].casefold() == needle]
    process = [w for w in windows if (w["process"] or "").casefold() == needle
               or (w["process"] or "").casefold() == f"{needle}.exe"]
    partial = [w for w in windows if needle in w["title"].casefold()]

    buckets = (exact, process) if not allow_partial else (exact, process, partial)
    for bucket in buckets:
        if len(bucket) == 1:
            return bucket[0]
        if len(bucket) > 1:
            raise PCControlError(
                "ambiguous",
                f"Há {len(bucket)} janelas que correspondem a '{text}'. Qual delas?",
                candidates=bucket,
            )

    if not allow_partial and partial:
        # The window probably IS one of these, but "probably" is not good
        # enough to close somebody's unsaved work on.
        raise PCControlError(
            "ambiguous",
            f"Não tenho a certeza de qual janela é '{text}'. Confirma qual queres fechar.",
            candidates=partial,
        )
    raise PCControlError("not_found", f"Não encontrei nenhuma janela para '{text}'.")


def _apply(hwnd: int, command: int, expected: str) -> dict:
    """Run a ShowWindow command and report the state the OS ended up in."""
    winapi.show_window(hwnd, command)
    # ShowWindow's return value reports the PREVIOUS visibility, not success,
    # so it cannot be used as a success signal. The placement is re-read.
    time.sleep(0.05)
    state = winapi.window_placement_state(hwnd)
    return {"state": state, "expected": expected, "changed": state == expected}


def minimize(hwnd: int) -> dict:
    return _apply(hwnd, winapi.SW_MINIMIZE, "minimized")


def maximize(hwnd: int) -> dict:
    return _apply(hwnd, winapi.SW_MAXIMIZE, "maximized")


def restore(hwnd: int) -> dict:
    return _apply(hwnd, winapi.SW_RESTORE, "normal")


def focus(hwnd: int) -> dict:
    """Bring a window forward, and check whether Windows allowed it."""
    winapi.focus_window(hwnd)
    time.sleep(0.05)
    focused = winapi.foreground_window() == hwnd
    return {
        "focused": focused,
        "state": winapi.window_placement_state(hwnd),
        # Not a failure of Nano: Windows blocks foreground changes from a
        # process that does not already own the foreground.
        "detail": None if focused else "O Windows não permitiu trazer esta janela para a frente.",
    }


def close(hwnd: int) -> dict:
    """Ask a window to close and observe whether it did.

    GRACEFUL ONLY. WM_CLOSE lets the application run its own shutdown -- which
    is what gives the user the chance to save. If the window is still there
    afterwards, that is reported as ``refused``: the application is very likely
    showing a save prompt, and the correct behaviour is to say so, not to
    escalate.
    """
    title = clamp_text(winapi.window_title(hwnd), 200)
    if not winapi.post_close(hwnd):
        raise PCControlError("close_failed", f"Não foi possível pedir o fecho de '{title}'.")

    deadline = time.monotonic() + _CLOSE_OBSERVE_SECONDS
    while time.monotonic() < deadline:
        if not winapi.is_window(hwnd):
            return {"closed": True, "title": title}
        time.sleep(_CLOSE_POLL_SECONDS)

    return {
        "closed": False,
        "title": title,
        "detail": ("A aplicação não fechou. Pode estar a perguntar se queres guardar "
                   "o trabalho — o Nano não força o fecho."),
    }


__all__ = ["close", "describe", "focus", "list_windows", "maximize", "minimize",
           "resolve_window", "restore"]
