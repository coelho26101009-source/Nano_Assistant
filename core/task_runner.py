"""Chained tasks: run a list of steps, check each one, recover from hiccups.

WHY THIS EXISTS
---------------
"Prepara o meu setup de estudo" is five or six PC actions in a row. Left to a
model, a chain like that fails in boring ways: it stops after step three, it
says "done" for a step it never called, it snaps a window that has not
appeared yet, or it gives up on a launch that only needed another second.

This module is the one place that runs a list of steps for NANO, whoever wrote
the list: a plan the model proposed (plugins/task_plan.py), a routine the
person saved (core/protocols.py), or a work mode (core/work_modes.py). It
does four things a model cannot be trusted to do every time:

* **Checks each step against the real world.** A step is "feito" only when the
  executor reported a verified success. After opening an app, NANO waits for
  its window to appear (so the next step can place it) and says so honestly
  when it did not see one. After setting the volume, the level the device
  reports back must be the level asked for.
* **Retries what is worth retrying, once or twice.** A launch that timed out,
  or a window that is "not found" a moment after its app was started, is
  tried again after a short pause. A refusal, an ambiguous target or bad
  arguments are never retried: those need the person, not patience.
* **Skips what cannot work any more.** When opening an app failed, the later
  steps that act on that app's window are marked "dependia de …" instead of
  failing one by one with confusing messages.
* **Stops cleanly.** The emergency stop (or a decline that cancels the whole
  task) stops the remaining steps; they are reported, not silently dropped.

HOW IT STAYS SAFE
-----------------
Only steps that are safe to do twice are ever retried (``RETRYABLE_TOOLS``,
and never a tool the executor marks NOT_SAFE_TO_RETRY).

This module touches nothing. Every step is handed to ``call`` -- in the Brain
that is ``Brain._run_tool`` (guardrails, policy, permission, the per-turn
duplicate ledger and the executor), for protocols and modes it is
``ToolExecutor.execute_tool_async``. A retry is a second call through the
same gate. Reading the window list to verify a launch uses ``probe``, which
must be a read-only tool (``pc_window_list``).
"""
from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from typing import Any, Awaitable, Callable

from core.i18n import tr

logger = logging.getLogger("nano.task_runner")

Call = Callable[[str, dict], Awaitable[Any]]
Status = Callable[[str], None]

#: How long NANO waits for a just-launched app's window before moving on.
LAUNCH_WAIT_SECONDS = 8.0
#: How often the window list is read while waiting.
POLL_SECONDS = 0.5
#: Pause before a retry (seconds), multiplied by the attempt number.
RETRY_PAUSE_SECONDS = 1.0
#: Error codes that say "try again in a moment", never "ask the person".
TRANSIENT_CODES = frozenset({
    "timeout", "launch_failed", "tool_exception", "tool_failed", "tool_cancelled",
    "audio_failed", "audio_unavailable", "busy", "failed", "temporarily_unavailable",
})
#: Error codes that must never be retried: the person or the policy said no,
#: or the target is wrong. Retrying would only ask the same question again.
FINAL_CODES = frozenset({
    "permission_denied", "denied", "user_denied", "cancelled", "ambiguous",
    "invalid_arguments", "invalid_tool_arguments", "unsupported_platform",
    "unsupported", "blocked", "protected_path", "refused", "emergency_stop",
})
#: Tools that act on a window or a running app: they can need a moment after
#: a launch, and they make no sense when that launch failed.
WINDOW_TOOLS = frozenset({
    "pc_app_switch", "pc_window_focus", "pc_window_minimize", "pc_window_maximize",
    "pc_window_restore", "pc_window_close", "pc_window_move", "pc_window_resize",
    "pc_window_center", "pc_window_snap", "pc_window_move_monitor",
    "pc_window_set_topmost", "pc_window_batch_state", "pc_window_batch_close",
    "pc_input_type_text", "pc_input_press_key", "pc_input_hotkey", "pc_pointer_scroll",
})
#: The only tools a step is ever retried for: doing them twice leaves the PC
#: exactly as doing them once (opening an app that is already open just brings
#: it forward; a window snapped twice is still snapped; the volume set twice is
#: still 30). Toggles (play/pause), typing, reminders, closing, opening a site
#: or a folder (a second tab or window) are never retried.
RETRYABLE_TOOLS = frozenset({
    "pc_app_launch", "pc_app_switch", "pc_window_focus", "pc_window_minimize",
    "pc_window_maximize", "pc_window_restore", "pc_window_center", "pc_window_snap",
    "pc_window_move", "pc_window_resize", "pc_window_move_monitor", "pc_window_set_topmost",
    "pc_window_batch_state", "pc_volume_set", "pc_volume_mute", "pc_volume_unmute",
    "pc_display_set_brightness", "pc_settings_open",
})
#: Retries per step, by kind.
MAX_TRANSIENT_RETRIES = 1
MAX_WINDOW_RETRIES = 3


