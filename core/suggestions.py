"""NANO notices the moment: routines at their time, habits, sleep and breaks.

Everything here only PROPOSES or SAYS something. The one thing that acts is a
routine the person set to run by itself, and it runs their own protocol
through the same path as saying its phrase (core/protocols.py): every step
still goes through PolicyEngine, PermissionManager and ToolExecutor, and
closing apps or locking the PC still asks.

WHAT IT DOES
------------
* **Routines** — "every weekday at 08:00, Modo estudo". At that time NANO
  either asks ("São 08:00: começo o Modo estudo?", with a button and a plain
  "sim" by voice) or, if the person chose so, runs it and says what it did.
* **Habits** — NANO keeps the day and time each protocol was started (never
  what was said). When one was started at about this time (±30 min) on 3 or
  more different days of the same kind (weekdays or weekend) in the last four
  weeks, and not yet today, it offers it once: "Costumas ativar o Modo estudo
  por esta hora. Queres que ative?".
* **Bedtime** — between 00:00 and 04:00, while the person is still using the
  PC, once a night: says the time, and when the first thing on today's agenda
  is before 11:00, says that too ("às 08:30 tens Matemática").
* **Breaks** — off by default. After two hours of uninterrupted use (no break
  of 5 minutes), once per two hours: a short nudge to rest the eyes.

All of it goes through the notice gate (core/notice_gate.py), so quiet hours,
"não me incomodes", full-screen games, the level and the daily limit apply.

WHAT IS STORED
--------------
``routines.json`` (the routines the person made) and ``suggestions_state.json``
(protocol start times of the last 4 weeks, and what was already offered
today). "Apagar todos os dados" removes both.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from core.i18n import tr

logger = logging.getLogger("nano.suggestions")

TICK_SECONDS = 30.0
MAX_ROUTINES = 20
ROUTINE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
PROTOCOL_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,39}$")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
#: A routine is still started this many minutes after its time (the PC was
#: busy, NANO was starting), never later: at 10:00, "08:00 routine" is wrong.
ROUTINE_GRACE_MINUTES = 20

HABIT_WINDOW_MINUTES = 30
HABIT_MIN_DAYS = 3
HABIT_LOOKBACK_DAYS = 28
HABIT_LOG_MAX = 400

BEDTIME_FROM, BEDTIME_UNTIL = 0, 4
BEDTIME_EARLY_EVENT_HOUR = 11
#: Idle seconds that still count as "using the PC right now".
ACTIVE_IDLE_SECONDS = 120
BREAK_AFTER_MINUTES = 120
#: A pause this long resets the "uninterrupted use" clock.
BREAK_RESET_SECONDS = 5 * 60

SETTING_DEFAULTS: dict[str, Any] = {
    "proactive_suggestions": True,
    "proactive_habits": True,
    "proactive_bedtime": True,
    "proactive_breaks": False,
}


def valid_setting(key: str, value: Any) -> bool | None:
    if key in SETTING_DEFAULTS:
        return isinstance(value, bool)
    return None


def _settings() -> dict[str, Any]:
    try:
        from core import user_settings
        get = user_settings.get
    except Exception:  # pragma: no cover
        def get(_key, default=None):
            return default
    return {key: get(key, default) is not False if default else get(key, default) is True
            for key, default in SETTING_DEFAULTS.items()}


class RoutineError(ValueError):
    """A routine that cannot be saved. The message is user-facing."""


def validate_routine(raw: Any, *, known_protocols: set[str] | None = None) -> dict:
    if not isinstance(raw, dict):
        raise RoutineError(tr("Rotina inválida."))
    protocol = str(raw.get("protocol") or "").strip()
    if not PROTOCOL_ID.match(protocol) or (known_protocols is not None and protocol not in known_protocols):
        raise RoutineError(tr("Escolhe um dos teus protocolos."))
    at = str(raw.get("time") or "").strip()
    if not _TIME.match(at):
        raise RoutineError(tr("Escolhe uma hora válida (HH:MM)."))
    days = raw.get("days")
    if not isinstance(days, list) or not days or not all(type(d) is int and 0 <= d <= 6 for d in days):
        raise RoutineError(tr("Escolhe pelo menos um dia."))
    routine_id = str(raw.get("id") or "").strip() or f"r-{uuid.uuid4().hex[:10]}"
    if not ROUTINE_ID.match(routine_id):
        raise RoutineError(tr("Rotina inválida."))
    return {"id": routine_id, "protocol": protocol, "time": at, "days": sorted(set(days)),
            "ask": raw.get("ask") is not False, "enabled": raw.get("enabled") is not False}


class RoutineStore:
    def __init__(self, path: Path | None) -> None:
        self.path = path
        self._lock = threading.Lock()

    def list(self) -> list[dict]:
        try:
            if self.path and self.path.exists():
                data = json.loads(self.path.read_text(encoding="utf-8"))
                out = []
                for raw in data if isinstance(data, list) else []:
                    try:
                        out.append(validate_routine(raw))
                    except RoutineError:
                        continue
                return out[:MAX_ROUTINES]
        except Exception:
            logger.debug("routines.json unreadable", exc_info=True)
        return []

    def _write(self, routines: list[dict]) -> None:
        if not self.path:
            return
        from core.atomic_storage import write_bytes

        write_bytes(self.path, json.dumps(routines, ensure_ascii=False, indent=2).encode("utf-8"))

    def save(self, raw: Any, *, known_protocols: set[str] | None = None) -> dict:
        routine = validate_routine(raw, known_protocols=known_protocols)
        with self._lock:
            routines = [r for r in self.list() if r["id"] != routine["id"]]
            if len(routines) >= MAX_ROUTINES:
                raise RoutineError(tr("Já tens {count} rotinas, o máximo.", count=MAX_ROUTINES))
            routines.append(routine)
            routines.sort(key=lambda r: (r["time"], r["protocol"]))
            self._write(routines)
        return routine

    def delete(self, routine_id: str) -> bool:
        with self._lock:
            routines = self.list()
            kept = [r for r in routines if r["id"] != routine_id]
            if len(kept) == len(routines):
                return False
            self._write(kept)
        return True

    def forget(self) -> None:
        try:
            if self.path and self.path.exists():
                self.path.unlink()
        except OSError:
            logger.debug("Could not delete routines.json", exc_info=True)


def _day_kind(moment: datetime) -> str:
    return "weekend" if moment.weekday() >= 5 else "weekday"


def habit_due(log: list[dict], now: datetime) -> list[str]:
    """Protocols started at about this time on enough same-kind days."""
    start = now - timedelta(days=HABIT_LOOKBACK_DAYS)
    minute_now = now.hour * 60 + now.minute
    days_by_protocol: dict[str, set[str]] = {}
    done_today: set[str] = set()
    for entry in log:
        try:
            moment = datetime.fromisoformat(str(entry.get("at")))
        except (TypeError, ValueError):
            continue
        protocol = str(entry.get("protocol") or "")
        if not protocol:
            continue
        if moment.date() == now.date():
            done_today.add(protocol)
            continue
        if moment < start or _day_kind(moment) != _day_kind(now):
            continue
        minute = moment.hour * 60 + moment.minute
        if abs(minute - minute_now) <= HABIT_WINDOW_MINUTES:
            days_by_protocol.setdefault(protocol, set()).add(moment.date().isoformat())
    return sorted(protocol for protocol, days in days_by_protocol.items()
                  if len(days) >= HABIT_MIN_DAYS and protocol not in done_today)


class SuggestionEngine:
    """Ticks every 30 s; emits notices through the gate."""

    def __init__(self, *, emit: Callable[[dict], Any], state_path: Path | None,
                 routines: RoutineStore,
                 protocols: Callable[[], list[dict]],
                 run_protocol: Callable[[str], Any],
                 overview: Callable[[], dict] | None = None,
                 idle: Callable[[], float | None] | None = None,
                 settings: Callable[[], dict] = _settings,
                 now: Callable[[], datetime] = datetime.now,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._emit = emit
        self.state_path = state_path
        self.routines = routines
        self._protocols = protocols
        self._run_protocol = run_protocol
        self._overview = overview
        self._idle = idle or (lambda: None)
        self._settings = settings
        self._now = now
        self._clock = clock
        self._lock = threading.Lock()
        self._state = self._load()
        self._active_since: float | None = None
        self._last_break_nudge: float | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── state ──────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        try:
            if self.state_path and self.state_path.exists():
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {"log": [e for e in data.get("log") or [] if isinstance(e, dict)][-HABIT_LOG_MAX:],
                            "done": [str(k) for k in data.get("done") or []][-200:]}
        except Exception:
            logger.debug("Suggestions state unreadable", exc_info=True)
        return {"log": [], "done": []}

    def _save(self) -> None:
        if not self.state_path:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self._state), encoding="utf-8")
        except Exception:
            logger.debug("Could not save the suggestions state", exc_info=True)

    def forget(self) -> None:
        with self._lock:
            self._state = {"log": [], "done": []}
            try:
                if self.state_path and self.state_path.exists():
                    self.state_path.unlink()
            except OSError:
                logger.debug("Could not delete the suggestions state", exc_info=True)
        self.routines.forget()

    def _done(self, key: str) -> bool:
        return key in self._state["done"]

    def _mark(self, key: str) -> None:
        self._state["done"] = (self._state["done"] + [key])[-200:]

    def record_protocol(self, protocol_id: Any) -> None:
        """A protocol was started (any way): remember only the day and time."""
        protocol = str(protocol_id or "")
        if not PROTOCOL_ID.match(protocol):
            return
        with self._lock:
            stamp = self._now().replace(second=0, microsecond=0).isoformat()
            self._state["log"] = (self._state["log"] + [{"protocol": protocol, "at": stamp}])[-HABIT_LOG_MAX:]
            self._save()

    # ── the decision ───────────────────────────────────────────────────────
    def tick(self) -> list[dict]:
        settings = self._settings()
        now = self._now()
        out: list[dict] = []
        protocols = {}
        try:
            protocols = {p["id"]: p for p in self._protocols() or [] if p.get("id")}
        except Exception:
            logger.debug("Protocols unavailable for suggestions", exc_info=True)
        with self._lock:
            out += self._routines_due(now, protocols)
            if settings.get("proactive_suggestions", True):
                if settings.get("proactive_habits", True):
                    out += self._habits_due(now, protocols)
                if settings.get("proactive_bedtime", True):
                    out += self._bedtime(now)
                if settings.get("proactive_breaks", False):
                    out += self._break(now)
            if out:
                self._save()
        for item in out:
            try:
                self._emit(item)
            except Exception:
                logger.debug("Could not emit a suggestion", exc_info=True)
        return out

    def _routines_due(self, now: datetime, protocols: dict) -> list[dict]:
        out = []
        for routine in self.routines.list():
            if not routine["enabled"] or now.weekday() not in routine["days"]:
                continue
            protocol = protocols.get(routine["protocol"])
            if protocol is None:
                continue
            hour, minute = (int(part) for part in routine["time"].split(":"))
            due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            key = f"routine:{routine['id']}:{now.date().isoformat()}"
            if not (due <= now <= due + timedelta(minutes=ROUTINE_GRACE_MINUTES)) or self._done(key):
                continue
            self._mark(key)
            name = str(protocol.get("name") or "")
            phrase = (protocol.get("phrases") or [name])[0]
            if routine["ask"]:
                out.append({"kind": "routine", "title": tr("Rotina · {time}", time=routine["time"]),
                            "text": tr("São {time}: começo o “{name}”?", time=routine["time"], name=name),
                            "speech": tr("São {time}. Começo o {name}?", time=routine["time"], name=name),
                            "offer": {"label": tr("Começar"), "send": phrase},
                            # Held while away: asked when the person is back,
                            # but not hours later.
                            "expires_at": time.time() + 2 * 3600})
            else:
                try:
                    self._run_protocol(routine["protocol"])
                except Exception:
                    logger.exception("Routine %s failed to start", routine["id"])
                    continue
                out.append({"kind": "routine", "title": tr("Rotina · {time}", time=routine["time"]),
                            "text": tr("Comecei o “{name}”, como combinámos.", name=name),
                            "speech": tr("Comecei o {name}.", name=name)})
        return out

    def _habits_due(self, now: datetime, protocols: dict) -> list[dict]:
        out = []
        scheduled = {r["protocol"] for r in self.routines.list() if r["enabled"]}
        for protocol_id in habit_due(self._state["log"], now):
            protocol = protocols.get(protocol_id)
            key = f"habit:{protocol_id}:{now.date().isoformat()}"
            if protocol is None or protocol_id in scheduled or self._done(key):
                continue
            self._mark(key)
            name = str(protocol.get("name") or "")
            phrase = (protocol.get("phrases") or [name])[0]
            out.append({"kind": "habit", "title": tr("Sugestão"),
                        "text": tr("Costumas ativar o “{name}” por esta hora. Queres que ative?", name=name),
                        "speech": tr("Costumas ativar o {name} por esta hora. Queres que ative?", name=name),
                        "offer": {"label": tr("Ativar"), "send": phrase}})
        return out

    def _is_active(self) -> bool | None:
        try:
            idle = self._idle()
        except Exception:
            return None
        if idle is None:
            return None
        return idle < ACTIVE_IDLE_SECONDS

    def _bedtime(self, now: datetime) -> list[dict]:
        if not (BEDTIME_FROM <= now.hour < BEDTIME_UNTIL):
            return []
        key = f"bedtime:{now.date().isoformat()}"
        # Only when NANO can tell the person is really at the PC: without the
        # reading (another OS), never.
        if self._done(key) or self._is_active() is not True:
            return []
        first = self._first_thing_today(now)
        self._mark(key)
        clock = now.strftime("%H:%M")
        if first:
            text = tr("Já são {clock} e às {time} tens “{title}”. Talvez seja boa hora para "
                      "descansar.", clock=clock, time=first[0].strftime("%H:%M"), title=first[1])
        else:
            text = tr("Já são {clock}. Se amanhã acordas cedo, talvez seja boa hora para descansar.",
                      clock=clock)
        return [{"kind": "bedtime", "title": tr("Já é tarde"), "text": text, "night": True}]

    def _first_thing_today(self, now: datetime) -> tuple[datetime, str] | None:
        if self._overview is None:
            return None
        try:
            from core.proactive import parse_when

            overview = self._overview() or {}
        except Exception:
            return None
        best = None
        for event in overview.get("events") or []:
            start = parse_when(event.get("start"))
            if start is None or len(str(event.get("start") or "")) == 10:
                continue
            if start.date() == now.date() and now < start and start.hour < BEDTIME_EARLY_EVENT_HOUR:
                item = (start, str(event.get("title") or "").strip()[:80])
                best = item if best is None or item[0] < best[0] else best
        return best

    def _break(self, now: datetime) -> list[dict]:
        try:
            idle = self._idle()
        except Exception:
            idle = None
        if idle is None:
            return []
        mono = self._clock()
        if idle >= BREAK_RESET_SECONDS:
            self._active_since = None
            return []
        if self._active_since is None:
            self._active_since = mono - idle
            return []
        used = mono - self._active_since
        if used < BREAK_AFTER_MINUTES * 60:
            return []
        if self._last_break_nudge is not None and mono - self._last_break_nudge < BREAK_AFTER_MINUTES * 60:
            return []
        self._last_break_nudge = mono
        hours = int(used // 3600)
        return [{"kind": "break", "title": tr("Pausa"),
                 "text": tr("Estás ao PC há {hours} h seguidas. Uma pausa de 5 minutos faz bem aos "
                            "olhos e às costas.", hours=max(1, hours))}]

    # ── the thread ─────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nano-suggestions", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(15.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("Suggestions loop failed")
            self._stop.wait(TICK_SECONDS)


__all__ = ["RoutineError", "RoutineStore", "SuggestionEngine", "habit_due", "valid_setting",
           "validate_routine"]
