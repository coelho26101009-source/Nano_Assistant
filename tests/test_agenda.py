"""The Agenda page's backend: reminders and events through core.agenda."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest

from core import agenda, plugin_loader
from plugins import calendar as cal
from plugins import reminders


class _FakeMemory:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)

    def save_message(self, *args, **kwargs):
        pass


@pytest.fixture
def plugins(monkeypatch):
    fake = _FakeMemory()
    monkeypatch.setattr(reminders, "get_memory", lambda: fake)
    monkeypatch.setattr(cal, "get_memory", lambda: fake)
    monkeypatch.setattr(reminders, "start_scheduler", lambda: True)
    monkeypatch.setattr(cal, "get_setting", lambda key, default=None: False if "google" in key else default)
    monkeypatch.setattr(plugin_loader, "_loaded_plugins", {"reminders": reminders, "calendar": cal})
    return fake


def _future(hours: int = 2) -> str:
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


def test_overview_lists_pending_reminders_and_upcoming_events(plugins):
    assert agenda.create_reminder("beber água", "10 minutos")["ok"] is True
    assert agenda.create_event("Dentista", _future(), None, "Lisboa")["ok"] is True
    view = agenda.overview(7)
    assert view["ok"] is True and view["windowDays"] == 7
    assert view["available"] == {"reminders": True, "calendar": True}
    assert [r["text"] for r in view["reminders"]] == ["beber água"]
    assert [e["title"] for e in view["events"]] == ["Dentista"]
    assert view["events"][0]["location"] == "Lisboa"


def test_reminders_can_be_deleted_from_the_page(plugins):
    created = agenda.create_reminder("x", "5 minutos")
    assert agenda.delete_reminder(created["id"])["ok"] is True
    assert agenda.overview()["reminders"] == []
    assert agenda.delete_reminder(created["id"])["ok"] is False


def test_events_can_be_deleted_from_the_page(plugins):
    created = agenda.create_event("Reunião", _future())
    assert agenda.delete_event(created["id"])["ok"] is True
    assert agenda.overview()["events"] == []


@pytest.mark.parametrize("text,when,error", [
    ("", "10 minutos", "empty_text"),
    ("   ", "10 minutos", "empty_text"),
    ("x", "", "empty_when"),
    ("x", "um dia destes", "invalid_when"),
    ("x", "2020-01-01 10:00", "invalid_when"),
])
def test_bad_reminders_are_refused_with_a_reason(plugins, text, when, error):
    result = agenda.create_reminder(text, when)
    assert result["ok"] is False and result["error"] == error and result["message"]


def test_bad_events_are_refused_with_a_reason(plugins):
    assert agenda.create_event("", _future())["error"] == "empty_title"
    assert agenda.create_event("x", "amanhã")["error"] == "invalid_start"
    assert agenda.create_event("x", _future(5), _future(2))["error"] == "end_before_start"
    assert agenda.create_event("x", _future(), "depois")["error"] == "invalid_end"


def test_ids_are_validated(plugins):
    for bad in (None, "abc", -1, 0, "1; DROP TABLE reminders"):
        assert agenda.delete_reminder(bad)["error"] == "invalid_id"
        assert agenda.delete_event(bad)["error"] == "invalid_id"


def test_long_input_is_bounded(plugins):
    created = agenda.create_reminder("a" * 5000, "10 minutos")
    assert created["ok"] is True
    assert len(agenda.overview()["reminders"][0]["text"]) == agenda.MAX_TEXT


def test_a_missing_plugin_is_reported_not_crashed(monkeypatch):
    monkeypatch.setattr(plugin_loader, "_loaded_plugins", {})
    view = agenda.overview()
    assert view["available"] == {"reminders": False, "calendar": False}
    assert agenda.create_reminder("x", "10 minutos")["error"] == "reminders_unavailable"
    assert agenda.create_event("x", _future())["error"] == "calendar_unavailable"


def test_the_window_is_clamped(plugins):
    assert agenda.overview(0)["windowDays"] == 1
    assert agenda.overview(10_000)["windowDays"] == agenda.MAX_DAYS
    assert agenda.overview("x")["windowDays"] == 30


def test_main_exposes_the_agenda_to_the_ui():
    from core import main

    for name in ("get_agenda", "agenda_create_reminder", "agenda_delete_reminder",
                 "agenda_create_event", "agenda_delete_event"):
        assert callable(getattr(main, name))


def test_the_agenda_is_not_a_model_tool():
    tool_names = {t["function"]["name"] for t in reminders.get_tools() + cal.get_tools()}
    assert not {n for n in tool_names if n.startswith("agenda_")}
