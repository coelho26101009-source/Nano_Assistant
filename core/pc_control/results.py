"""The result contract every PC-control tool returns, and the bounds on it.

TWO RULES LIVE HERE, AND THEY ARE THE POINT OF THE MODULE.

1. NOTHING IS TRUE BECAUSE THE MODEL SAID SO.

   A PC action reports what the operating system actually did. "Spotify
   aberto." is a sentence Nano may only say after a real launch returned a
   real process. Every constructor below forces a ``status`` that came from an
   observed outcome, and ``fail()`` always carries an ``error`` code -- which
   is what makes ToolExecutor's verification step treat it as a failure rather
   than wrapping it as a success.

2. NOTHING CROSSES THE EXECUTION BOUNDARY UNBOUNDED.

   Window lists, file-search hits and system snapshots all grow with the
   machine, not with the request. An unbounded result is both a context-window
   problem and a way to smuggle a wall of text at the model. Everything is
   clamped here, once, before it leaves.
"""
from __future__ import annotations

import json
from typing import Any

#: Mirrors core.task_engine.MAX_RESULT_BYTES. A PC tool result must fit in the
#: task store without being truncated there, so it is clamped here first, where
#: the truncation can be structured and honest instead of a cut JSON string.
MAX_RESULT_BYTES = 128 * 1024

#: Hard ceilings on collection-shaped output. These are deliberately small: the
#: model needs enough to act, never the whole machine.
MAX_WINDOWS = 60
MAX_APP_CANDIDATES = 12
MAX_FILE_RESULTS = 100
MAX_STRING_CHARS = 512
MAX_DEPTH = 8


class PCControlError(Exception):
    """A PC action failed for a reason the user should be told about.

    Carries a stable machine code plus a Portuguese message. The code is what
    tests and the UI branch on; the message is what a human reads. Neither is
    ever a stack trace -- internal detail belongs in the log.
    """

    def __init__(self, status: str, message: str, **details: Any):
        self.status = status
        self.message = message
        self.details = details
        super().__init__(f"{status}: {message}")


def clamp_text(value: Any, limit: int = MAX_STRING_CHARS) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _clamp_structure(value: Any, depth: int = 0, dropped: list | None = None) -> Any:
    """Recursively bound strings, collections and nesting depth.

    ``dropped`` collects a marker whenever something was actually removed, so
    the caller can SAY the result was trimmed. A silently shortened list is the
    worst possible output here: a reader who cannot tell a complete answer from
    a partial one will act on the partial one believing it is complete.
    """
    if depth >= MAX_DEPTH:
        if dropped is not None:
            dropped.append("depth")
        return "…"
    if isinstance(value, str):
        return clamp_text(value)
    if isinstance(value, dict):
        items = list(value.items())
        if len(items) > 64 and dropped is not None:
            dropped.append("dict")
        return {str(k)[:64]: _clamp_structure(v, depth + 1, dropped) for k, v in items[:64]}
    if isinstance(value, (list, tuple)):
        items = list(value)
        if len(items) > MAX_FILE_RESULTS and dropped is not None:
            dropped.append("list")
        return [_clamp_structure(v, depth + 1, dropped) for v in items[:MAX_FILE_RESULTS]]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return clamp_text(value)


def _enforce_byte_budget(payload: dict) -> dict:
    """Last-resort clamp: drop list items until the JSON fits the budget.

    Per-collection caps are the primary defence; this catches the case where
    the items themselves are large. It reports the truncation in the payload
    rather than silently returning a shorter list, because a caller that
    cannot tell a complete answer from a trimmed one will act on the wrong one.
    """
    try:
        encoded = json.dumps(payload, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"ok": False, "status": "internal_error",
                "error": "result_not_serialisable",
                "message": "O resultado da ferramenta não pôde ser serializado."}
    if len(encoded.encode("utf-8")) <= MAX_RESULT_BYTES:
        return payload

    for key, value in list(payload.items()):
        if isinstance(value, list) and value:
            keep = max(1, len(value) // 2)
            while len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) > MAX_RESULT_BYTES and keep >= 1:
                payload[key] = value[:keep]
                payload["truncated"] = True
                payload["truncated_reason"] = "result_size_limit"
                if keep == 1:
                    break
                keep //= 2
    return payload


def ok(status: str, message: str, **fields: Any) -> dict:
    """A successful, OBSERVED outcome.

    ``status`` is not decoration: "launched" and "already_running" are
    different facts about the world and the caller is entitled to both.
    """
    payload: dict[str, Any] = {"ok": True, "status": status, "message": message}
    dropped: list[str] = []
    payload.update({k: _clamp_structure(v, 0, dropped) for k, v in fields.items()})
    if dropped:
        payload["truncated"] = True
        payload["truncated_reason"] = "result_bounds"
    return _enforce_byte_budget(payload)


def fail(status: str, message: str, **fields: Any) -> dict:
    """A failure, stated plainly.

    ``error`` is always set to the status code. ToolExecutor._verify_execution
    inspects handler output and treats a truthy ``error`` as a failed
    execution, so this is what stops a refused or impossible action from being
    wrapped as ``success: true`` and reported to the model as if it had worked.
    """
    payload: dict[str, Any] = {"ok": False, "status": status, "error": status, "message": message}
    dropped: list[str] = []
    payload.update({k: _clamp_structure(v, 0, dropped) for k, v in fields.items()})
    if dropped:
        payload["truncated"] = True
        payload["truncated_reason"] = "result_bounds"
    return _enforce_byte_budget(payload)


def from_error(exc: PCControlError) -> dict:
    return fail(exc.status, exc.message, **exc.details)


__all__ = [
    "MAX_APP_CANDIDATES",
    "MAX_DEPTH",
    "MAX_FILE_RESULTS",
    "MAX_RESULT_BYTES",
    "MAX_STRING_CHARS",
    "MAX_WINDOWS",
    "PCControlError",
    "clamp_text",
    "fail",
    "from_error",
    "ok",
]
