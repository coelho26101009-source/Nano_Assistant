"""
Nano Plugin: Calendar
Local calendar in SQLite (always available, no accounts or network needed)
with optional .ics file import and optional Google Calendar reading.

Google Calendar (opcional):
  pip install google-api-python-client google-auth-oauthlib
  config/settings.yaml → calendar.google_enabled: true
  Credenciais OAuth em data/google_credentials.json (token gravado ao lado).
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.app_paths import DATA_DIR
from core.config import get_setting
from core.memory import get_memory

logger = logging.getLogger("helios.plugins.calendar")

DATA_DIR.mkdir(parents=True, exist_ok=True)
GOOGLE_CREDS = DATA_DIR / "google_credentials.json"
GOOGLE_TOKEN = DATA_DIR / "google_token.json"
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]


def _conn():
    conn = get_memory().conn
    conn.execute("""
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            start_at TEXT NOT NULL,
            end_at TEXT,
            location TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            source TEXT NOT NULL DEFAULT 'local'
        )
    """)
    conn.commit()
    return conn


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def add_event(title: str, start: str, end: str | None = None,
              location: str = "", notes: str = "") -> dict:
    """Adiciona um evento ao calendário local."""
    start_dt = _parse_dt(start)
    if start_dt is None:
        return {"error": f"Data inválida: '{start}'. Usa '2026-01-02 18:30' ou '02/01/2026 18:30'."}
    end_dt = _parse_dt(end) if end else start_dt + timedelta(hours=1)

    conn = _conn()
    cur = conn.execute(
        "INSERT INTO calendar_events (title, start_at, end_at, location, notes, source) VALUES (?,?,?,?,?, 'local')",
        (title.strip(), start_dt.isoformat(timespec="minutes"), end_dt.isoformat(timespec="minutes") if end_dt else None, location, notes),
    )
    conn.commit()
    return {"success": True, "id": cur.lastrowid, "title": title, "start": start_dt.strftime("%Y-%m-%d %H:%M"), "message": f"Evento '{title}' marcado para {start_dt:%d/%m às %H:%M}."}


def list_events(days: int = 7, include_google: bool = True) -> dict:
    """Lista os eventos dos próximos N dias (locais + Google, se configurado)."""
    now = datetime.now()
    until = now + timedelta(days=int(days))
    rows = _conn().execute(
        "SELECT id, title, start_at, end_at, location, source FROM calendar_events WHERE start_at BETWEEN ? AND ? ORDER BY start_at",
        (now.isoformat(timespec="minutes"), until.isoformat(timespec="minutes")),
    ).fetchall()
    events = [{"id": r[0], "title": r[1], "start": r[2], "end": r[3], "location": r[4], "source": r[5]} for r in rows]

    google_error = None
    if include_google and get_setting("calendar.google_enabled", False):
        google_events, google_error = _google_events(now, until)
        events.extend(google_events)
        events.sort(key=lambda e: e["start"])

    result = {"events": events, "count": len(events), "window_days": int(days)}
    if google_error:
        result["google_error"] = google_error
    return result


def delete_event(event_id: int) -> dict:
    """Apaga um evento local pelo id."""
    conn = _conn()
    cur = conn.execute("DELETE FROM calendar_events WHERE id = ? AND source = 'local'", (int(event_id),))
    conn.commit()
    if cur.rowcount:
        return {"success": True, "message": f"Evento #{event_id} apagado."}
    return {"success": False, "message": f"Não encontrei o evento local #{event_id}."}


def import_ics(file_path: str) -> dict:
    """Importa eventos de um ficheiro .ics para o calendário local."""
    path = Path(file_path)
    if not path.exists():
        return {"error": f"Ficheiro não encontrado: {file_path}"}
    imported = 0
    title = start = end = location = None
    conn = _conn()
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if line == "BEGIN:VEVENT":
                title = start = end = location = None
            elif line.startswith("SUMMARY:"):
                title = line[len("SUMMARY:"):]
            elif line.startswith("DTSTART"):
                start = _parse_ics_dt(line.split(":", 1)[-1])
            elif line.startswith("DTEND"):
                end = _parse_ics_dt(line.split(":", 1)[-1])
            elif line.startswith("LOCATION:"):
                location = line[len("LOCATION:"):]
            elif line == "END:VEVENT" and title and start:
                conn.execute(
                    "INSERT INTO calendar_events (title, start_at, end_at, location, source) VALUES (?,?,?,?, 'ics')",
                    (title, start.isoformat(timespec="minutes"), end.isoformat(timespec="minutes") if end else None, location or ""),
                )
                imported += 1
        conn.commit()
    except Exception as exc:
        return {"error": f"Falha a importar '{path.name}': {exc}"}
    return {"success": True, "imported": imported, "file": path.name, "message": f"Importei {imported} eventos de '{path.name}'."}


def _parse_ics_dt(value: str) -> datetime | None:
    value = value.strip().rstrip("Z")
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%d"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    return None


def _google_events(start: datetime, end: datetime) -> tuple[list[dict], str | None]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        return [], "Google Calendar pedido mas as bibliotecas opcionais estão em falta."
    if not GOOGLE_CREDS.exists():
        return [], "Credenciais OAuth do Google Calendar em falta."
    try:
        creds = None
        if GOOGLE_TOKEN.exists():
            creds = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN), GOOGLE_SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(GOOGLE_CREDS), GOOGLE_SCOPES)
                creds = flow.run_local_server(port=0)
            GOOGLE_TOKEN.write_text(creds.to_json(), encoding="utf-8")
        service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        items = service.events().list(calendarId="primary", timeMin=start.astimezone().isoformat(), timeMax=end.astimezone().isoformat(), singleEvents=True, orderBy="startTime").execute().get("items", [])
        return [{"id": item.get("id"), "title": item.get("summary", "(sem título)"), "start": item["start"].get("dateTime", item["start"].get("date", "")), "end": item.get("end", {}).get("dateTime", item.get("end", {}).get("date", "")), "location": item.get("location", ""), "source": "google"} for item in items], None
    except Exception:
        logger.exception("Google Calendar falhou")
        return [], "Google Calendar falhou. Verifica a configuração do calendário."


def get_tools() -> list[dict]:
    """Return function specifications for the calendar plugin."""
    return [
        {
            "type": "function",
            "function": {
                "name": "calendar_add_event",
                "description": "Marca um evento no calendário local do Simão.",
                "parameters": {
                    "type": "object",
                    "required": ["title", "start"],
                    "properties": {
                        "title": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "location": {"type": "string"},
                        "notes": {"type": "string"}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "calendar_list_events",
                "description": "Lista os eventos dos próximos dias (calendário local e Google, se activo).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "days": {"type": "integer", "default": 7},
                        "include_google": {"type": "boolean", "default": True}
                    }
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "calendar_delete_event",
                "description": "Apaga um evento local do calendário pelo id.",
                "parameters": {
                    "type": "object",
                    "required": ["event_id"],
                    "properties": {"event_id": {"type": "integer"}}
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "calendar_import_ics",
                "description": "Importa eventos de um ficheiro .ics para o calendário local.",
                "parameters": {
                    "type": "object",
                    "required": ["file_path"],
                    "properties": {"file_path": {"type": "string"}}
                }
            }
        }
    ]


TOOL_HANDLERS: dict = {
    "calendar_add_event": lambda a: add_event(**a),
    "calendar_list_events": lambda a: list_events(**a),
    "calendar_delete_event": lambda a: delete_event(**a),
    "calendar_import_ics": lambda a: import_ics(**a),
}
