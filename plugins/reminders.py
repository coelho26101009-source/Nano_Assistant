"""
Nano Plugin: Reminders
Persistent reminders and alarms (SQLite) with a lightweight scheduler on a
daemon thread. When a reminder is due it is shown on the PC (a Windows
notification, through whatever notifier the host registered), sent to the
phone (webhook, if configured) and saved to memory so Nano can refer to it
later.
"""

import asyncio
import calendar
import logging
import re
import sys
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_setting
from core.memory import get_memory
from plugins.smart_life import phone_notification

logger = logging.getLogger("helios.plugins.reminders")

CHECK_INTERVAL_SECONDS = 20

_scheduler: threading.Thread | None = None
_stop = threading.Event()

#: Local delivery channels (Windows notification, UI toast). The host
#: registers them; the plugin never imports the UI or the desktop shell.
_notifiers: list[Callable[[dict], None]] = []
_notifiers_lock = threading.Lock()


def register_notifier(notifier: Callable[[dict], None]) -> None:
    """Add a local delivery channel for due reminders (idempotent)."""
    with _notifiers_lock:
        if notifier not in _notifiers:
            _notifiers.append(notifier)


def unregister_notifier(notifier: Callable[[dict], None]) -> None:
    with _notifiers_lock:
        if notifier in _notifiers:
            _notifiers.remove(notifier)


def _notify_locally(payload: dict) -> int:
    """Hand a due reminder to every registered notifier. Returns how many took it.

    One failing channel never stops the others, and never un-fires the
    reminder: it was already claimed in ``_fire``.
    """
    if not get_setting("reminders.desktop_notifications", True):
        return 0
    with _notifiers_lock:
        targets = list(_notifiers)
    delivered = 0
    for notifier in targets:
        try:
            notifier(dict(payload))
            delivered += 1
        except Exception as exc:
            logger.warning(f"Notificação local do lembrete #{payload.get('id')} falhou: {exc}")
    return delivered


# ─── Persistence ──────────────────────────────────────────────────────────────

def _conn():
    conn = get_memory().conn
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reminders (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            text     TEXT NOT NULL,
            due_at   TEXT NOT NULL,
            created  TEXT NOT NULL,
            fired    INTEGER NOT NULL DEFAULT 0
        )
    """)
    # Recurring reminders (added after the first release): NULL = once.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(reminders)")}
    if "recurrence" not in columns:
        conn.execute("ALTER TABLE reminders ADD COLUMN recurrence TEXT")
    conn.commit()
    return conn


# ─── Parsing de datas ─────────────────────────────────────────────────────────

_RELATIVE = re.compile(
    r"^\s*(?:daqui\s+a\s+|em\s+|dentro\s+de\s+)?(\d+)\s*"
    r"(min|mins|minuto|minutos|h|hora|horas|s|seg|segundos|d|dia|dias)\s*$",
    re.IGNORECASE,
)

_UNIT_SECONDS = {
    "s": 1, "seg": 1, "segundos": 1,
    "min": 60, "mins": 60, "minuto": 60, "minutos": 60,
    "h": 3600, "hora": 3600, "horas": 3600,
    "d": 86400, "dia": 86400, "dias": 86400,
}


_WEEKDAYS = {
    "segunda": 0, "segunda-feira": 0, "terca": 1, "terça": 1, "terca-feira": 1, "terça-feira": 1,
    "quarta": 2, "quarta-feira": 2, "quinta": 3, "quinta-feira": 3, "sexta": 4, "sexta-feira": 4,
    "sabado": 5, "sábado": 5, "domingo": 6,
}
_WEEKDAY_RE = "|".join(sorted((re.escape(k) for k in _WEEKDAYS), key=len, reverse=True))

#: Recurrence codes stored in the database, and how they read in Portuguese.
RECURRENCES = {
    "daily": "todos os dias",
    "weekdays": "nos dias úteis",
    "weekly": "todas as semanas",
    "monthly": "todos os meses",
}
_RECURRENCE_ALIASES = {
    "daily": "daily", "diario": "daily", "diário": "daily", "diariamente": "daily",
    "todos os dias": "daily", "todo dia": "daily", "todos dias": "daily",
    "weekdays": "weekdays", "dias uteis": "weekdays", "dias úteis": "weekdays",
    "nos dias uteis": "weekdays", "nos dias úteis": "weekdays",
    "weekly": "weekly", "semanal": "weekly", "semanalmente": "weekly",
    "todas as semanas": "weekly", "toda semana": "weekly",
    "monthly": "monthly", "mensal": "monthly", "mensalmente": "monthly",
    "todos os meses": "monthly", "todo mes": "monthly", "todo mês": "monthly",
}


def normalise_recurrence(value) -> str | None:
    """A stored recurrence code, or None for a one-off. Raises on nonsense."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    key = " ".join(str(value).strip().lower().split())
    if key in ("nao", "não", "none", "nunca", "once", "uma vez"):
        return None
    if key not in _RECURRENCE_ALIASES:
        raise ValueError(f"Não sei repetir '{value}'. Usa: todos os dias, dias úteis, "
                         "todas as semanas ou todos os meses.")
    return _RECURRENCE_ALIASES[key]


