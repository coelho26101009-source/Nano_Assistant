"""The Agenda page: reminders and calendar events, for the UI.

The reminders and calendar plugins own the data and the date handling. This
module is the narrow surface the Agenda page talks to: it validates what the
user typed, calls the RUNNING plugin instances (see plugin_loader.get_plugin),
and returns small, uniform results ({"ok": bool, ...}) the page can render.

These are the user's own actions on Nano's own data -- like editing a memory on
the Memória page -- not operating-system effects, so they do not go through the
model tool pipeline. Nothing here is a model tool.
"""
from __future__ import annotations

from core import i18n
from core.i18n import tr

import logging
from datetime import datetime
from typing import Any

from core.plugin_loader import get_plugin

logger = logging.getLogger("nano.agenda")

MAX_TEXT = 300
MAX_TITLE = 200
MAX_LOCATION = 200
MAX_WHEN = 64
MAX_DAYS = 366


def _plugin(name: str) -> Any | None:
    return get_plugin(name)


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _as_id(value: Any) -> int | None:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _say(plugin_message: Any, fallback: str) -> str:
    """The plugin's own sentence in Portuguese; in another language, ours.

    The reminders and calendar plugins answer the model in Portuguese, with
    the exact time in the sentence. The page shows the time in the list
    anyway, so a translated generic sentence loses nothing.
    """
    if i18n.current() == "pt-PT" and plugin_message:
        return str(plugin_message)
    return tr(fallback)


def _failure(error: str, message: str) -> dict:
    return {"ok": False, "error": error, "message": message}


def overview(days: int = 30) -> dict:
    """Pending reminders and the events of the next ``days`` days."""
    try:
        window = max(1, min(int(days), MAX_DAYS))
    except (TypeError, ValueError):
        window = 30
    reminders_plugin = _plugin("reminders")
    calendar_plugin = _plugin("calendar")
    result: dict = {
        "ok": True,
        "windowDays": window,
        "available": {"reminders": reminders_plugin is not None, "calendar": calendar_plugin is not None},
        "reminders": [],
        "events": [],
    }
    if reminders_plugin is not None:
        try:
            result["reminders"] = list(reminders_plugin.list_reminders().get("reminders", []))
        except Exception:
            logger.exception("Agenda: não consegui ler os lembretes")
            result["remindersError"] = tr("Não consegui ler os lembretes.")
    if calendar_plugin is not None:
        try:
            listed = calendar_plugin.list_events(days=window)
            result["events"] = list(listed.get("events", []))
            if listed.get("google_error"):
                result["googleError"] = str(listed["google_error"])[:300]
        except Exception:
            logger.exception("Agenda: não consegui ler os eventos")
            result["eventsError"] = tr("Não consegui ler os eventos.")
    return result


def create_reminder(text: Any, when: Any, repeat: Any = None) -> dict:
    reminders_plugin = _plugin("reminders")
    if reminders_plugin is None:
        return _failure("reminders_unavailable", tr("Os lembretes estão desligados."))
    text = _clean(text, MAX_TEXT)
    when = _clean(when, MAX_WHEN)
    if not text:
        return _failure("empty_text", tr("Escreve do que te devo lembrar."))
    if not when:
        return _failure("empty_when", tr("Indica quando, por exemplo '10 minutos' ou '18:30'."))
    repeat = _clean(repeat, 32) or None
    if repeat is not None and repeat not in getattr(reminders_plugin, "RECURRENCES", {}):
        return _failure("invalid_repeat", tr("Repetição inválida."))
    created = reminders_plugin.set_reminder(text, when, repeat)
    if created.get("success"):
        return {"ok": True, "id": created.get("id"), "dueAt": created.get("due_at"),
                "recurrence": created.get("recurrence"),
                "message": _say(created.get("message"), "Lembrete criado.")}
    return _failure("invalid_when", str(created.get("error") or tr("Não consegui criar o lembrete.")))


def delete_reminder(reminder_id: Any) -> dict:
    reminders_plugin = _plugin("reminders")
    if reminders_plugin is None:
        return _failure("reminders_unavailable", tr("Os lembretes estão desligados."))
    number = _as_id(reminder_id)
    if number is None:
        return _failure("invalid_id", tr("Lembrete inválido."))
    result = reminders_plugin.cancel_reminder(number)
    return {"ok": bool(result.get("success")), "message": result.get("message", "")}


def _parse_local(value: str) -> datetime | None:
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def create_event(title: Any, start: Any, end: Any = None, location: Any = "") -> dict:
    calendar_plugin = _plugin("calendar")
    if calendar_plugin is None:
        return _failure("calendar_unavailable", tr("O calendário está desligado."))
    title = _clean(title, MAX_TITLE)
    start = _clean(start, MAX_WHEN)
    end = _clean(end, MAX_WHEN) or None
    location = _clean(location, MAX_LOCATION)
    if not title:
        return _failure("empty_title", tr("Dá um título ao evento."))
    start_dt = _parse_local(start)
    if start_dt is None:
        return _failure("invalid_start", tr("Indica quando começa."))
    if end is not None:
        end_dt = _parse_local(end)
        if end_dt is None:
            return _failure("invalid_end", tr("A hora de fim não é válida."))
        if end_dt < start_dt:
            return _failure("end_before_start", tr("O evento não pode acabar antes de começar."))
    created = calendar_plugin.add_event(title, start, end, location=location)
    if created.get("success"):
        return {"ok": True, "id": created.get("id"), "message": _say(created.get("message"), "Evento criado.")}
    return _failure("invalid_event", str(created.get("error") or tr("Não consegui criar o evento.")))


def delete_event(event_id: Any) -> dict:
    calendar_plugin = _plugin("calendar")
    if calendar_plugin is None:
        return _failure("calendar_unavailable", tr("O calendário está desligado."))
    number = _as_id(event_id)
    if number is None:
        return _failure("invalid_id", tr("Evento inválido."))
    result = calendar_plugin.delete_event(number)
    return {"ok": bool(result.get("success")), "message": result.get("message", "")}
