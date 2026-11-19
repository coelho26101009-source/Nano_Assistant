"""NANO speaks first: the morning briefing and the heads-up before an event.

WHAT IT DOES
------------
* **Morning briefing.** Once a day, the first time NANO is running at or after
  the chosen time (08:00 by default), it says good morning and lists today's
  events and reminders, and how many come tomorrow. If the PC was off at
  08:00, it comes when NANO starts, the same day.
* **Event heads-up.** A few minutes before a calendar event starts (15 by
  default), it says so. Reminders already notify at their own time
  (plugins/reminders.py); this covers events, which had no warning at all.

WHAT IT DOES NOT DO
-------------------
It calls no model and nothing leaves the PC: the briefing is built from the
local agenda, in the person's language, by plain code. It performs no action,
so there is nothing to authorise. It only shows a message.

Both are switches in Definições → Geral → "O NANO fala primeiro", on by
default and off in one click. The only state is the date of the last briefing
and the events already announced, in ``proactive_state.json`` in the data
directory, which "Apagar todos os dados" removes.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.i18n import tr

logger = logging.getLogger("nano.proactive")

DEFAULT_BRIEFING_TIME = "08:00"
DEFAULT_LEAD_MINUTES = 15
LEAD_MINUTES_CHOICES = (5, 10, 15, 30, 60)
#: How often the scheduler looks. A heads-up is never more than this late.
TICK_SECONDS = 30.0
#: The most items one briefing lists of each kind; the rest is a count.
MAX_LISTED = 5
#: How long one read of the agenda is reused. The scheduler ticks every 30 s,
#: and with Google Calendar connected every read is a network call; event
#: times do not move, so a two-minute-old copy only delays a brand-new event.
OVERVIEW_TTL_SECONDS = 120.0
#: A briefing is not given after this hour: at 23:00 "bom dia" is noise.
LATEST_BRIEFING_HOUR = 14

_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def valid_time(value: Any) -> bool:
    return isinstance(value, str) and bool(_TIME.match(value))


def _settings() -> dict[str, Any]:
    try:
        from core import user_settings
        get = user_settings.get
    except Exception:  # pragma: no cover - settings always import in the app
        def get(_key, default=None):
            return default
    briefing_time = get("proactive_briefing_time", DEFAULT_BRIEFING_TIME)
    lead = get("proactive_event_lead_minutes", DEFAULT_LEAD_MINUTES)
    return {
        "briefing": get("proactive_briefing", True) is not False,
        "briefing_time": briefing_time if valid_time(briefing_time) else DEFAULT_BRIEFING_TIME,
        "event_alerts": get("proactive_event_alerts", True) is not False,
        "lead_minutes": lead if lead in LEAD_MINUTES_CHOICES else DEFAULT_LEAD_MINUTES,
        "name": str(get("display_name", "") or "").strip(),
    }


def parse_when(value: Any) -> datetime | None:
    """A stored ISO time as local naive datetime, or None."""
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 10:
        text += "T00:00:00"
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    return moment


def _hhmm(moment: datetime) -> str:
    return moment.strftime("%H:%M")


def _greeting(now: datetime, name: str) -> str:
    if now.hour < 12:
        base = tr("Bom dia")
    elif now.hour < 20:
        base = tr("Boa tarde")
    else:
        base = tr("Boa noite")
    return f"{base}, {name}!" if name else f"{base}!"


def _items_on(day: datetime, overview: dict) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]]]:
    events, reminders = [], []
    for event in overview.get("events") or []:
        start = parse_when(event.get("start"))
        if start and start.date() == day.date():
            events.append((start, str(event.get("title") or "").strip()[:120]))
    for reminder in overview.get("reminders") or []:
        if reminder.get("fired"):
            continue
        due = parse_when(reminder.get("due_at"))
        if due and due.date() == day.date():
            reminders.append((due, str(reminder.get("text") or "").strip()[:120]))
    return sorted(events), sorted(reminders)


def build_briefing(overview: dict, now: datetime, *, name: str = "") -> dict:
    """The morning briefing as ``{"title", "text"}``. Pure; no I/O."""
    events, reminders = _items_on(now, overview)
    later_events = [item for item in events if item[0] >= now - timedelta(minutes=30)]
    tomorrow_events, tomorrow_reminders = _items_on(now + timedelta(days=1), overview)
    lines = [_greeting(now, name)]
    if not later_events and not reminders:
        lines.append(tr("Hoje não tens nada na agenda."))
    if later_events:
        lines.append(tr("Hoje na agenda:"))
        lines += [f"• {_hhmm(start)} {title}" for start, title in later_events[:MAX_LISTED]]
        if len(later_events) > MAX_LISTED:
            lines.append(tr("• e mais {count}", count=len(later_events) - MAX_LISTED))
    if reminders:
        lines.append(tr("Lembretes de hoje:"))
        lines += [f"• {_hhmm(due)} {text}" for due, text in reminders[:MAX_LISTED]]
        if len(reminders) > MAX_LISTED:
            lines.append(tr("• e mais {count}", count=len(reminders) - MAX_LISTED))
    tomorrow = len(tomorrow_events) + len(tomorrow_reminders)
    if tomorrow:
        lines.append(tr("Amanhã tens {count} coisa(s) marcada(s).", count=tomorrow))
    return {"kind": "briefing", "title": tr("Resumo do dia"), "text": "\n".join(lines)}


def build_event_alert(title: str, start: datetime, now: datetime) -> dict:
    minutes = max(0, round((start - now).total_seconds() / 60))
    text = (tr("{title} começa agora ({time}).", title=title, time=_hhmm(start)) if minutes <= 1
            else tr("{title} começa daqui a {minutes} min ({time}).",
                    title=title, minutes=minutes, time=_hhmm(start)))
    return {"kind": "event", "title": tr("Daqui a pouco"), "text": text}


class ProactiveScheduler:
    """Decides, every tick, whether NANO should speak first. Thread-safe enough:
    one daemon thread calls ``tick``; tests call it directly with a clock."""

    def __init__(self, *, overview: Callable[[], dict], emit: Callable[[dict], None],
                 state_path: Path | None, settings: Callable[[], dict] = _settings,
                 now: Callable[[], datetime] = datetime.now):
        self._overview = overview
        self._emit = emit
        self._settings = settings
        self._now = now
        self.state_path = state_path
        self._state = self._load()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._cached: tuple[float, dict] | None = None

    # ── state ────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            if self.state_path and self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {"last_briefing": str(data.get("last_briefing") or ""),
                            "announced": [str(x) for x in data.get("announced") or []][-200:]}
        except Exception:
            logger.debug("Estado da proatividade ilegível; recomeça", exc_info=True)
        return {"last_briefing": "", "announced": []}

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._state), encoding="utf-8")
        except Exception:
            logger.debug("Não consegui guardar o estado da proatividade", exc_info=True)

    def forget(self) -> None:
        """Drop the state file (used by "Apagar todos os dados")."""
        self._state = {"last_briefing": "", "announced": []}
        self._cached = None
        try:
            if self.state_path and self.state_path.exists():
                self.state_path.unlink()
        except Exception:
            logger.debug("Não consegui apagar o estado da proatividade", exc_info=True)

    # ── the decision ─────────────────────────────────────────────────────
    def tick(self) -> list[dict]:
        """Emit whatever is due now. Returns what was emitted."""
        settings = self._settings()
        if not settings.get("briefing") and not settings.get("event_alerts"):
            return []
        now = self._now()
        try:
            overview = self._read_overview()
        except Exception:
            logger.debug("Agenda indisponível para a proatividade", exc_info=True)
            return []
        out: list[dict] = []
        changed = False

        if settings.get("briefing"):
            hour, minute = (int(part) for part in settings["briefing_time"].split(":"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            today = now.date().isoformat()
            if (now >= due and now.hour < max(LATEST_BRIEFING_HOUR, hour + 1)
                    and self._state["last_briefing"] != today):
                out.append(build_briefing(overview, now, name=settings.get("name", "")))
                self._state["last_briefing"] = today
                changed = True

        if settings.get("event_alerts"):
            lead = timedelta(minutes=int(settings.get("lead_minutes") or DEFAULT_LEAD_MINUTES))
            for event in overview.get("events") or []:
                start = parse_when(event.get("start"))
                if start is None or len(str(event.get("start") or "")) == 10:
                    continue                      # all-day events get no countdown
                key = f"{event.get('id')}@{start.isoformat()}"
                if key in self._state["announced"]:
                    continue
                if now <= start <= now + lead:
                    title = str(event.get("title") or tr("Evento")).strip()[:120]
                    out.append(build_event_alert(title, start, now))
                    self._state["announced"] = (self._state["announced"] + [key])[-200:]
                    changed = True

        if changed:
            self._save()
        for notice in out:
            try:
                self._emit(notice)
            except Exception:
                logger.debug("Falha a mostrar um aviso proativo", exc_info=True)
        return out

    def _read_overview(self) -> dict:
        stamp = time.monotonic()
        if self._cached and stamp - self._cached[0] < OVERVIEW_TTL_SECONDS:
            return self._cached[1]
        overview = self._overview() or {}
        self._cached = (stamp, overview)
        return overview

    def briefing_now(self) -> dict:
        """The briefing on demand (button), without marking the day as done."""
        settings = self._settings()
        try:
            overview = self._overview() or {}
        except Exception:
            overview = {}
        return build_briefing(overview, self._now(), name=settings.get("name", ""))

    # ── the thread ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nano-proactive", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # A short wait first, so the window has opened and can show the notice.
        if self._stop.wait(8.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Falha no ciclo da proatividade")
            self._stop.wait(TICK_SECONDS)


__all__ = ["DEFAULT_BRIEFING_TIME", "DEFAULT_LEAD_MINUTES", "LEAD_MINUTES_CHOICES",
           "ProactiveScheduler", "build_briefing", "build_event_alert", "parse_when",
           "valid_time"]