def _time_of_day(text: str) -> tuple[int, int] | None:
    """'9', '9h', '9h30', '09:30', '9 e meia', 'meio-dia', '3 da tarde', '21h15'."""
    t = text.strip().lower()
    t = re.sub(r"^(?:às|as|à|a|pelas|pelas\s+|por\s+volta\s+das)\s+", "", t)
    if t in ("meio-dia", "meio dia", "meiodia"):
        return 12, 0
    if t in ("meia-noite", "meia noite"):
        return 0, 0
    match = re.fullmatch(
        r"(\d{1,2})(?:\s*(?::|h|horas?)\s*(\d{2})?)?(?:\s*e\s+(meia|um quarto|quarto))?"
        r"(?:\s*(?:min|m))?(?:\s+(?:da|de)\s+(manhã|manha|tarde|noite|madrugada))?",
        t)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if match.group(3):
        minute = 30 if match.group(3) == "meia" else 15
    period = match.group(4)
    if period in ("tarde", "noite") and hour < 12:
        hour += 12
    if period == "noite" and hour == 24:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _at(day: datetime, hm: tuple[int, int]) -> datetime:
    return day.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)


_EN_WEEKDAYS = {
    "monday": "segunda", "tuesday": "terça", "wednesday": "quarta", "thursday": "quinta",
    "friday": "sexta", "saturday": "sábado", "sunday": "domingo",
}
#: English phrasings, rewritten into the Portuguese ones this parser already
#: knows. Order matters: longer phrases first. Applied only to whole words, so
#: a Portuguese phrase passes through untouched.
_EN_PHRASES = (
    (r"\bthe day after tomorrow\b|\bday after tomorrow\b", "depois de amanhã"),
    (r"\btomorrow\b", "amanhã"),
    (r"\btonight\b", "hoje à noite"),
    (r"\btoday\b", "hoje"),
    (r"\bin the morning\b|\bmorning\b", "de manhã"),
    (r"\bin the afternoon\b|\bafternoon\b", "à tarde"),
    (r"\bin the evening\b|\bat night\b|\bevening\b", "à noite"),
    (r"\bat noon\b|\bnoon\b|\bmidday\b", "meio-dia"),
    (r"\bat midnight\b|\bmidnight\b", "meia-noite"),
    (r"\bevery weekday\b|\bon weekdays\b|\bweekdays\b", "dias úteis"),
    (r"\bevery day\b|\bdaily\b", "todos os dias"),
    (r"\bevery week\b|\bweekly\b", "todas as semanas"),
    (r"\bevery month\b|\bmonthly\b", "todos os meses"),
    (r"\b(?:in|within)\s+(?=\d)", "daqui a "),
    (r"\bminutes?\b", "minutos"),
    (r"\b(?:hours?|hrs?)\b", "horas"),
    (r"\bseconds?\b|\bsecs?\b", "segundos"),
    (r"\bdays?\b", "dias"),
    (r"\b(?:on|this)\s+(?=(?:mon|tues|wednes|thurs|fri|satur|sun)day\b)", ""),
    (r"\bnext\s+(?=(?:mon|tues|wednes|thurs|fri|satur|sun)day\b)", "próxima "),
)


