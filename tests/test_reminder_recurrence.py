"""Recurring reminders and natural Portuguese time phrases ("amanhã às 9")."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from core import agenda, plugin_loader
from plugins import reminders

# Wednesday, 18 November 2026, 10:00.
NOW = datetime(2026, 11, 18, 10, 0)


class _FakeMemory:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)

    def save_message(self, *args, **kwargs):
        pass


@pytest.fixture
def memory(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(reminders, "get_memory", lambda: fake)
    monkeypatch.setattr(reminders, "start_scheduler", lambda: True)
    monkeypatch.setattr(reminders, "get_setting", lambda key, default=None: default if key != "iot.notification_webhook" else "")
    monkeypatch.setattr(reminders, "_notifiers", [])
    monkeypatch.setattr(plugin_loader, "_loaded_plugins", {"reminders": reminders})
    return fake


@pytest.mark.parametrize("phrase, expected", [
    ("amanhã às 9", datetime(2026, 11, 19, 9, 0)),
    ("amanha as 9", datetime(2026, 11, 19, 9, 0)),
    ("sexta às 15h", datetime(2026, 11, 20, 15, 0)),
    ("depois de amanhã", datetime(2026, 11, 20, 9, 0)),
    ("hoje às 18", datetime(2026, 11, 18, 18, 0)),
    ("amanhã de manhã", datetime(2026, 11, 19, 9, 0)),
    ("quinta 3 da tarde", datetime(2026, 11, 19, 15, 0)),
    ("segunda às 9 e meia", datetime(2026, 11, 23, 9, 30)),
    ("quarta às 9", datetime(2026, 11, 25, 9, 0)),   # today's 9:00 has passed
    ("9h30", datetime(2026, 11, 19, 9, 30)),
    ("18:30", datetime(2026, 11, 18, 18, 30)),
    ("10 minutos", NOW + timedelta(minutes=10)),
])
def test_natural_phrases_resolve_to_the_expected_moment(phrase, expected):
    assert reminders.parse_when(phrase, NOW) == expected


@pytest.mark.parametrize("phrase", ["", "qualquer coisa", "amanhã às 27", "às 9 e 70"])
def test_nonsense_is_not_guessed(phrase):
    assert reminders.parse_when(phrase, NOW) is None


@pytest.mark.parametrize("phrase, rest, code", [
    ("todos os dias às 8", "às 8", "daily"),
    ("dias úteis às 7h30", "às 7h30", "weekdays"),
    ("todas as semanas às 10", "às 10", "weekly"),
    ("todas as segundas às 9", "segunda às 9", "weekly"),
    ("todos os meses às 10", "às 10", "monthly"),
    ("amanhã às 9", "amanhã às 9", None),
])
def test_recurring_phrases_are_split_into_time_and_rule(phrase, rest, code):
    assert reminders.parse_recurring(phrase) == (rest, code)


def test_next_occurrence_follows_each_rule():
    friday = datetime(2026, 11, 20, 8, 0)
    assert reminders.next_occurrence(friday, "daily", friday) == datetime(2026, 11, 21, 8, 0)
    assert reminders.next_occurrence(friday, "weekdays", friday) == datetime(2026, 11, 23, 8, 0)
    assert reminders.next_occurrence(friday, "weekly", friday) == datetime(2026, 11, 27, 8, 0)
    jan31 = datetime(2027, 1, 31, 9, 0)
    assert reminders.next_occurrence(jan31, "monthly", jan31) == datetime(2027, 2, 28, 9, 0)


def test_missed_occurrences_are_skipped_not_fired_in_a_burst():
    due = datetime(2026, 11, 1, 8, 0)
    assert reminders.next_occurrence(due, "daily", NOW) == datetime(2026, 11, 19, 8, 0)


def test_monthly_series_keeps_its_day_after_a_short_month():
    jan31 = datetime(2027, 1, 31, 9, 0)
    feb = reminders.next_occurrence(jan31, "monthly", jan31)
    # The series is anchored on the stored due date, so after February it is
    # the 28th from then on; it never drifts further than the month allows.
    assert reminders.next_occurrence(feb, "monthly", feb).month == 3


def test_unknown_repeat_values_are_refused(memory):
    result = reminders.set_reminder("x", "18:30", repeat="de hora a hora")
    assert "error" in result


def test_a_recurring_reminder_is_stored_with_its_rule(memory):
    created = reminders.set_reminder("tomar o comprimido", "todos os dias às 8")
    assert created["success"] and created["recurrence"] == "daily"
    assert "todos os dias" in created["message"]
    listed = reminders.list_reminders()["reminders"]
    assert listed[0]["recurrence"] == "daily"
    assert listed[0]["recurrence_label"] == "todos os dias"


def test_a_recurring_series_without_a_time_asks_for_one(memory):
    assert "A que horas" in reminders.set_reminder("x", "todos os dias")["error"]


def test_the_repeat_argument_works_with_a_plain_time(memory):
    created = reminders.set_reminder("stand-up", "9:30", repeat="weekdays")
    assert created["recurrence"] == "weekdays"
    due = datetime.strptime(created["due_at"], "%Y-%m-%d %H:%M")
    assert due.weekday() < 5


def test_firing_a_recurring_reminder_moves_it_to_the_next_slot(memory):
    seen = []
    reminders.register_notifier(seen.append)
    created = reminders.set_reminder("água", "todos os dias às 8")
    conn = reminders._conn()
    past = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0)
    conn.execute("UPDATE reminders SET due_at = ? WHERE id = ?", (past.isoformat(), created["id"]))
    conn.commit()

    assert reminders._fire(created["id"], "água", past.isoformat(), "daily") is True
    # A second scheduler pass with the stale due_at does not deliver it twice.
    assert reminders._fire(created["id"], "água", past.isoformat(), "daily") is False
    assert len(seen) == 1

    row = conn.execute("SELECT due_at, fired FROM reminders WHERE id = ?", (created["id"],)).fetchone()
    assert row[1] == 0
    assert datetime.fromisoformat(row[0]) == past + timedelta(days=1)


def test_a_one_off_reminder_still_fires_once(memory):
    created = reminders.set_reminder("x", "10 minutos")
    assert reminders._fire(created["id"], "x") is True
    assert reminders._fire(created["id"], "x") is False
    assert reminders.list_reminders()["count"] == 0


def test_old_databases_gain_the_recurrence_column(memory):
    memory.conn.execute("CREATE TABLE reminders (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, "
                        "due_at TEXT, created TEXT, fired INTEGER DEFAULT 0)")
    memory.conn.commit()
    reminders._conn()
    columns = {row[1] for row in memory.conn.execute("PRAGMA table_info(reminders)")}
    assert "recurrence" in columns


def test_the_agenda_page_can_create_a_recurring_reminder(memory):
    result = agenda.create_reminder("reunião", "segunda às 9", "weekly")
    assert result["ok"] and result["recurrence"] == "weekly"
    assert agenda.create_reminder("x", "18:30", "hourly")["error"] == "invalid_repeat"


def test_the_tool_schema_offers_the_repeat_rules():
    schema = next(t for t in reminders.get_tools() if t["function"]["name"] == "set_reminder")
    props = schema["function"]["parameters"]["properties"]
    assert set(props["repeat"]["enum"]) == set(reminders.RECURRENCES)
    assert "amanhã às 9" in props["when"]["description"]
