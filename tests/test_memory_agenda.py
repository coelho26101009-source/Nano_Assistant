"""Memory linked to the agenda: "tens teste de matemática amanhã, queres rever?".

The evening before an event worth getting ready for, NANO offers to help and
recalls what it remembers about it; the morning briefing adds one remembered
line under a matching event. All local, no model.
"""
from __future__ import annotations

from datetime import datetime

from core import proactive

EVENING = datetime(2026, 11, 19, 19, 0)
FACTS = ["Tenho dificuldades em matemática, sobretudo em funções",
         "O professor de matemática é o Rui",
         "Gosto de pizza", "Para amanhã nada"]


def _scheduler(events, now, facts=FACTS, **settings):
    emitted = []
    base = {"briefing": False, "event_alerts": False, "prep": True, "briefing_time": "08:00",
            "lead_minutes": 15, "name": ""}
    base.update(settings)
    scheduler = proactive.ProactiveScheduler(
        overview=lambda: {"events": events, "reminders": []}, emit=emitted.append,
        state_path=None, settings=lambda: base, now=lambda: now, facts=lambda: facts)
    return scheduler, emitted


def test_related_facts_share_a_meaningful_word():
    found = proactive.related_facts("Teste de Matemática", FACTS)
    assert found and all("matemática" in f for f in found) and len(found) <= 2
    assert proactive.related_facts("Jantar", FACTS) == []
    # Generic words never link a fact to an event.
    assert proactive.related_facts("Reunião para amanhã", FACTS) == []


def test_which_events_need_getting_ready():
    for title in ("Teste de matemática", "Exame de Física", "Entrevista na Sonae", "Job interview",
                  "Apresentação do projeto", "Voo para Londres"):
        assert proactive.needs_preparation(title), title
    for title in ("Jantar com a Ana", "Aniversário do Rui", "Testemunho"):
        assert not proactive.needs_preparation(title), title


def test_the_evening_before_nano_offers_to_help_and_recalls_what_it_knows():
    events = [{"id": 7, "title": "Teste de Matemática", "start": "2026-11-20T09:00"}]
    scheduler, emitted = _scheduler(events, EVENING)
    out = scheduler.tick()
    assert len(out) == 1 and out[0]["kind"] == "prep"
    assert "09:00" in out[0]["text"] and "Teste de Matemática" in out[0]["text"]
    assert "funções" in out[0]["text"]
    assert scheduler.tick() == []                  # once per event
    assert emitted == out


def test_not_before_the_evening_and_not_for_ordinary_events():
    events = [{"id": 7, "title": "Teste de Matemática", "start": "2026-11-20T09:00"},
              {"id": 8, "title": "Jantar", "start": "2026-11-20T20:00"}]
    scheduler, _ = _scheduler(events, EVENING.replace(hour=15))
    assert scheduler.tick() == []
    scheduler, _ = _scheduler(events, EVENING)
    assert [n["kind"] for n in scheduler.tick()] == ["prep"]


def test_switched_off_it_stays_quiet():
    events = [{"id": 7, "title": "Exame", "start": "2026-11-20T09:00"}]
    scheduler, _ = _scheduler(events, EVENING, prep=False)
    assert scheduler.tick() == []


def test_without_memory_the_offer_still_comes_without_recall():
    events = [{"id": 7, "title": "Teste de Matemática", "start": "2026-11-20T09:00"}]
    scheduler, _ = _scheduler(events, EVENING, facts=[])
    out = scheduler.tick()
    assert out and "\n•" not in out[0]["text"]


def test_the_briefing_adds_one_remembered_line_under_a_matching_event():
    morning = datetime(2026, 11, 20, 8, 5)
    overview = {"events": [{"id": 7, "title": "Teste de Matemática", "start": "2026-11-20T09:00"},
                           {"id": 8, "title": "Almoço", "start": "2026-11-20T13:00"}], "reminders": []}
    text = proactive.build_briefing(overview, morning, facts=FACTS)["text"]
    assert text.count("↳") == 1 and "matemática" in text.split("↳", 1)[1]