def _from_english(text: str) -> str:
    """'tomorrow at 9pm' -> 'amanhã às 21:00'; Portuguese comes back unchanged."""
    t = text.lower()
    if not re.search(r"[a-z]", t):
        return text
    # 9pm, 9:30 pm, 12am: to the 24-hour clock the parser reads.
    def clock(match: re.Match) -> str:
        hour, minute, half = int(match.group(1)), int(match.group(2) or 0), match.group(3)
        if hour > 12 or minute > 59:
            return match.group(0)
        hour = (hour % 12) + (12 if half == "pm" else 0)
        return f"{hour:02d}:{minute:02d}"
    t = re.sub(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", clock, t)
    for pattern, replacement in _EN_PHRASES:
        t = re.sub(pattern, replacement, t)
    t = re.sub(r"\bevery\s+(" + "|".join(_EN_WEEKDAYS) + r")\b",
               lambda m: f"todas as {_EN_WEEKDAYS[m.group(1)]}s", t)
    t = re.sub(r"\b(" + "|".join(_EN_WEEKDAYS) + r")s?\b", lambda m: _EN_WEEKDAYS[m.group(1)], t)
    t = re.sub(r"\bat\b", "às", t)
    return " ".join(t.split())


def parse_when(when: str, now: datetime | None = None) -> datetime | None:
    """Turn what the user said into a moment.

    Accepts ISO ('2026-01-02 18:30'), 'dd/mm[/aaaa] HH:MM', 'HH:MM' (today or
    tomorrow), relative ('10 minutos', 'daqui a 2 horas') and Portuguese phrases:
    'amanhã às 9', 'hoje às 15h30', 'depois de amanhã às 10', 'sexta às 18h',
    'na segunda de manhã', 'às 9 da noite', 'meio-dia'.
    """
    when = _from_english(" ".join((when or "").strip().split()))
    if not when:
        return None
    now = now or datetime.now()

    match = _RELATIVE.match(when)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return now + timedelta(seconds=amount * _UNIT_SECONDS[unit])

    if re.fullmatch(r"\d{1,2}:\d{2}", when):
        hour, minute = (int(p) for p in when.split(":"))
        if hour > 23 or minute > 59:
            return None
        target = _at(now, (hour, minute))
        return target if target > now else target + timedelta(days=1)

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%d/%m %H:%M"):
        try:
            if fmt == "%d/%m %H:%M":
                # Parse with the year attached: strptime defaults to 1900,
                # which is not a leap year, so "29/02 10:00" failed outright.
                parsed = datetime.strptime(f"{now.year}/{when}", "%Y/" + fmt)
                if parsed <= now:
                    # "02/01 09:00" said in December means next January,
                    # not a moment that has already passed.
                    try:
                        parsed = parsed.replace(year=now.year + 1)
                    except ValueError:  # 29/02 and next year is not a leap year
                        pass
                return parsed
            return datetime.strptime(when, fmt)
        except ValueError:
            continue

    return _parse_phrase(when.lower(), now)


_PERIOD_DEFAULT = {"manhã": (9, 0), "manha": (9, 0), "tarde": (15, 0), "noite": (21, 0)}


def _parse_phrase(text: str, now: datetime) -> datetime | None:
    text = re.sub(r"^(?:para|pra)\s+", "", text)
    day: datetime | None = None
    rest = text
    explicit_day = False

    m = re.match(r"^(depois de amanhã|depois de amanha|amanhã|amanha|hoje)\b\s*(.*)$", text)
    if m:
        offset = {"hoje": 0, "amanhã": 1, "amanha": 1}.get(m.group(1), 2)
        day, rest, explicit_day = now + timedelta(days=offset), m.group(2), True
    else:
        m = re.match(rf"^(?:na |no |nesta |neste |esta |este |próxima |proxima |próximo |proximo )?"
                     rf"({_WEEKDAY_RE})\b\s*(.*)$", text)
        if m:
            target = _WEEKDAYS[m.group(1)]
            ahead = (target - now.weekday()) % 7
            day, rest, explicit_day = now + timedelta(days=ahead), m.group(2), True

    rest = rest.strip()
    period = re.fullmatch(r"(?:de |à |a )?(manhã|manha|tarde|noite)", rest)
    if period:
        hm = _PERIOD_DEFAULT[period.group(1)]
    elif rest:
        hm = _time_of_day(rest)
        if hm is None:
            return None
    elif explicit_day:
        hm = (9, 0)            # "amanhã" alone: the start of the day, not midnight
    else:
        return None

    if day is None:
        target = _at(now, hm)
        return target if target > now else target + timedelta(days=1)
    target = _at(day, hm)
    if target <= now:
        if explicit_day and m and m.group(1) in _WEEKDAYS:
            target += timedelta(days=7)   # "sexta às 9" said on a Friday at 10 means next week
        else:
            return target                  # "hoje às 8" at 10: in the past; the caller refuses it
    return target


def parse_recurring(when: str) -> tuple[str, str | None]:
    """Split 'todos os dias às 8' into ('às 8', 'daily'). Plain phrases pass through."""
    text = _from_english(" ".join((when or "").strip().split()))
    lowered = text.lower()
    patterns = (
        (r"^(?:todos os dias|todo dia|diariamente)\b\s*(.*)$", "daily"),
        (r"^(?:nos |em )?dias [úu]teis\b\s*(.*)$", "weekdays"),
        (r"^(?:todos os meses|todo m[eê]s|mensalmente)\b\s*(.*)$", "monthly"),
        (r"^(?:todas as semanas|semanalmente)\b\s*(.*)$", "weekly"),
    )
    for pattern, code in patterns:
        m = re.match(pattern, lowered)
        if m:
            return m.group(1).strip() or "", code
    m = re.match(rf"^(?:todas as|todos os|às|as) ({_WEEKDAY_RE})s?\b\s*(.*)$", lowered)
    if m and m.group(1) in _WEEKDAYS:
        return f"{m.group(1)} {m.group(2)}".strip(), "weekly"
    return text, None


def next_occurrence(due: datetime, recurrence: str, now: datetime | None = None) -> datetime:
    """The first occurrence of the series strictly after ``now``.

    Missed occurrences (the PC was off) are skipped, not fired in a burst.
    """
    now = now or datetime.now()
    candidate = due
    for _ in range(4000):
        if recurrence == "daily":
            candidate += timedelta(days=1)
        elif recurrence == "weekdays":
            candidate += timedelta(days=1)
            while candidate.weekday() >= 5:
                candidate += timedelta(days=1)
        elif recurrence == "weekly":
            candidate += timedelta(weeks=1)
        elif recurrence == "monthly":
            month = candidate.month % 12 + 1
            year = candidate.year + (candidate.month // 12)
            day = min(due.day, calendar.monthrange(year, month)[1])
            candidate = candidate.replace(year=year, month=month, day=day)
        else:
            raise ValueError(f"recorrência desconhecida: {recurrence}")
        if candidate > now:
            return candidate
    raise ValueError("não encontrei a próxima ocorrência")


# ─── Ferramentas ──────────────────────────────────────────────────────────────

def _recurrence_label(code) -> str | None:
    return RECURRENCES.get(code) if code else None


def set_reminder(text: str, when: str, repeat: str | None = None) -> dict:
    """Cria um lembrete, único ou recorrente.

    'when' aceita '10 minutos', '18:30', '2026-01-02 18:30', 'amanhã às 9',
    'sexta às 15h', 'todos os dias às 8' ou 'todas as segundas às 9'.
    'repeat' (opcional): daily, weekdays, weekly ou monthly.
    """
    try:
        repeat_code = normalise_recurrence(repeat)
    except ValueError as exc:
        return {"error": str(exc)}
    when_text, phrase_code = parse_recurring(when)
    recurrence = repeat_code or phrase_code
    if recurrence and not when_text:
        return {"error": f"A que horas? Por exemplo: '{RECURRENCES[recurrence]} às 9'."}
    due = parse_when(when_text) if when_text else None
    if due is None:
        return {"error": f"Não percebi o momento '{when}'. Usa '10 minutos', '18:30', "
                         "'amanhã às 9', 'sexta às 15h' ou 'todos os dias às 8'."}
    if not (text or "").strip():
        return {"error": "Preciso de saber do que te devo lembrar."}
    now = datetime.now()
    if recurrence:
        # A series whose first slot already passed (e.g. "todos os dias às 8"
        # said at 10:00) starts at its next slot instead of being refused.
        if due <= now or (recurrence == "weekdays" and due.weekday() >= 5):
            due = next_occurrence(due, recurrence, now)
    elif due <= now:
        # An absolute time in the past used to be stored as due and fired on
        # the scheduler's next pass, i.e. "lembra-me às 2026-01-02 18:30" a
        # day late produced an immediate, confusing notification.
        return {"error": f"O momento '{when}' já passou ({due:%d/%m/%Y %H:%M}). Indica uma hora no futuro."}

    conn = _conn()
    cur = conn.execute(
        "INSERT INTO reminders (text, due_at, created, fired, recurrence) VALUES (?,?,?,0,?)",
        (text.strip(), due.isoformat(timespec="seconds"), now.isoformat(timespec="seconds"), recurrence),
    )
    conn.commit()
    start_scheduler()
    logger.info(f"Lembrete #{cur.lastrowid} para {due:%Y-%m-%d %H:%M} ({recurrence or 'único'}): {text[:60]}")
    label = _recurrence_label(recurrence)
    message = f"Lembrete criado para {due:%d/%m às %H:%M}."
    if label:
        message = f"Lembrete criado, {label}. O primeiro é a {due:%d/%m às %H:%M}."
    return {
        "success": True,
        "id": cur.lastrowid,
        "text": text.strip(),
        "due_at": due.strftime("%Y-%m-%d %H:%M"),
        "recurrence": recurrence,
        "recurrence_label": label,
        "message": message,
    }


def list_reminders(include_fired: bool = False) -> dict:
    """Lista os lembretes pendentes (ou todos)."""
    query = "SELECT id, text, due_at, fired, recurrence FROM reminders"
    if not include_fired:
        query += " WHERE fired = 0"
    query += " ORDER BY due_at"
    rows = _conn().execute(query).fetchall()
    return {
        "reminders": [
            {"id": r[0], "text": r[1], "due_at": r[2], "fired": bool(r[3]),
             "recurrence": r[4], "recurrence_label": _recurrence_label(r[4])} for r in rows
        ],
        "count": len(rows),
    }


def cancel_reminder(reminder_id: int) -> dict:
    """Cancela (apaga) um lembrete pelo id."""
    conn = _conn()
    cur = conn.execute("DELETE FROM reminders WHERE id = ?", (int(reminder_id),))
    conn.commit()
    if cur.rowcount:
        return {"success": True, "message": f"Lembrete #{reminder_id} cancelado."}
    return {"success": False, "message": f"Não encontrei o lembrete #{reminder_id}."}


# ─── Scheduler ────────────────────────────────────────────────────────────────

def _fire(reminder_id: int, text: str, due_at: str | None = None,
          recurrence: str | None = None) -> bool:
    """Deliver one due reminder exactly once.

    The row is claimed (``fired = 1``) *before* anything is delivered. It used
    to be marked only at the end, so a failure in between -- the memory write
    raising, say -- left it pending and the scheduler delivered it again every
    20 seconds, phone notification included, forever.

    A recurring reminder is claimed by moving ``due_at`` to its next
    occurrence, guarded by the old value, so it too is delivered once per slot.
    """
    conn = _conn()
    following = None
    if recurrence in RECURRENCES and due_at:
        try:
            following = next_occurrence(datetime.fromisoformat(due_at), recurrence)
        except ValueError:
            following = None
    if following is not None:
        claimed = conn.execute(
            "UPDATE reminders SET due_at = ? WHERE id = ? AND fired = 0 AND due_at = ?",
            (following.isoformat(timespec="seconds"), reminder_id, due_at),
        ).rowcount
    else:
        claimed = conn.execute(
            "UPDATE reminders SET fired = 1 WHERE id = ? AND fired = 0", (reminder_id,)
        ).rowcount
    conn.commit()
    if not claimed:
        return False

    message = f"⏰ Lembrete: {text}"
    logger.info(message)
    due_label = ""
    if due_at:
        try:
            due_label = datetime.fromisoformat(due_at).strftime("%H:%M")
        except ValueError:
            due_label = ""
    _notify_locally({"id": reminder_id, "text": text, "due_label": due_label, "message": message})
    try:
        get_memory().save_message("assistant", message, {"source": "reminder", "reminder_id": reminder_id})
    except Exception as exc:
        logger.warning(f"Não consegui guardar o lembrete #{reminder_id} na memória: {exc}")

    webhook = get_setting("iot.notification_webhook", "")
    if webhook:
        try:
            asyncio.run(phone_notification(message, webhook))
        except Exception as exc:
            logger.warning(f"Notificação do lembrete falhou: {exc}")
    return True


def _loop():
    while not _stop.is_set():
        try:
            now = datetime.now().isoformat(timespec="seconds")
            due = _conn().execute(
                "SELECT id, text, due_at, recurrence FROM reminders WHERE fired = 0 AND due_at <= ?",
                (now,),
            ).fetchall()
            for reminder_id, text, due_at, recurrence in due:
                _fire(reminder_id, text, due_at, recurrence)
        except Exception as exc:
            logger.error(f"Scheduler de lembretes: {exc}")
        _stop.wait(CHECK_INTERVAL_SECONDS)


def start_scheduler() -> bool:
    """Arranca a thread daemon que dispara os lembretes (idempotente)."""
    global _scheduler
    if _scheduler and _scheduler.is_alive():
        return True
    _stop.clear()
    _scheduler = threading.Thread(target=_loop, name="nano-reminders", daemon=True)
    _scheduler.start()
    logger.info("Scheduler de lembretes activo.")
    return True


def stop_scheduler():
    _stop.set()


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "set_reminder",
            "description": (
                "Cria um lembrete/alarme, único ou recorrente. O Nano notifica quando "
                "chegar a hora, mesmo com a janela fechada."
            ),
            "parameters": {"type": "object", "required": ["text", "when"], "properties": {
                "text": {"type": "string", "description": "Do que lembrar"},
                "when": {"type": "string",
                          "description": ("'10 minutos', '18:30', 'amanhã às 9', 'sexta às 15h', "
                                          "'todos os dias às 8', 'todas as segundas às 9' "
                                          "ou '2026-01-02 18:30'")},
                "repeat": {"type": "string", "enum": ["daily", "weekdays", "weekly", "monthly"],
                           "description": "Repetição, só se o utilizador a pedir."},
            }},
        }},
        {"type": "function", "function": {
            "name": "list_reminders",
            "description": "Lista os lembretes pendentes do utilizador.",
            "parameters": {"type": "object", "properties": {
                "include_fired": {"type": "boolean", "default": False},
            }},
        }},
        {"type": "function", "function": {
            "name": "cancel_reminder",
            "description": "Cancela um lembrete pelo seu id.",
            "parameters": {"type": "object", "required": ["reminder_id"], "properties": {
                "reminder_id": {"type": "integer"},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "set_reminder":    lambda a: set_reminder(**a),
    "list_reminders":  lambda a: list_reminders(**a),
    "cancel_reminder": lambda a: cancel_reminder(**a),
}

# Starts with Nano so reminders created in other sessions still fire.
def start_background_services() -> bool:
    """Start the scheduler. Called by the bootstrap, never at import time.

    Starting threads at import time meant that any `import` of the plugin —
    a test, a syntax check — left a scheduler running with no way to stop it.
    """
    if not get_setting("reminders.enabled", True):
        return False
    return start_scheduler()


def stop_background_services() -> None:
    stop_scheduler()
