"""Proactivity: NANO speaks first -- the morning briefing and event heads-ups.

The scheduler is driven with a fake clock and a fake agenda, so each test
states the time, what is on the agenda and what NANO must (or must not) say.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from core import proactive

DAY = datetime(2026, 11, 20, 7, 0)


def _agenda(events=(), reminders=()):
    return {"events": list(events), "reminders": list(reminders)}


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


def _settings(**changes):
    base = {"briefing": True, "briefing_time": "08:00", "event_alerts": True,
            "lead_minutes": 15, "name": "Simão"}
    base.update(changes)
    return lambda: base


@pytest.fixture
def make(tmp_path):
    def build(agenda=None, clock=None, settings=None):
        emitted = []
        clock = clock or Clock(DAY)
        scheduler = proactive.ProactiveScheduler(
            overview=lambda: agenda if agenda is not None else _agenda(),
            emit=emitted.append, state_path=tmp_path / "proactive_state.json",
            settings=settings or _settings(), now=clock)
        return scheduler, emitted, clock
    return build


# ── the morning briefing ──────────────────────────────────────────────────

def test_the_briefing_comes_once_at_the_chosen_time(make):
    agenda = _agenda(
        events=[{"id": 1, "title": "Reunião", "start": "2026-11-20T10:00:00"}],
        reminders=[{"id": 2, "text": "beber água", "due_at": "2026-11-20T15:00:00", "fired": False},
                   {"id": 3, "text": "já foi", "due_at": "2026-11-20T06:00:00", "fired": True}])
    scheduler, emitted, clock = make(agenda)
    assert scheduler.tick() == []                     # 07:00, too early
    clock.moment = DAY.replace(hour=8, minute=1)
    [notice] = scheduler.tick()
    assert notice["kind"] == "briefing"
    assert "Simão" in notice["text"]
    assert "10:00 Reunião" in notice["text"] and "15:00 beber água" in notice["text"]
    assert "já foi" not in notice["text"]
    clock.moment = DAY.replace(hour=9)
    assert scheduler.tick() == []                     # once a day
    assert emitted == [notice]


def test_a_pc_switched_on_later_still_gets_it_but_not_at_night(make):
    scheduler, _, clock = make(clock=Clock(DAY.replace(hour=11)))
    assert scheduler.tick()[0]["kind"] == "briefing"
    late, _, late_clock = make(clock=Clock(DAY.replace(hour=22)))
    late._state["last_briefing"] = ""
    assert late.tick() == []


def test_the_day_already_briefed_survives_a_restart(make, tmp_path):
    scheduler, _, clock = make(clock=Clock(DAY.replace(hour=8, minute=30)))
    assert scheduler.tick()
    again, emitted, _ = make(clock=Clock(DAY.replace(hour=9)))
    assert again.tick() == [] and emitted == []


def test_an_empty_day_says_so(make):
    scheduler, _, _ = make(clock=Clock(DAY.replace(hour=8)))
    assert "nada na agenda" in scheduler.tick()[0]["text"]


def test_tomorrow_is_counted(make):
    agenda = _agenda(events=[{"id": 9, "title": "Dentista", "start": "2026-11-21T09:00:00"}])
    scheduler, _, _ = make(agenda, clock=Clock(DAY.replace(hour=8)))
    assert "Amanhã tens 1" in scheduler.tick()[0]["text"]


def test_switched_off_means_silent(make):
    scheduler, emitted, _ = make(clock=Clock(DAY.replace(hour=8)),
                                 settings=_settings(briefing=False, event_alerts=False))
    assert scheduler.tick() == [] and emitted == []


def test_on_demand_briefing_does_not_use_up_the_day(make):
    scheduler, _, clock = make(clock=Clock(DAY.replace(hour=8)))
    assert scheduler.briefing_now()["kind"] == "briefing"
    assert scheduler.tick()[0]["kind"] == "briefing"


# ── event heads-up ────────────────────────────────────────────────────────

def test_an_event_is_announced_once_inside_the_lead_time(make):
    agenda = _agenda(events=[{"id": 5, "title": "Chamada", "start": "2026-11-20T15:00:00"}])
    scheduler, _, clock = make(agenda, clock=Clock(DAY.replace(hour=14, minute=30)),
                               settings=_settings(briefing=False))
    assert scheduler.tick() == []                     # 30 min before, lead is 15
    clock.moment = DAY.replace(hour=14, minute=46)
    [notice] = scheduler.tick()
    assert notice["kind"] == "event" and "Chamada" in notice["text"] and "14 min" in notice["text"]
    clock.moment = DAY.replace(hour=14, minute=50)
    assert scheduler.tick() == []


def test_past_and_all_day_events_get_no_countdown(make):
    agenda = _agenda(events=[{"id": 6, "title": "Passou", "start": "2026-11-20T09:00:00"},
                             {"id": 7, "title": "Feriado", "start": "2026-11-20"}])
    scheduler, _, _ = make(agenda, clock=Clock(DAY.replace(hour=9, minute=5)),
                           settings=_settings(briefing=False))
    assert scheduler.tick() == []


def test_timezone_aware_starts_are_read_as_local_time():
    moment = proactive.parse_when("2026-11-20T15:00:00+00:00")
    assert moment is not None and moment.tzinfo is None
    assert proactive.parse_when("not a date") is None


def test_a_broken_agenda_never_breaks_the_loop(make):
    scheduler, emitted, _ = make(clock=Clock(DAY.replace(hour=8)))
    scheduler._overview = lambda: (_ for _ in ()).throw(RuntimeError("db locked"))
    assert scheduler.tick() == [] and emitted == []


def test_forget_removes_the_state_file(make, tmp_path):
    scheduler, _, _ = make(clock=Clock(DAY.replace(hour=8)))
    scheduler.tick()
    assert (tmp_path / "proactive_state.json").exists()
    scheduler.forget()
    assert not (tmp_path / "proactive_state.json").exists()


# ── settings and wiring ───────────────────────────────────────────────────

def test_settings_are_allow_listed_and_validated():
    from core import user_settings

    for key in ("proactive_briefing", "proactive_event_alerts"):
        assert user_settings._valid_value(key, True)
        assert not user_settings._valid_value(key, "yes")
    assert user_settings._valid_value("proactive_briefing_time", "07:30")
    for bad in ("7:30", "24:00", "07:60", "aa:bb", 730):
        assert not user_settings._valid_value("proactive_briefing_time", bad)
    assert user_settings._valid_value("proactive_event_lead_minutes", 15)
    assert not user_settings._valid_value("proactive_event_lead_minutes", 7)
    assert not user_settings._valid_value("proactive_event_lead_minutes", True)


def test_main_sends_notices_to_the_desktop_shell_and_the_ui(monkeypatch):
    from core import main

    emitted, ui = [], []
    monkeypatch.setattr(main, "_desktop_emit", lambda event, payload=None: emitted.append((event, payload)))
    monkeypatch.setattr(main, "_notify_ui", lambda name, *args: ui.append((name, args)))
    monkeypatch.setattr(main, "_DESKTOP_BRIDGE", object())
    main._on_proactive_notice({"kind": "briefing", "title": "Resumo", "text": "x" * 3000, "extra": 1})
    event, payload = emitted[0]
    assert event == "nano_notice" and set(payload) == {"kind", "title", "text"}
    assert len(payload["text"]) == 1500
    assert ui == [("on_proactive_notice", (payload,))]


def test_erase_all_data_forgets_the_proactive_state():
    import inspect

    from core import main

    assert "proactive_scheduler.forget()" in inspect.getsource(main.erase_all_data)


def test_the_agenda_is_not_read_on_every_tick(make):
    reads = []
    scheduler, _, clock = make(clock=Clock(DAY.replace(hour=6)))
    scheduler._overview = lambda: reads.append(1) or _agenda()
    for minute in range(5):
        clock.moment = DAY.replace(hour=6, minute=minute)
        scheduler.tick()
    assert len(reads) == 1
