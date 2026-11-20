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

#: The evening before an event that needs getting ready for, NANO offers to
#: help, from this hour until PREP_LATEST_HOUR.
PREP_HOUR = 18
PREP_LATEST_HOUR = 23
#: Events worth getting ready for (accents and case ignored, whole words).
PREP_KEYWORDS = (
    "teste", "exame", "frequencia", "apresentacao", "entrevista", "prova", "oral",
    "defesa", "reuniao", "consulta", "viagem", "voo", "jogo", "treino",
    "test", "exam", "quiz", "presentation", "interview", "meeting", "appointment",
    "flight", "trip", "match", "deadline", "entrega",
)
#: Short words that say nothing about what a fact and an event share.
_STOPWORDS = frozenset({
    "para", "com", "uma", "umas", "uns", "das", "dos", "que", "por", "mais", "como", "sobre",
    "the", "and", "with", "for", "from", "this", "that", "meu", "minha", "teu", "tua",
    "amanha", "hoje", "dia", "tomorrow", "today",
})
MAX_RELATED_FACTS = 2


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
        "prep": get("proactive_prep", True) is not False,
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


def _plain(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFD", str(text or "").casefold())
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _significant_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", _plain(text)) if w not in _STOPWORDS}


def needs_preparation(title: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", _plain(title)))
    return any(keyword in words for keyword in PREP_KEYWORDS)


def related_facts(title: str, facts: list[str], *, limit: int = MAX_RELATED_FACTS) -> list[str]:
    """What NANO remembers that shares a meaningful word with an event title.

    "Teste de matemática" finds "Tenho dificuldades em matemática" and
    "O professor de matemática é o Rui"; generic words (para, amanhã, the...)
    never count.
    """
    keys = _significant_words(title) - {k for k in PREP_KEYWORDS}
    if not keys:
        return []
    scored = []
    for fact in facts:
        overlap = len(keys & _significant_words(fact))
        if overlap:
            scored.append((-overlap, len(fact), str(fact).strip()[:160]))
    return [text for _, _, text in sorted(scored)[:limit]]


def build_prep_notice(title: str, start: datetime, facts: list[str], *,
                      all_day: bool = False) -> dict:
    """The evening-before offer: "tens teste de matemática amanhã, queres rever?"."""
    if all_day:
        # An all-day event has no hour: "amanhã às 00:00" was simply wrong.
        first = tr("Amanhã tens “{title}”. Queres preparar-te? Posso ajudar a rever, "
                   "fazer um resumo ou organizar o que precisas.", title=title)
    else:
        first = tr("Amanhã às {time} tens “{title}”. Queres preparar-te? Posso ajudar a rever, "
                   "fazer um resumo ou organizar o que precisas.", time=_hhmm(start), title=title)
    lines = [first]
    related = related_facts(title, facts)
    if related:
        lines.append(tr("O que me lembro sobre isto:"))
        lines += [f"• {fact}" for fact in related]
    return {"kind": "prep", "title": tr("Para amanhã"), "text": "\n".join(lines)}


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


#: Marks an all-day event inside ``_items_on``'s (time, title) pairs. A control
#: character, so no real title can start with it.
_ALL_DAY = "\x00"


def is_all_day(event: dict) -> bool:
    """A calendar event with a date and no time (Google's ``start.date``)."""
    return len(str(event.get("start") or "").strip()) == 10


def _on_day(event: dict, start: datetime, day: datetime) -> bool:
    """Whether an event is on ``day``. An all-day event covers every day up to
    its end date, which Google stores as the day AFTER the last one."""
    if not is_all_day(event):
        return start.date() == day.date()
    end = parse_when(event.get("end"))
    last = start.date()
    if end is not None and len(str(event.get("end") or "").strip()) == 10 and end > start:
        last = (end - timedelta(days=1)).date()
    return start.date() <= day.date() <= last


def _items_on(day: datetime, overview: dict) -> tuple[list[tuple[datetime, str]], list[tuple[datetime, str]]]:
    """Today's (or tomorrow's) events and reminders, sorted.

    An all-day event is dated at midnight of ``day``, so it sorts first, and
    its title carries the ``_ALL_DAY`` mark: see ``build_briefing``.
    """
    events, reminders = [], []
    for event in overview.get("events") or []:
        start = parse_when(event.get("start"))
        if start and _on_day(event, start, day):
            title = str(event.get("title") or "").strip()[:120]
            if is_all_day(event):
                midnight = datetime.combine(day.date(), datetime.min.time())
                events.append((midnight, _ALL_DAY + title))
            else:
                events.append((start, title))
    for reminder in overview.get("reminders") or []:
        if reminder.get("fired"):
            continue
        due = parse_when(reminder.get("due_at"))
        if due and due.date() == day.date():
            reminders.append((due, str(reminder.get("text") or "").strip()[:120]))
    return sorted(events), sorted(reminders)


def build_briefing(overview: dict, now: datetime, *, name: str = "",
                   facts: list[str] | None = None) -> dict:
    """The morning briefing as ``{"title", "text"}``. Pure; no I/O."""
    events, reminders = _items_on(now, overview)
    # An all-day event (a birthday, a holiday, an exam day) started at 00:00,
    # so the "still ahead" filter used to drop every one of them from the
    # morning briefing. They are always listed, first, without a clock time.
    later_events = [item for item in events
                    if item[1].startswith(_ALL_DAY) or item[0] >= now - timedelta(minutes=30)]
    tomorrow_events, tomorrow_reminders = _items_on(now + timedelta(days=1), overview)
    lines = [_greeting(now, name)]
    if not later_events and not reminders:
        lines.append(tr("Hoje não tens nada na agenda."))
    if later_events:
        lines.append(tr("Hoje na agenda:"))
        for start, title in later_events[:MAX_LISTED]:
            if title.startswith(_ALL_DAY):
                title = title[len(_ALL_DAY):]
                lines.append(f"• {tr('Todo o dia')}: {title}")
            else:
                lines.append(f"• {_hhmm(start)} {title}")
            # One thing NANO remembers about it, if anything matches.
            lines += [f"   ↳ {fact}" for fact in related_facts(title, facts or [], limit=1)]
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
                 now: Callable[[], datetime] = datetime.now,
                 facts: Callable[[], list[str]] | None = None):
        self._overview = overview
        self._facts = facts
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
        if not (settings.get("briefing") or settings.get("event_alerts") or settings.get("prep")):
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
                out.append(build_briefing(overview, now, name=settings.get("name", ""),
                                          facts=self._read_facts()))
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

        if settings.get("prep") and PREP_HOUR <= now.hour < PREP_LATEST_HOUR:
            tomorrow = (now + timedelta(days=1)).date()
            facts = None
            for event in overview.get("events") or []:
                start = parse_when(event.get("start"))
                title = str(event.get("title") or "").strip()[:120]
                if start is None or start.date() != tomorrow or not needs_preparation(title):
                    continue
                key = f"prep:{event.get('id')}@{start.date().isoformat()}"
                if key in self._state["announced"]:
                    continue
                if facts is None:
                    facts = self._read_facts()
                out.append(build_prep_notice(title, start, facts, all_day=is_all_day(event)))
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

    def _read_facts(self) -> list[str]:
        if self._facts is None:
            return []
        try:
            return [str(f) for f in (self._facts() or [])][:300]
        except Exception:
            logger.debug("Memória indisponível para a proatividade", exc_info=True)
            return []

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
        return build_briefing(overview, self._now(), name=settings.get("name", ""),
                              facts=self._read_facts())

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