def fold(text: Any) -> str:
    """Lower case, no accents, no spaces: for comparing app and window names."""
    decomposed = unicodedata.normalize("NFD", str(text or "").casefold())
    bare = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    if bare.endswith(".exe"):
        bare = bare[:-4]
    return "".join(ch for ch in bare if ch.isalnum())


def step_target(step: dict) -> str:
    """The app or window a step is about, folded ('' when it has none)."""
    args = step.get("args") or {}
    for key in ("name", "app", "query"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return fold(value)
    return ""


def _same_target(a: str, b: str) -> bool:
    return bool(a and b and (a == b or (len(a) >= 3 and a in b) or (len(b) >= 3 and b in a)))


def _output(result: Any) -> dict:
    if isinstance(result, dict):
        inner = result.get("output")
        if isinstance(inner, dict):
            return inner
    return {}


def succeeded(result: Any) -> bool:
    """A verified success from the executor (or a plugin's ``ok``)."""
    if not isinstance(result, dict):
        return False
    if result.get("success") is True:
        return _output(result).get("ok") is not False
    return result.get("ok") is True and not result.get("cancelled")


def failure_code(result: Any) -> str:
    """The most specific reason a step did not work, as a short code."""
    if not isinstance(result, dict):
        return "failed"
    output = _output(result)
    if output.get("ok") is False and output.get("status"):
        return str(output["status"])
    status = str(result.get("status") or "")
    if status in {"permission_denied", "denied", "user_denied"}:
        return "permission_denied"
    if result.get("cancelled"):
        return "cancelled"
    if status and status not in {"failed", "verification_failed", "error"}:
        return status
    error = str(result.get("error") or "")
    if error and " " not in error and len(error) <= 40:
        return error
    if status == "verification_failed":
        # "Verificação falhou: not_found" -> not_found
        tail = error.rsplit(":", 1)[-1].strip()
        if tail and " " not in tail:
            return tail
    return status or "failed"


def failure_message(result: Any) -> str:
    if not isinstance(result, dict):
        return ""
    output = _output(result)
    for value in (output.get("message"), result.get("message"), result.get("error")):
        if isinstance(value, str) and value.strip():
            return " ".join(value.split())[:200]
    return ""


def is_denied(code: str) -> bool:
    return code in {"permission_denied", "denied", "user_denied", "cancelled"}


def _retry_budget(step: dict, code: str, launched: set[str],
                  retry_allowed: Callable[[str], bool] | None = None) -> int:
    if code in FINAL_CODES or step.get("tool") not in RETRYABLE_TOOLS:
        return 0
    if retry_allowed is not None and not retry_allowed(str(step.get("tool"))):
        return 0
    if step["tool"] in WINDOW_TOOLS and code in {"not_found", "no_window", "window_not_found"} \
            and any(_same_target(step_target(step), name) for name in launched):
        return MAX_WINDOW_RETRIES
    if code in TRANSIENT_CODES:
        return MAX_TRANSIENT_RETRIES
    return 0


async def _wait_for_window(step: dict, result: Any, probe: Call | None,
                           sleep: Callable[[float], Awaitable[None]]) -> bool | None:
    """Whether a window of the app just launched is on screen.

    True: seen. False: not seen within LAUNCH_WAIT_SECONDS. None: NANO cannot
    tell (no probe, not Windows, the listing failed) -- never reported as a
    failure, only not claimed as verified.
    """
    output = _output(result)
    if output.get("status") == "already_running":
        return True
    if probe is None:
        return None
    app = output.get("app") if isinstance(output.get("app"), dict) else {}
    names = {fold(app.get("name")), step_target(step)}
    executable = str(app.get("executable") or "")
    if executable:
        names.add(fold(executable.replace("\\", "/").rsplit("/", 1)[-1]))
    names.discard("")
    pid = output.get("pid")
    deadline = time.monotonic() + LAUNCH_WAIT_SECONDS
    while True:
        try:
            listing = await probe("pc_window_list", {})
        except Exception:
            logger.debug("window listing failed while verifying a launch", exc_info=True)
            return None
        if not succeeded(listing):
            return None
        windows = _output(listing).get("windows")
        if not isinstance(windows, list):
            return None
        for window in windows:
            if not isinstance(window, dict):
                continue
            if pid and window.get("pid") == pid:
                return True
            process = fold(window.get("process"))
            title = fold(window.get("title"))
            if any(name and (_same_target(name, process) or (len(name) >= 3 and name in title))
                   for name in names):
                return True
        if time.monotonic() >= deadline:
            return False
        await sleep(POLL_SECONDS)


def _check_effect(step: dict, result: Any) -> str | None:
    """A reason the reported effect is not the one asked for, or None."""
    if step["tool"] == "pc_volume_set":
        wanted = (step.get("args") or {}).get("level")
        got = _output(result).get("level")
        if isinstance(wanted, (int, float)) and isinstance(got, (int, float)) and abs(got - wanted) > 2:
            return tr("O volume ficou em {got}% em vez de {wanted}%.", got=int(got), wanted=int(wanted))
    if step["tool"] == "pc_display_set_brightness":
        wanted = (step.get("args") or {}).get("level")
        got = _output(result).get("level")
        if isinstance(wanted, (int, float)) and isinstance(got, (int, float)) and abs(got - wanted) > 5:
            return tr("O brilho ficou em {got}% em vez de {wanted}%.", got=int(got), wanted=int(wanted))
    return None


async def run_steps(
    steps: list[dict],
    call: Call,
    *,
    probe: Call | None = None,
    status: Status | None = None,
    should_stop: Callable[[], bool] | None = None,
    retry_allowed: Callable[[str], bool] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict:
    """Run ``steps`` ({tool, args, label[, skip]}) in order. Never raises.

    Returns ``{"ok", "done", "failed", "skipped", "cancelled", "total",
    "steps": [outcome...]}``; each outcome has ``label``, ``tool``, ``ok``,
    ``status`` (ok / skipped / denied / failed / cancelled), ``code``,
    ``message``, ``attempts`` and ``verified`` (True / False / None).
    """
    outcomes: list[dict] = []
    launched: set[str] = set()        # apps opened by this run (folded names)
    failed_targets: set[str] = set()  # apps whose launch failed
    stopped = False
    total = len(steps)

    def tell(text: str) -> None:
        if status is not None:
            try:
                status(text)
            except Exception:
                logger.debug("status listener failed", exc_info=True)

    for index, step in enumerate(steps, start=1):
        label = str(step.get("label") or step.get("tool") or "")
        base = {"index": index, "label": label, "tool": step.get("tool"), "attempts": 0,
                "verified": None}
        if stopped or (should_stop is not None and should_stop()):
            stopped = True
            outcomes.append({**base, "ok": False, "status": "cancelled", "code": "stopped",
                             "message": tr("Parei antes deste passo.")})
            continue
        if step.get("skip"):
            outcomes.append({**base, "ok": True, "status": "skipped", "code": "skipped",
                             "message": str(step.get("skip"))})
            continue
        target = step_target(step)
        if step.get("tool") in WINDOW_TOOLS and any(_same_target(target, bad) for bad in failed_targets):
            outcomes.append({**base, "ok": False, "status": "skipped", "code": "dependency_failed",
                             "message": tr("Dependia de abrir “{app}”, que falhou.",
                                           app=(step.get("args") or {}).get("name")
                                           or (step.get("args") or {}).get("app")
                                           or (step.get("args") or {}).get("query") or "")})
            continue

        tell(f"⚙️ {index}/{total} · {label}")
        attempts = 0
        result: Any = None
        while True:
            attempts += 1
            try:
                result = await call(str(step["tool"]), dict(step.get("args") or {}))
            except Exception:
                logger.exception("Step %s (%s) raised", index, step.get("tool"))
                result = {"success": False, "status": "failed", "error": "tool_exception"}
            if succeeded(result):
                break
            code = failure_code(result)
            if attempts > _retry_budget(step, code, launched, retry_allowed):
                break
            if should_stop is not None and should_stop():
                break
            logger.info("Step %s (%s) failed with %s; retrying (%d)", index, step["tool"], code, attempts)
            tell(f"⚙️ {index}/{total} · {label} · " + tr("a tentar outra vez"))
            await sleep(RETRY_PAUSE_SECONDS * attempts)

        outcome = {**base, "attempts": attempts}
        if succeeded(result):
            mismatch = _check_effect(step, result)
            if mismatch:
                outcome.update({"ok": False, "status": "failed", "code": "verification",
                                "message": mismatch, "verified": False})
            else:
                output = _output(result)
                outcome.update({"ok": True, "status": "ok", "code": str(output.get("status") or "ok"),
                                "message": str(output.get("message") or "")[:200]})
                if step.get("tool") == "pc_app_launch":
                    if target:
                        launched.add(target)
                    # Wait for the window only when a later step needs it
                    # (placing it, focusing it): otherwise the launch result,
                    # which already carries the real process id, is enough.
                    later = steps[index:]
                    if any(s.get("tool") in WINDOW_TOOLS and _same_target(step_target(s), target)
                           for s in later):
                        seen = await _wait_for_window(step, result, probe, sleep)
                        outcome["verified"] = seen
                        if seen is False:
                            outcome["message"] = tr("Abri, mas ainda não vi a janela.")
                    else:
                        outcome["verified"] = None
                else:
                    outcome["verified"] = True if isinstance(result, dict) and result.get("success") else None
        else:
            code = failure_code(result)
            denied = is_denied(code)
            outcome.update({"ok": False, "status": "denied" if denied else "failed", "code": code,
                            "message": failure_message(result)})
            if step.get("tool") == "pc_app_launch" and target:
                failed_targets.add(target)
            if code == "emergency_stop" or (should_stop is not None and should_stop()):
                stopped = True
        outcomes.append(outcome)

    done = sum(1 for o in outcomes if o["status"] == "ok")
    skipped = sum(1 for o in outcomes if o["status"] == "skipped" and o["ok"])
    failed = sum(1 for o in outcomes if not o["ok"])
    cancelled = sum(1 for o in outcomes if o["status"] == "cancelled")
    return {"ok": failed == 0, "done": done, "failed": failed, "skipped": skipped,
            "cancelled": cancelled, "total": total, "steps": outcomes}


def short_report(run: dict, *, max_items: int = 4) -> str:
    """One or two plain sentences on what did NOT work, for the answer and voice.

    Empty when everything worked: a short answer is the point.
    """
    bad = [o for o in run.get("steps") or [] if not o.get("ok")]
    if not bad:
        return ""
    parts = []
    for outcome in bad[:max_items]:
        why = outcome.get("message") or ""
        if outcome.get("status") == "denied":
            why = tr("recusado")
        elif outcome.get("status") == "cancelled":
            why = tr("não cheguei lá")
        parts.append(f"{outcome['label']} ({why})" if why else str(outcome["label"]))
    more = len(bad) - len(parts)
    text = tr("Não fiz: {steps}.", steps="; ".join(parts))
    if more > 0:
        text = text[:-1] + tr(" e mais {count}.", count=more)
    return text


__all__ = ["FINAL_CODES", "LAUNCH_WAIT_SECONDS", "RETRYABLE_TOOLS", "TRANSIENT_CODES", "WINDOW_TOOLS",
           "failure_code", "fold", "run_steps", "short_report", "step_target", "succeeded"]
