"""Locking, sleeping, restarting, shutting down, signing out.

THE FIVE MOST CONSEQUENTIAL THINGS NANO CAN DO, AND THE RULES THAT APPLY.

* **Nothing here is forced.** `ExitWindowsEx` is called without `EWX_FORCE`, so
  an application with unsaved work can veto the shutdown and put its own dialog
  in front of the user. Forcing would be a data-loss primitive and PC Control
  does not own one.
* **Nothing here is scheduled.** There is no countdown, no delay, no "in five
  minutes". The action happens immediately after the user approves it, or it
  does not happen. A pending shutdown the user has forgotten about is exactly
  the kind of surprise this design refuses.
* **Nothing here closes applications first.** Windows asks them; Nano does not
  go around shutting things down to smooth the path.
* **Every one of them is confirmed against a named action.** The policy layer
  gates these on `pc.session.*` / `pc.power.*`, and restart, shutdown and sign
  out are registered as CRITICAL capabilities, which means a task-wide grant
  cannot cover them -- only an explicit, single-use approval can.

The `_ACTIONS` table is the whole surface. There is no argument that reaches
Windows other than the constant this table holds.
"""
from __future__ import annotations

import logging

from core.pc_control import winapi
from core.pc_control.results import PCControlError

logger = logging.getLogger("nano.pc_control.power")

#: action -> (human label, what the confirmation card should say)
ACTIONS: dict[str, tuple[str, str]] = {
    "lock":     ("bloquear a sessão", "BLOQUEAR SESSÃO"),
    "sleep":    ("suspender o computador", "SUSPENDER O COMPUTADOR"),
    "restart":  ("reiniciar o computador", "REINICIAR O COMPUTADOR"),
    "shutdown": ("desligar o computador", "DESLIGAR O COMPUTADOR"),
    "logoff":   ("terminar a sessão", "TERMINAR SESSÃO DO WINDOWS"),
}


def describe(action) -> tuple[str, str, str]:
    key = str(action or "").strip().lower()
    if key not in ACTIONS:
        raise PCControlError("invalid_input",
                             f"'{action}' não é uma acção de energia conhecida.",
                             allowed=sorted(ACTIONS))
    label, headline = ACTIONS[key]
    return key, label, headline


def _require_windows() -> None:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Estas acções só funcionam no Windows.")


def lock() -> dict:
    _require_windows()
    if not winapi.lock_workstation():
        raise PCControlError("failed", "O Windows não bloqueou a sessão.")
    return {"action": "lock", "requested": True}


def sleep() -> dict:
    _require_windows()
    if not winapi.suspend_system():
        raise PCControlError(
            "failed",
            "O Windows não suspendeu o computador. Pode estar desativado nas "
            "definições de energia.")
    return {"action": "sleep", "requested": True}


def _exit_windows(action: str, flags: int) -> dict:
    """Ask Windows to end the session, and report what it answered.

    ``requested`` is deliberately not called ``done``. A restart is not
    something this process lives to observe, and an application may still veto
    it a second later. The honest claim is that Windows accepted the request.
    """
    _require_windows()
    if not winapi.exit_windows(flags):
        raise PCControlError(
            "failed",
            "O Windows recusou o pedido. Pode haver uma aplicação a impedi-lo.")
    return {"action": action, "requested": True,
            "note": ("O Windows aceitou o pedido. Uma aplicação com trabalho por "
                     "guardar ainda o pode cancelar.")}


def restart() -> dict:
    return _exit_windows("restart", winapi.EWX_REBOOT)


def shutdown() -> dict:
    return _exit_windows("shutdown", winapi.EWX_SHUTDOWN | winapi.EWX_POWEROFF)


def logoff() -> dict:
    return _exit_windows("logoff", winapi.EWX_LOGOFF)


#: action -> the function that performs it. Looked up, never composed.
HANDLERS = {
    "lock": lock,
    "sleep": sleep,
    "restart": restart,
    "shutdown": shutdown,
    "logoff": logoff,
}


def perform(action) -> dict:
    key, label, _headline = describe(action)
    result = HANDLERS[key]()
    result["label"] = label
    return result


__all__ = ["ACTIONS", "HANDLERS", "describe", "lock", "logoff", "perform",
           "restart", "shutdown", "sleep"]
