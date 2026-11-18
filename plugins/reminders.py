"""
Nano Plugin: Reminders
Persistent reminders and alarms (SQLite) with a lightweight scheduler on a
daemon thread. When a reminder is due it is shown on the PC (a Windows
notification, through whatever notifier the host registered), sent to the
phone (webhook, if configured) and saved to memory so Nano can refer to it
later.
"""

import asyncio
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


def parse_when(when: str) -> datetime | None:
    """Aceita ISO ('2026-01-02 18:30'), 'HH:MM' (hoje/amanhã) ou '10 minutos'."""
    when = (when or "").strip()
    if not when:
        return None

    match = _RELATIVE.match(when)
    if match:
        amount, unit = int(match.group(1)), match.group(2).lower()
        return datetime.now() + timedelta(seconds=amount * _UNIT_SECONDS[unit])

    if re.fullmatch(r"\d{1,2}:\d{2}", when):
        hour, minute = (int(p) for p in when.split(":"))
        target = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        return target if target > datetime.now() else target + timedelta(days=1)

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M", "%d/%m %H:%M"):
        try:
            if fmt == "%d/%m %H:%M":
                # Parse with the year attached: strptime defaults to 1900,
                # which is not a leap year, so "29/02 10:00" failed outright.
                now = datetime.now()
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
    return None


# ─── Ferramentas ──────────────────────────────────────────────────────────────

def set_reminder(text: str, when: str) -> dict:
    """Cria um lembrete. 'when' aceita '10 minutos', '18:30' ou '2026-01-02 18:30'."""
    due = parse_when(when)
    if due is None:
        return {"error": f"Não percebi o momento '{when}'. Usa '10 minutos', '18:30' ou '2026-01-02 18:30'."}
    if not (text or "").strip():
        return {"error": "Preciso de saber do que te devo lembrar."}
    if due <= datetime.now():
        # An absolute time in the past used to be stored as due and fired on
        # the scheduler's next pass, i.e. "lembra-me às 2026-01-02 18:30" a
        # day late produced an immediate, confusing notification.
        return {"error": f"O momento '{when}' já passou ({due:%d/%m/%Y %H:%M}). Indica uma hora no futuro."}

    conn = _conn()
    cur = conn.execute(
        "INSERT INTO reminders (text, due_at, created, fired) VALUES (?,?,?,0)",
        (text.strip(), due.isoformat(timespec="seconds"), datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    start_scheduler()
    logger.info(f"Lembrete #{cur.lastrowid} para {due:%Y-%m-%d %H:%M}: {text[:60]}")
    return {
        "success": True,
        "id": cur.lastrowid,
        "text": text.strip(),
        "due_at": due.strftime("%Y-%m-%d %H:%M"),
        "message": f"Lembrete criado para {due:%d/%m às %H:%M}.",
    }


def list_reminders(include_fired: bool = False) -> dict:
    """Lista os lembretes pendentes (ou todos)."""
    query = "SELECT id, text, due_at, fired FROM reminders"
    if not include_fired:
        query += " WHERE fired = 0"
    query += " ORDER BY due_at"
    rows = _conn().execute(query).fetchall()
    return {
        "reminders": [
            {"id": r[0], "text": r[1], "due_at": r[2], "fired": bool(r[3])} for r in rows
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

def _fire(reminder_id: int, text: str, due_at: str | None = None) -> bool:
    """Deliver one due reminder exactly once.

    The row is claimed (``fired = 1``) *before* anything is delivered. It used
    to be marked only at the end, so a failure in between -- the memory write
    raising, say -- left it pending and the scheduler delivered it again every
    20 seconds, phone notification included, forever.
    """
    conn = _conn()
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
                "SELECT id, text, due_at FROM reminders WHERE fired = 0 AND due_at <= ?", (now,)
            ).fetchall()
            for reminder_id, text, due_at in due:
                _fire(reminder_id, text, due_at)
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
                "Cria um lembrete/alarme. O Nano notifica quando chegar a hora, "
                "mesmo com a janela fechada."
            ),
            "parameters": {"type": "object", "required": ["text", "when"], "properties": {
                "text": {"type": "string", "description": "Do que lembrar"},
                "when": {"type": "string",
                          "description": "'10 minutos', '2 horas', '18:30' ou '2026-01-02 18:30'"},
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
