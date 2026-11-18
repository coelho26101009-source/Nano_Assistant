"""Reminders and calendar plugins: date handling, delivery and .ics import."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from plugins import calendar as cal
from plugins import reminders


class _FakeMemory:
    def __init__(self, *, fail_save: bool = False):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.saved: list[str] = []
        self.fail_save = fail_save

    def save_message(self, role, content, metadata=None):
        if self.fail_save:
            raise RuntimeError("disk full")
        self.saved.append(content)


@pytest.fixture
def memory(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(reminders, "get_memory", lambda: fake)
    monkeypatch.setattr(cal, "get_memory", lambda: fake)
    monkeypatch.setattr(reminders, "start_scheduler", lambda: True)
    monkeypatch.setattr(reminders, "get_setting", lambda key, default=None: "" if "webhook" in key else default)
    return fake


# ─── reminders ────────────────────────────────────────────────────────────────

def test_a_reminder_in_the_past_is_refused(memory):
    result = reminders.set_reminder("pagar a luz", "2020-01-02 18:30")
    assert "error" in result and "já passou" in result["error"]
    assert reminders.list_reminders()["count"] == 0


def test_day_month_without_year_rolls_to_next_year_when_already_past():
    yesterday = datetime.now() - timedelta(days=1)
    parsed = reminders.parse_when(yesterday.strftime("%d/%m %H:%M"))
    assert parsed is not None and parsed > datetime.now()
    assert parsed.year == yesterday.year + 1


def test_a_due_reminder_is_delivered_once_even_if_saving_it_fails(memory):
    created = reminders.set_reminder("ligar à avó", "10 minutos")
    memory.fail_save = True
    assert reminders._fire(created["id"], "ligar à avó") is True
    # Already claimed: a second pass of the scheduler must not deliver it again.
    assert reminders._fire(created["id"], "ligar à avó") is False
    assert reminders.list_reminders()["count"] == 0


def test_a_due_reminder_is_saved_to_memory(memory):
    created = reminders.set_reminder("beber água", "5 minutos")
    reminders._fire(created["id"], "beber água")
    assert memory.saved == ["⏰ Lembrete: beber água"]


# ─── calendar .ics import ─────────────────────────────────────────────────────

ICS = (
    "BEGIN:VCALENDAR\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY;LANGUAGE=pt:Reunião de planeamento do projeto com a equipa de\r\n"
    "  design\\, marketing e vendas\r\n"
    "DTSTART:20300115T090000Z\r\n"
    "DTEND:20300115T100000Z\r\n"
    "LOCATION:Sala 2\\; piso 1\r\n"
    "END:VEVENT\r\n"
    "BEGIN:VEVENT\r\n"
    "SUMMARY:Dia livre\r\n"
    "DTSTART;VALUE=DATE:20300120\r\n"
    "END:VEVENT\r\n"
    "END:VCALENDAR\r\n"
)


def test_ics_import_unfolds_unescapes_and_converts_utc(memory, tmp_path):
    path = tmp_path / "agenda.ics"
    path.write_text(ICS, encoding="utf-8")
    result = cal.import_ics(str(path))
    assert result["imported"] == 2

    rows = memory.conn.execute(
        "SELECT title, start_at, location FROM calendar_events ORDER BY start_at").fetchall()
    title, start_at, location = rows[0]
    assert title == "Reunião de planeamento do projeto com a equipa de design, marketing e vendas"
    assert location == "Sala 2; piso 1"
    expected = datetime(2030, 1, 15, 9, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    assert start_at == expected.isoformat(timespec="minutes")
    assert rows[1][0] == "Dia livre" and rows[1][1] == "2030-01-20T00:00"


def test_importing_the_same_ics_twice_does_not_duplicate(memory, tmp_path):
    path = tmp_path / "agenda.ics"
    path.write_text(ICS, encoding="utf-8")
    cal.import_ics(str(path))
    again = cal.import_ics(str(path))
    assert again["imported"] == 0 and again["skipped"] == 2
    assert memory.conn.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 2


def test_imported_events_can_be_deleted(memory, tmp_path):
    path = tmp_path / "agenda.ics"
    path.write_text(ICS, encoding="utf-8")
    cal.import_ics(str(path))
    event_id = memory.conn.execute("SELECT id FROM calendar_events LIMIT 1").fetchone()[0]
    assert cal.delete_event(event_id)["success"] is True
