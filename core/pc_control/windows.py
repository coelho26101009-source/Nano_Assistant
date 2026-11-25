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
import os
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
#:
#: 4.0s, NOT the 1.5s this started at. A plain Win32 window on an ordinary
#: desktop disappears in well under 100ms — measured here, repeatedly, both
#: idle and under deliberate CPU load, and CPU load made no measurable
#: difference. A PACKAGED (UWP) window is a different shape of problem: Windows
#: hosts it as two processes, `ApplicationFrameHost.exe` owning the visible
#: frame this module targets and a separate `*.exe` owning the real
#: `Windows.UI.Core.CoreWindow`, and WM_CLOSE on the frame has to cross that
#: process boundary and tear down the XAML/DirectComposition surfaces before
#: the frame itself goes away. That handshake is normally fast too, but it
#: depends on GPU-accelerated compositing, and CI runs on virtualised hosts
#: with no hardware GPU, where DirectComposition falls back to a software
#: rasterizer that is well documented to be markedly slower for exactly this
#: kind of teardown. Two Windows CI runs failed at this exact step, both
#: recovered on rerun with no code change, and every step before close --
#: including minimize and restore, which exercise the very same handle --
#: passed every time: that pattern is a slow, genuine close racing a
#: fixed-deadline verification, not a broken close. Calculator is the only
#: packaged app this suite drives, so it is the only place this shows up, but
#: the fix is not about Calculator: it is headroom for any window whose close
#: has to cross a process boundary before the handle dies. See
#: `tests/test_pc_control_close_verification.py` for the mechanism, proved
#: without needing a real window.
_CLOSE_OBSERVE_SECONDS = 4.0
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
        if not winapi.IS_WINDOWS:
            # The query branch below reaches winapi only through
            # list_windows(), which already guards itself -- a test can
            # exercise the ambiguity logic cross-platform by faking
            # list_windows without touching real Win32 at all. This branch is
            # different: it calls winapi.is_window() directly, so nothing
            # upstream of it declares a platform check. Without this, a
            # malformed-window_id call on a non-Windows host hit
            # winapi.is_window() raw, which raises WindowsUnavailable rather
            # than a PCControlError -- plugins/pc_control.py's guard has no
            # declared status for that, so it fell through to a generic
            # "internal_error" instead of the same intentional
            # "unsupported_platform" every other window/monitor/audio tool
            # already reports off Windows.
            raise PCControlError("unsupported_platform", "O controlo de janelas só funciona no Windows.")
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

    RECYCLED HANDLES DURING THE POLL. HWND values are reclaimed by Windows
    once a window is destroyed, and the poll below runs for several seconds --
    long enough that some other window, on an active desktop, could be created
    and land on the very integer this one used. ``IsWindow`` only asks "does
    something answer to this handle right now", so on its own it cannot tell
    that apart from our window still being open. The owning process id is
    captured before the close is requested and checked again on every poll: a
    handle that answers ``True`` but now belongs to a different process is not
    the window we asked to close -- ours is gone, and that counts as closed.
    """
    title = clamp_text(winapi.window_title(hwnd), 200)
    owner_pid = _safe_window_pid(hwnd)
    if not winapi.post_close(hwnd):
        raise PCControlError("close_failed", f"Não foi possível pedir o fecho de '{title}'.")

    deadline = time.monotonic() + _CLOSE_OBSERVE_SECONDS
    while time.monotonic() < deadline:
        if not _still_our_window(hwnd, owner_pid):
            return {"closed": True, "title": title}
        time.sleep(_CLOSE_POLL_SECONDS)

    return {
        "closed": False,
        "title": title,
        "detail": ("A aplicação não fechou. Pode estar a perguntar se queres guardar "
                   "o trabalho — o Nano não força o fecho."),
    }


def _safe_window_pid(hwnd: int) -> int:
    """``winapi.window_pid``, defensively, for the ONE call that has no
    ``except`` of its own to fall into: the owner captured before the close
    request is even sent. ``0`` means "could not be read", and it is a safe
    sentinel here specifically because ``_still_our_window`` treats a falsy
    ``owner_pid`` as "no identity to check this whole close, trust ``IsWindow``
    alone" -- exactly the pre-hardening behaviour, and exactly what a failed
    read at this point should degrade to.

    THIS IS NOT REUSED FOR THE IN-POLL RE-CHECK, AND THAT IS DELIBERATE. A
    poll-time read failure needs a DIFFERENT fallback: "unknown this instant,
    keep trusting IsWindow for this poll" -- not "treat as pid 0", which would
    then compare unequal to a real ``owner_pid`` and read as a mismatch,
    misfiring the recycled-handle branch on an ordinary transient failure. An
    earlier version of this function tried to serve both call sites and
    produced exactly that bug: a failed re-check was indistinguishable from a
    genuinely different owner, so `_still_our_window` reported the window
    closed after one flaky read instead of continuing to poll. Two call
    sites, two correct fallbacks -- see ``_still_our_window`` below for the
    other one.

    Reading the owning process can fail for reasons that have nothing to do
    with the window itself -- off Windows, ``winapi._user32()`` reaches for
    ``ctypes.windll``, which does not exist there. That is not hypothetical:
    ``tests/test_pc_control_v2.py`` deliberately drives this exact function
    on Linux, with ``winapi.IS_WINDOWS`` forced ``True`` and only
    ``post_close`` / ``is_window`` / ``window_title`` mocked, precisely to
    prove PC Control never reaches a process-termination primitive. A PID
    lookup that raises must not block that request from being made, or from
    being verified by the handle check alone.
    """
    try:
        return winapi.window_pid(hwnd)
    except Exception:
        logger.debug("could not read window owner before requesting close", exc_info=True)
        return 0


def _still_our_window(hwnd: int, owner_pid: int) -> bool:
    """Whether ``hwnd`` is still a live handle to the SAME window we closed.

    ``False`` means "treat as closed", for two different reasons: the handle
    is dead (the ordinary case), or it is alive but answers for a different
    process now (a recycled integer -- see ``close`` above). If the owning
    process cannot be read here -- or was never readable, see
    ``_safe_window_pid`` -- the handle check alone decides for THIS poll; a
    read failure is evidence of nothing, and must not be read as a mismatch.
    That is the original, simpler behaviour, and it is the safe fallback
    because a false ``True`` here only costs one more poll interval, not a
    wrong verdict.
    """
    if not winapi.is_window(hwnd):
        return False
    if owner_pid:
        try:
            if winapi.window_pid(hwnd) != owner_pid:
                return False
        except Exception:
            logger.debug("could not re-check window owner during close poll", exc_info=True)
    return True


# ==========================================================================
#  PC CONTROL V2
# ==========================================================================

#: Bound on a batch: how many windows one command may act on at once.
MAX_BATCH_WINDOWS = 20

# --------------------------------------------------------------------------
#  Windows that must never receive synthetic keyboard input
#
#  THIS IS WHAT STOPS "open a terminal, then type into it" FROM BEING A SHELL.
#
#  PC Control's premise is that there is no general command line. Launching a
#  terminal is a legitimate request -- it is an application the user installed
#  -- but sending keystrokes to one would compose two allowed actions into
#  arbitrary command execution, with nothing but the user's careful reading of
#  a confirmation card in between. So the composition is broken structurally:
#  the input tools refuse a target that is a console, whatever the user or the
#  model asks for, and say why.
#
#  Nano's own windows are refused for the same class of reason: typing into the
#  assistant's own chat box is a loop, and the approval dialog itself is a Nano
#  window that briefly holds the foreground.
# --------------------------------------------------------------------------

_TERMINAL_PROCESSES = frozenset({
    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
    "windowsterminal.exe", "wt.exe", "openconsole.exe", "bash.exe",
    "sh.exe", "mintty.exe", "wsl.exe", "wslhost.exe", "putty.exe",
    "python.exe", "pythonw.exe", "node.exe", "cmder.exe", "alacritty.exe",
    "wezterm-gui.exe", "hyper.exe", "conemu64.exe", "conemu.exe",
    "powershell_ise.exe",
})

_TERMINAL_CLASSES = frozenset({
    "consolewindowclass",
    "cascadia_hosting_window_class",
    "pseudoconsolewindow",
    "mintty",
    "putty",
})


def is_terminal_window(window: dict) -> bool:
    """Whether a window is a console, by process name or by window class."""
    process = str(window.get("process") or "").casefold()
    if process in _TERMINAL_PROCESSES:
        return True
    try:
        window_class = winapi.window_class(int(window["window_id"])).strip().casefold()
    except Exception:
        return False
    return window_class in _TERMINAL_CLASSES


def _nano_pids() -> set[int]:
    """Nano's own process and the desktop shell that spawned it.

    The backend runs as a child of the Electron main process, so the parent and
    the parent's other children are all "Nano" as far as the user is concerned.
    Read live rather than cached: the shell can restart the backend.
    """
    pids = {os.getpid()}
    try:
        parent = os.getppid()
    except (AttributeError, OSError):
        return pids
    if not parent:
        return pids
    pids.add(parent)
    try:
        import psutil

        for child in psutil.Process(parent).children(recursive=True):
            pids.add(child.pid)
    except Exception:
        logger.debug("could not enumerate Nano's own processes", exc_info=True)
    return pids


def is_nano_window(window: dict) -> bool:
    try:
        pid = winapi.window_pid(int(window["window_id"]))
    except Exception:
        return False
    return pid in _nano_pids()


def resolve_input_target(*, window_id: int | None = None,
                         query: str | None = None) -> dict:
    """The window a keyboard action may be aimed at, or a refusal.

    STRICT resolution (`allow_partial=False`): a loose title match is not good
    enough to send somebody's keystrokes at, for the same reason it is not good
    enough to close a window on. Consoles and Nano's own windows are refused
    outright -- not gated, refused.
    """
    target = resolve_window(window_id=window_id, query=query, allow_partial=False)
    if is_terminal_window(target):
        raise PCControlError(
            "blocked",
            f"'{target['title']}' é uma consola. O Nano nunca escreve numa "
            "linha de comandos — não existe execução de comandos no Nano.",
            window=target)
    if is_nano_window(target):
        raise PCControlError(
            "blocked",
            "Essa é uma janela do próprio Nano; o Nano não escreve em si mesmo.",
            window=target)
    return target


def resolve_group(query: str, *, limit: int = MAX_BATCH_WINDOWS) -> list[dict]:
    """Every window belonging to one application, for a batch action.

    Matching is by PROCESS NAME or by exact title -- never by a loose substring
    of the title. "Fecha todas as janelas do Discord" must mean the Discord
    process, not every window with the word "discord" somewhere in its caption,
    because a browser tab or a chat message can put that word anywhere.
    """
    text = str(query or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar a aplicação.")

    needle = text.casefold()
    candidates = list_windows()
    by_process = [w for w in candidates
                  if (w["process"] or "").casefold() in {needle, f"{needle}.exe"}]
    by_title = [w for w in candidates if w["title"].casefold() == needle]
    matched = by_process or by_title
    if not matched:
        raise PCControlError(
            "not_found",
            f"Não encontrei nenhuma janela de '{text}'.",
            candidates=[{"title": w["title"], "process": w["process"]}
                        for w in candidates[:10]])
    if len(matched) > limit:
        raise PCControlError(
            "invalid_input",
            f"São {len(matched)} janelas, mais do que as {limit} que o Nano trata "
            "de uma vez.",
            count=len(matched))
    return matched


def summarise(group: list[dict]) -> dict:
    """A bounded, human-readable description of a batch, for confirmation."""
    return {
        "count": len(group),
        "processes": sorted({w["process"] for w in group if w["process"]}),
        "titles": [w["title"] for w in group[:MAX_BATCH_WINDOWS]],
    }


__all__ = ["MAX_BATCH_WINDOWS", "close", "describe", "focus", "is_nano_window",
           "is_terminal_window", "list_windows", "maximize", "minimize",
           "resolve_group", "resolve_input_target", "resolve_window", "restore",
           "summarise"]
