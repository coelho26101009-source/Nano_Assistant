"""Turn an AMBIGUOUS PC Control result into a question the user can answer.

PC Control already refuses to guess: `app.launch`, `app.switch` and every
`window.*` tool return status ``ambiguous`` with the candidate list instead of
picking one (see docs/architecture/PC_CONTROL.md, "Ambiguity and voice"). Until
now that list only reached the model, which then asked in prose -- and the user
had to retype a window title exactly.

This module turns the candidate list into a bounded, plain-data question:

    {"question": "Há 2 janelas que correspondem a 'relatório'. Qual delas?",
     "tool": "pc_window_close",
     "options": [{"label": "relatório.docx — Word", "reply": "…"}, …]}

The chat shows each option as a button; pressing it sends ``reply`` as an
ordinary user message, so the choice travels the whole MODEL -> POLICY ->
PERMISSION -> EXECUTION pipeline again, now with an exact target (the
``window_id`` or ``app_id`` the tool itself returned). Nothing here executes
anything, approves anything or skips a confirmation.
"""
from __future__ import annotations

from typing import Any

MAX_OPTIONS = 6
MAX_LABEL = 80
MAX_QUESTION = 200


def _clip(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _ambiguous_payload(result: Any) -> dict | None:
    """Find the tool's own ambiguous payload, wherever the executor put it."""
    if not isinstance(result, dict):
        return None
    for candidate in (result, result.get("output")):
        if isinstance(candidate, dict) and candidate.get("status") == "ambiguous":
            return candidate
    return None


def _option(candidate: Any) -> dict | None:
    if not isinstance(candidate, dict):
        return None
    window_id = candidate.get("window_id")
    if isinstance(window_id, int) and not isinstance(window_id, bool):
        title = _clip(candidate.get("title") or "(sem título)", MAX_LABEL)
        process = _clip(candidate.get("process") or "", 40)
        label = f"{title} — {process}" if process else title
        return {
            "label": _clip(label, MAX_LABEL),
            "reply": f"Refiro-me à janela «{title}»{f' ({process})' if process else ''}, window_id {window_id}.",
        }
    app_id = candidate.get("app_id")
    name = candidate.get("name")
    if isinstance(app_id, str) and app_id and name:
        label = _clip(name, MAX_LABEL)
        return {"label": label, "reply": f"Refiro-me à aplicação «{label}», app_id {_clip(app_id, 120)}."}
    return None


def from_tool_result(tool_name: str, result: Any) -> dict | None:
    """The question to put to the user, or None when there is nothing to ask."""
    payload = _ambiguous_payload(result)
    if payload is None:
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        return None
    options: list[dict] = []
    seen: set[str] = set()
    for candidate in candidates:
        option = _option(candidate)
        if option is None or option["reply"] in seen:
            continue
        seen.add(option["reply"])
        options.append(option)
        if len(options) >= MAX_OPTIONS:
            break
    if len(options) < 2:
        # One candidate is not a choice, and zero is "not found".
        return None
    return {
        "question": _clip(payload.get("message") or "Qual destas?", MAX_QUESTION),
        "tool": _clip(tool_name, 64),
        "options": options,
        "more": max(0, len(candidates) - len(options)),
    }


def for_overlay(clarification: Any) -> dict | None:
    """The smaller shape the voice overlay shows: labels only, no replies."""
    if not isinstance(clarification, dict):
        return None
    labels = [str(o.get("label", "")) for o in clarification.get("options", []) if isinstance(o, dict)]
    labels = [label for label in labels if label][:4]
    if len(labels) < 2:
        return None
    return {"question": _clip(clarification.get("question"), MAX_QUESTION), "options": labels}
