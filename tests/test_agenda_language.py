"""The Agenda and Modos pages in English, and reminders said in English.

NANO opens in English by default, so the Agenda's "When" field has to accept
"tomorrow at 9pm" as well as "amanhã às 21h", and the pages added for the
agenda, work modes and "which one?" choices must not be Portuguese-only.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pytest

from core import agenda, clarification, i18n
from plugins import reminders

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 18, 10, 0)  # a Wednesday


@pytest.fixture
def english(monkeypatch):
    monkeypatch.setenv("NANO_LANGUAGE", "en")
    i18n.set_override(None)
    yield
    i18n.set_override(None)


@pytest.mark.parametrize("said, expected", [
    ("10 minutes", datetime(2026, 11, 18, 10, 10)),
    ("in 2 hours", datetime(2026, 11, 18, 12, 0)),
    ("tomorrow at 9", datetime(2026, 11, 19, 9, 0)),
    ("tomorrow at 9pm", datetime(2026, 11, 19, 21, 0)),
    ("friday at 3pm", datetime(2026, 11, 20, 15, 0)),
    ("next monday morning", datetime(2026, 11, 23, 9, 0)),
    ("at 6:30 pm", datetime(2026, 11, 18, 18, 30)),
    ("noon", datetime(2026, 11, 18, 12, 0)),
])
def test_reminder_times_said_in_english(said, expected):
    assert reminders.parse_when(said, NOW) == expected


@pytest.mark.parametrize("said, expected", [
    ("amanhã às 9", datetime(2026, 11, 19, 9, 0)),
    ("sexta às 15h", datetime(2026, 11, 20, 15, 0)),
    ("às 9 da noite", datetime(2026, 11, 18, 21, 0)),
    ("10 minutos", datetime(2026, 11, 18, 10, 10)),
])
def test_portuguese_times_are_unchanged(said, expected):
    assert reminders.parse_when(said, NOW) == expected


@pytest.mark.parametrize("said, rest, code", [
    ("every day at 8", "às 8", "daily"),
    ("every weekday at 9am", "às 09:00", "weekdays"),
    ("every monday at 10", "segunda às 10", "weekly"),
])
def test_recurring_reminders_said_in_english(said, rest, code):
    assert reminders.parse_recurring(said) == (rest, code)


def test_agenda_messages_follow_the_language(english, monkeypatch):
    monkeypatch.setattr(agenda, "_plugin", lambda name: None)
    assert agenda.create_reminder("x", "10 minutes")["message"] == "Reminders are turned off."


def test_a_choice_reply_follows_the_language(english):
    question = clarification.from_tool_result("pc_window_focus", {"output": {
        "status": "ambiguous", "candidates": [
            {"window_id": 1, "title": "relatório.docx", "process": "WINWORD"},
            {"window_id": 2, "title": "relatório.pdf", "process": "Acrobat"},
        ]}})
    assert question["question"] == "Which one?"
    assert question["options"][0]["reply"].startswith("I mean the window")


@pytest.mark.parametrize("page", ["AgendaPage.tsx", "WorkModesPage.tsx"])
def test_the_new_pages_go_through_the_translator(page):
    source = (ROOT / "frontend" / "components" / page).read_text(encoding="utf-8")
    assert 'from "../lib/i18n"' in source
    # A literal attribute with words in it ("18:30" is fine) was not translated.
    leftovers = re.findall(r'\b(?:label|title|placeholder|hint|aria-label)="([^"]*[A-Za-zÀ-ú]{2,}[^"]*)"', source)
    assert not leftovers, f"{page} still has literal text: {leftovers}"
