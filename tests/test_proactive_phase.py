"""NANO proactive, phase 1: the anti-annoyance gate, weather in the briefing,
new PC alerts, suggestions by context, routines, and the chat/voice answers."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from core import notice_gate, pc_alerts, presence, proactive, suggestions, weather


# ── helpers ───────────────────────────────────────────────────────────────
class Clock:
    def __init__(self, start: datetime) -> None:
        self.moment = start

    def now(self) -> datetime:
        return self.moment

    def time(self) -> float:
        return self.moment.timestamp()

    def advance(self, **kwargs) -> None:
        self.moment += timedelta(**kwargs)


def make_gate(tmp_path, clock, *, settings=None, idle=0.0, busy=""):
    delivered: list[tuple[dict, bool]] = []
    state = {"idle": idle, "busy": busy}
    values = dict(notice_gate.SETTING_DEFAULTS)
    values.update(settings or {})
    gate = notice_gate.NoticeGate(
        deliver=lambda notice, speak: delivered.append((notice, speak)),
        state_path=tmp_path / "gate.json", settings=lambda: values,
        clock=clock.time, now=clock.now,
        idle=lambda: state["idle"], busy=lambda: state["busy"])
    return gate, delivered, state, values


# ── the gate ──────────────────────────────────────────────────────────────
def test_gate_delivers_and_speaks_important_notices(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, delivered, _state, _values = make_gate(tmp_path, clock)
    assert gate.submit({"kind": "event", "title": "Daqui a pouco", "text": "Reunião às 10:15"}) == "deliver"
    notice, speak = delivered[-1]
    assert speak is True and notice["priority"] == "high" and notice["id"].startswith("n-")
    assert gate.submit({"kind": "download", "title": "Download", "text": "a.zip"}) == "deliver"
    assert delivered[-1][1] is False      # "important" only speaks high/critical/briefing


def test_gate_drops_repeats_and_muted_kinds(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, delivered, _s, _v = make_gate(tmp_path, clock)
    notice = {"kind": "disk", "title": "Disco", "text": "Pouco espaço"}
    assert gate.submit(dict(notice)) == "deliver"
    assert gate.submit(dict(notice)) == "drop"
    clock.advance(minutes=31)
    assert gate.submit(dict(notice)) == "deliver"
    assert gate.mute("disk") is True
    clock.advance(minutes=31)
    assert gate.submit(dict(notice)) == "drop"
    assert gate.mute("watch") is False    # a watch the person asked for can't be muted
    assert gate.unmute("disk") is True
    # Survives a restart.
    gate.mute("download")
    again, _d, _s2, _v2 = make_gate(tmp_path, clock)
    assert again.muted() == ["download"]


def test_level_decides_what_gets_through(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, _d, _s, values = make_gate(tmp_path, clock, settings={"proactive_level": "off"})
    assert gate.submit({"kind": "event", "title": "E", "text": "x"}) == "drop"
    assert gate.submit({"kind": "battery", "priority": "critical", "title": "B", "text": "5%"}) == "deliver"
    values["proactive_level"] = "essential"
    assert gate.submit({"kind": "event", "title": "E", "text": "y"}) == "deliver"
    assert gate.submit({"kind": "download", "title": "D", "text": "z"}) == "drop"


def test_quiet_hours_hold_then_summarise_in_the_morning(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 23, 30))
    gate, delivered, _s, _v = make_gate(tmp_path, clock)
    assert gate.submit({"kind": "download", "title": "Download terminado", "text": "a.zip"}) == "hold"
    assert gate.submit({"kind": "disk", "title": "Disco quase cheio", "text": "4 GB"}) == "hold"
    assert gate.submit({"kind": "habit", "title": "Sugestão", "text": "modo"}) == "drop"
    # Important: shown, but without sound or voice.
    assert gate.submit({"kind": "event", "title": "E", "text": "cedo"}) == "deliver"
    assert delivered[-1][1] is False and delivered[-1][0]["silent"] is True
    assert gate.tick() == []
    clock.moment = datetime(2026, 11, 25, 8, 0)
    out = gate.tick()
    assert len(out) == 1 and out[0]["kind"] == "digest"
    assert "a.zip" in out[0]["text"] and "4 GB" in out[0]["text"]


def test_quiet_hours_cross_midnight():
    assert notice_gate.in_quiet_hours(datetime(2026, 1, 1, 23, 10), "23:00", "07:30")
    assert notice_gate.in_quiet_hours(datetime(2026, 1, 1, 3, 0), "23:00", "07:30")
    assert not notice_gate.in_quiet_hours(datetime(2026, 1, 1, 7, 30), "23:00", "07:30")
    assert notice_gate.in_quiet_hours(datetime(2026, 1, 1, 13, 0), "12:00", "14:00")
    assert not notice_gate.in_quiet_hours(datetime(2026, 1, 1, 13, 0), "12:00", "12:00")


def test_away_holds_and_welcomes_back(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    gate, delivered, state, _v = make_gate(tmp_path, clock, idle=900)
    assert gate.present() is False
    assert gate.submit({"kind": "download", "title": "Download terminado", "text": "jogo.zip"}) == "hold"
    assert gate.submit({"kind": "network", "title": "Sem rede", "text": "caiu"}) == "deliver"
    gate.tick()
    state["idle"] = 3
    out = gate.tick()
    # One held notice comes back as itself, not as a one-line summary.
    assert len(out) == 1 and out[0]["kind"] == "download"


def test_fullscreen_game_holds_useful_notices(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    gate, _d, state, values = make_gate(tmp_path, clock, busy="game")
    assert gate.present() is False
    assert gate.submit({"kind": "download", "title": "D", "text": "a"}) == "hold"
    values["proactive_respect_fullscreen"] = False
    assert gate.submit({"kind": "download", "title": "D", "text": "b"}) == "deliver"


def test_snooze_by_command_and_rate_limit(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    gate, _d, _s, _v = make_gate(tmp_path, clock)
    status = gate.snooze(60)
    assert status["active"] and status["until"] == "16:00"
    assert gate.submit({"kind": "download", "title": "D", "text": "a"}) == "hold"
    assert gate.submit({"kind": "battery", "priority": "critical", "title": "B", "text": "5%"}) == "deliver"
    gate.resume()
    assert gate.snooze_status()["active"] is False
    for index in range(6):
        gate.submit({"kind": "download", "title": "D", "text": f"file{index}"})
    decisions = [h["decision"] for h in gate.history()[:3]]
    assert "hold" in decisions          # over the hourly cap: held, not lost


def test_daily_cap_on_suggestions(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, _d, _s, _v = make_gate(tmp_path, clock)
    results = []
    for index in range(5):
        clock.advance(hours=1)
        results.append(gate.submit({"kind": "habit", "title": "S", "text": f"s{index}"}))
    assert results.count("deliver") == notice_gate.DAILY_LOW_CAP["balanced"]


def test_night_notice_shown_silently_in_quiet_hours(tmp_path):
    clock = Clock(datetime(2026, 11, 25, 1, 15))
    gate, delivered, _s, _v = make_gate(tmp_path, clock)
    assert gate.submit({"kind": "bedtime", "title": "Já é tarde", "text": "x", "night": True}) == "deliver"
    assert delivered[-1][1] is False


def test_once_key_is_said_once_even_after_restart(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, _d, _s, _v = make_gate(tmp_path, clock)
    item = {"kind": "uptime", "title": "R", "text": "9 dias", "once": "uptime:2026-11-15:1"}
    assert gate.submit(dict(item)) == "deliver"
    clock.advance(hours=3)
    again, _d2, _s2, _v2 = make_gate(tmp_path, clock)
    assert again.submit(dict(item)) == "drop"


def test_offers_answerable_for_three_minutes(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, delivered, _s, _v = make_gate(tmp_path, clock)
    gate.submit({"kind": "memory", "title": "M", "text": "Fecho o Chrome?",
                 "offer": {"label": "Fechar o Chrome", "send": "Fecha o Chrome"}})
    notice_id = delivered[-1][0]["id"]
    assert gate.latest_offer()[1]["send"] == "Fecha o Chrome"
    assert gate.offer(notice_id)["kind"] == "memory"
    clock.advance(minutes=4)
    assert gate.latest_offer() is None
    assert gate.offer(notice_id) is not None      # the button still works


@pytest.mark.parametrize("text,expected", [
    ("não me incomodes durante uma hora", {"minutes": 60}),
    ("Nano, não me incomodes durante 30 minutos", {"minutes": 30}),
    ("fica calado até amanhã", {"until": "morning"}),
    ("silêncio por 2 horas", {"minutes": 120}),
    ("modo não incomodar", {"minutes": 60}),
    ("do not disturb for 45 minutes", {"minutes": 45}),
    ("be quiet until tomorrow", {"until": "morning"}),
    ("já podes voltar a falar", {"resume": True}),
    ("podes voltar a falar", {"resume": True}),
    ("não me incomodes com o trabalho do João", None),
    ("como desligo o modo não incomodar do windows?", None),
])
def test_parse_silence(text, expected):
    assert notice_gate.parse_silence(text) == expected


def test_answer_kind():
    assert notice_gate.answer_kind("Sim!") == "yes"
    assert notice_gate.answer_kind("ei nano, sim por favor") == "yes"
    assert notice_gate.answer_kind("agora não") == "no"
    assert notice_gate.answer_kind("sim, mas primeiro abre o Spotify") is None


def test_gate_settings_validation():
    assert notice_gate.valid_setting("proactive_level", "balanced") is True
    assert notice_gate.valid_setting("proactive_level", "loud") is False
    assert notice_gate.valid_setting("proactive_quiet_start", "25:00") is False
    assert notice_gate.valid_setting("proactive_speak", "all") is True
    assert notice_gate.valid_setting("theme", "dark") is None


def test_user_settings_accepts_the_new_keys(tmp_path, monkeypatch):
    from core import user_settings

    for key, good, bad in (
        ("proactive_level", "essential", "nope"),
        ("proactive_quiet_start", "22:30", "22h"),
        ("proactive_breaks", True, "yes"),
        ("weather_city", "Porto", "x" * 80),
        ("pc_alerts_memory", False, 0),
    ):
        assert user_settings._valid_value(key, good) is True, key
        assert user_settings._valid_value(key, bad) is False, key


# ── weather ───────────────────────────────────────────────────────────────
FORECAST = {
    "current": {"temperature_2m": 13.4, "weather_code": 3},
    "daily": {"temperature_2m_max": [16.6], "temperature_2m_min": [9.2], "weather_code": [61]},
    "hourly": {"time": [f"2026-11-24T{h:02d}:00" for h in range(24)],
               "precipitation_probability": [0] * 14 + [80] * 10},
}


def test_weather_summary_mentions_rain_and_umbrella():
    summary = weather.summarise(FORECAST, datetime(2026, 11, 24, 8, 0), "Porto")
    assert summary["rain"] is True
    assert "Porto: 13° agora, nublado, entre 9° e 17°." in summary["line"]
    assert "à tarde" in summary["line"] and "guarda-chuva" in summary["speech"]
    # CC BY 4.0: the shown line credits Open-Meteo; the spoken one does not.
    assert summary["line"].endswith("Dados: Open-Meteo.com.")
    assert "Open-Meteo" not in summary["speech"]


def test_weather_geocodes_once_and_caches(tmp_path):
    calls = []

    def fetch(url, params):
        calls.append(url)
        if "geocoding" in url:
            return {"results": [{"name": "Porto", "latitude": 41.14961, "longitude": -8.61099,
                                 "country": "Portugal"}]}
        assert params["latitude"] == 41.15 and params["longitude"] == -8.61
        return FORECAST

    now = [1000.0]
    service = weather.Weather(cache_path=tmp_path / "w.json", fetch=fetch, clock=lambda: now[0])
    assert service.today("Porto", datetime(2026, 11, 24, 8, 0))["rain"] is True
    assert service.today("porto", datetime(2026, 11, 24, 8, 5)) is not None
    assert len(calls) == 2              # one geocode, one forecast
    now[0] += weather.CACHE_SECONDS + 1
    service.today("Porto", datetime(2026, 11, 24, 9, 0))
    assert len(calls) == 3


def test_weather_is_silent_without_a_city_or_when_offline(tmp_path):
    def broken(url, params):
        raise OSError("offline")

    service = weather.Weather(cache_path=None, fetch=broken)
    assert service.today("") is None
    assert service.today("Porto") is None


def test_briefing_carries_weather_and_a_short_spoken_version():
    now = datetime(2026, 11, 24, 8, 0)
    overview = {"events": [{"id": "1", "title": "Matemática", "start": "2026-11-24T10:00:00"},
                           {"id": "2", "title": "Treino", "start": "2026-11-24T18:00:00"}],
                "reminders": []}
    briefing = proactive.build_briefing(overview, now, name="Simão",
                                        weather={"line": "Porto: 13°", "speech": "Vai chover."})
    assert "Porto: 13°" in briefing["text"]
    assert briefing["speech"].startswith("Bom dia, Simão!")
    assert "2 coisas" in briefing["speech"] and "Matemática às 10:00" in briefing["speech"]
    assert briefing["speech"].endswith("Vai chover.")


def test_briefing_waits_until_the_person_is_at_the_pc(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 8, 5))
    present = [False]
    emitted = []
    scheduler = proactive.ProactiveScheduler(
        overview=lambda: {"events": [], "reminders": []}, emit=emitted.append,
        state_path=tmp_path / "p.json", now=clock.now, present=lambda: present[0],
        settings=lambda: {"briefing": True, "briefing_time": "08:00", "event_alerts": False,
                          "lead_minutes": 15, "prep": False, "name": ""})
    assert scheduler.tick() == []
    present[0] = True
    clock.advance(minutes=40)
    out = scheduler.tick()
    assert len(out) == 1 and out[0]["kind"] == "briefing"


def test_event_alert_expires_when_the_event_starts():
    start = datetime(2026, 11, 24, 10, 15)
    alert = proactive.build_event_alert("Reunião", start, datetime(2026, 11, 24, 10, 0))
    assert alert["expires_at"] == start.timestamp() and alert["speech"]


# ── new PC alerts ─────────────────────────────────────────────────────────
def _alerts(**overrides):
    values = {k: False for k in pc_alerts.ALERT_KEYS}
    values.update(overrides.pop("enabled", {}))
    emitted = []
    defaults = dict(battery=lambda: None, disk=lambda: None, network=lambda: None,
                    downloads=lambda: None, memory=lambda: None, top_app=lambda: None,
                    uptime_days=lambda: None)
    defaults.update(overrides)
    return pc_alerts.PcAlerts(emit=emitted.append, settings=lambda: values, **defaults), emitted


def test_memory_alert_needs_two_minutes_and_offers_to_close_the_top_app():
    alerts, emitted = _alerts(enabled={"memory": True},
                              memory=lambda: {"percent": 94.0, "top": None},
                              top_app=lambda: ("chrome.exe", 3.14))
    for _ in range(pc_alerts.MEMORY_CONFIRM_TICKS - 1):
        assert alerts.tick() == []
    out = alerts.tick()
    assert out[0]["kind"] == "memory" and "Chrome" in out[0]["text"] and "3.1 GB" in out[0]["text"]
    assert out[0]["offer"]["send"] == "Fecha o Chrome"
    assert alerts.tick() == []          # not again for two hours


def test_battery_critical_is_critical_and_full_battery_once_per_charge():
    reading = {"percent": 9, "plugged": False}
    alerts, _ = _alerts(enabled={"battery": True, "battery_full": True}, battery=lambda: dict(reading))
    assert alerts.tick()[0]["priority"] == "critical"
    reading.update(percent=100, plugged=True)
    out = alerts.tick()
    assert [n["kind"] for n in out] == ["battery_full"]
    assert alerts.tick() == []
    reading.update(plugged=False, percent=99)
    alerts.tick()
    reading.update(plugged=True, percent=100)
    assert [n["kind"] for n in alerts.tick()] == ["battery_full"]


def test_uptime_suggestion_after_a_week():
    alerts, _ = _alerts(enabled={"uptime": True}, uptime_days=lambda: 9.3)
    out = alerts.tick()
    assert out[0]["kind"] == "uptime" and "9 dias" in out[0]["text"] and out[0]["once"]
    assert alerts.tick() == []


# ── suggestions and routines ──────────────────────────────────────────────
PROTOCOLS = [{"id": "estudo", "name": "Modo estudo", "phrases": ["modo estudo"]}]


def make_engine(tmp_path, clock, *, idle=10.0, settings=None, overview=None):
    emitted, ran = [], []
    values = dict(suggestions.SETTING_DEFAULTS)
    values.update(settings or {})
    store = suggestions.RoutineStore(tmp_path / "routines.json")
    mono = [0.0]
    engine = suggestions.SuggestionEngine(
        emit=emitted.append, state_path=tmp_path / "s.json", routines=store,
        protocols=lambda: PROTOCOLS, run_protocol=ran.append,
        overview=(lambda: overview or {"events": []}), idle=lambda: idle,
        settings=lambda: values, now=clock.now, clock=lambda: mono[0])
    return engine, emitted, ran, store, mono


def test_routine_asks_at_its_time_once(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 8, 2))        # a Tuesday
    engine, emitted, ran, store, _m = make_engine(tmp_path, clock)
    store.save({"protocol": "estudo", "time": "08:00", "days": [0, 1, 2, 3, 4]},
               known_protocols={"estudo"})
    out = engine.tick()
    assert out[0]["kind"] == "routine" and out[0]["offer"]["send"] == "modo estudo"
    assert engine.tick() == [] and ran == []


def test_routine_can_run_by_itself_and_skips_other_days(tmp_path):
    clock = Clock(datetime(2026, 11, 29, 8, 1))        # a Sunday
    engine, _e, ran, store, _m = make_engine(tmp_path, clock)
    store.save({"protocol": "estudo", "time": "08:00", "days": [0], "ask": False},
               known_protocols={"estudo"})
    assert engine.tick() == []
    clock.moment = datetime(2026, 11, 30, 8, 1)        # Monday
    out = engine.tick()
    assert ran == ["estudo"] and "Comecei" in out[0]["text"]
    clock.moment = datetime(2026, 12, 7, 9, 0)         # too late: not started
    assert engine.tick() == []


def test_routine_validation():
    with pytest.raises(suggestions.RoutineError):
        suggestions.validate_routine({"protocol": "x", "time": "8:00", "days": [1]})
    with pytest.raises(suggestions.RoutineError):
        suggestions.validate_routine({"protocol": "x", "time": "08:00", "days": []})
    with pytest.raises(suggestions.RoutineError):
        suggestions.validate_routine({"protocol": "../x", "time": "08:00", "days": [1]})
    with pytest.raises(suggestions.RoutineError):
        suggestions.validate_routine({"protocol": "x", "time": "08:00", "days": [1]},
                                     known_protocols={"y"})


def test_habit_offer_after_three_days_at_the_same_time(tmp_path):
    clock = Clock(datetime(2026, 11, 17, 18, 5))       # Tuesday
    engine, emitted, _r, _s, _m = make_engine(tmp_path, clock)
    for day in (17, 18, 19):
        clock.moment = datetime(2026, 11, day, 18, 5)
        engine.record_protocol("estudo")
    clock.moment = datetime(2026, 11, 24, 18, 20)
    out = engine.tick()
    assert [n["kind"] for n in out] == ["habit"] and out[0]["offer"]["send"] == "modo estudo"
    assert engine.tick() == []         # once a day
    # Weekend runs do not count for a weekday.
    assert suggestions.habit_due([{"protocol": "estudo", "at": "2026-11-22T18:00:00"}] * 3,
                                 datetime(2026, 11, 24, 18, 0)) == []


def test_habit_not_offered_when_already_done_today(tmp_path):
    log = [{"protocol": "estudo", "at": f"2026-11-{d}T18:00:00"} for d in (17, 18, 19)]
    log.append({"protocol": "estudo", "at": "2026-11-24T17:00:00"})
    assert suggestions.habit_due(log, datetime(2026, 11, 24, 18, 0)) == []


def test_bedtime_mentions_an_early_event_and_only_when_active(tmp_path):
    clock = Clock(datetime(2026, 11, 25, 1, 10))
    overview = {"events": [{"id": "1", "title": "Matemática", "start": "2026-11-25T08:30:00"}]}
    engine, _e, _r, _s, _m = make_engine(tmp_path, clock, overview=overview)
    out = engine.tick()
    assert out[0]["kind"] == "bedtime" and "08:30" in out[0]["text"] and out[0]["night"]
    assert engine.tick() == []
    idle_engine, _e2, _r2, _s2, _m2 = make_engine(tmp_path / "b", clock, idle=None)
    assert idle_engine.tick() == []


def test_break_nudge_after_two_hours_of_use(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    engine, _e, _r, _s, mono = make_engine(tmp_path, clock, settings={"proactive_breaks": True,
                                                                       "proactive_bedtime": False})
    assert engine.tick() == []
    out = []
    while mono[0] < 2 * 3600 + 5 and not out:     # in use: a look every 30 s
        mono[0] += suggestions.TICK_SECONDS
        out = engine.tick()
    assert out and out[0]["kind"] == "break"
    for _ in range(20):
        mono[0] += suggestions.TICK_SECONDS
        assert engine.tick() == []


# ── the chat: silence commands and "sim" to an offer ──────────────────────
class _FakeGate:
    def __init__(self):
        self.snoozed = None
        self.offer_item = ("n-1", {"send": "Fecha o Chrome", "kind": "memory"})
        self.closed = []

    def snooze(self, minutes=None, *, until_morning=False):
        self.snoozed = "morning" if until_morning else minutes
        return {"active": True, "until": "16:00"}

    def resume(self):
        self.snoozed = 0
        return {"active": False}

    def latest_offer(self):
        return self.offer_item

    def close_offer(self, notice_id):
        self.closed.append(notice_id)


def test_brain_handles_silence_and_offers_without_a_model():
    from core.brain import Brain

    brain = Brain.__new__(Brain)
    gate = _FakeGate()
    brain.notice_gate = gate
    assert "16:00" in brain._proactive_reply("não me incomodes durante uma hora")["answer"]
    assert gate.snoozed == 60
    assert brain._proactive_reply("sim") == {"send": "Fecha o Chrome"}
    assert gate.closed == ["n-1"]
    assert "outra altura" in brain._proactive_reply("agora não")["answer"]
    gate.offer_item = None
    assert brain._proactive_reply("sim") is None
    assert brain._proactive_reply("abre o spotify") is None


def test_main_accept_returns_the_sentence_to_send(monkeypatch):
    from core import main

    gate = main.notice_gate_instance
    delivered = []
    monkeypatch.setattr(gate, "_deliver", lambda notice, speak: delivered.append(notice))
    monkeypatch.setattr(gate, "_settings", lambda: dict(notice_gate.SETTING_DEFAULTS))
    monkeypatch.setattr(gate, "_idle", lambda: 0.0)
    monkeypatch.setattr(gate, "_busy", lambda: "")
    monkeypatch.setattr(gate, "_context", lambda settings: {"snoozed": False, "quiet": False,
                                                            "busy": False, "away": False})
    gate.submit({"kind": "routine", "title": "Rotina", "text": "Começo?",
                 "offer": {"label": "Começar", "send": "modo estudo"}})
    notice_id = delivered[-1]["id"]
    assert main.proactive_action(notice_id, "accept") == {"ok": True, "send": "modo estudo"}
    assert main.proactive_action(notice_id, "accept")["ok"] is False
    assert main.mute_notice_kind("watch")["ok"] is False
    assert main.set_proactive_snooze(7)["ok"] is False


def test_idle_time_survives_the_49_day_tick_wrap():
    """GetTickCount wraps to 0 every 49.7 days and ctypes may return it as a
    negative int: idle time must stay small and positive across both."""
    assert presence.elapsed_seconds(10_000, 4_000) == 6.0
    # Last input just before the wrap, now just after it.
    assert presence.elapsed_seconds(1_500, 0xFFFFFFFF - 499) == 2.0
    # The same "now" handed back as a signed 32-bit value.
    assert presence.elapsed_seconds(-1, 0xFFFFFFFF - 1000) == 1.0


def test_presence_is_unknown_off_windows(monkeypatch):
    monkeypatch.setattr(presence.sys, "platform", "linux")
    assert presence.idle_seconds() is None
    assert presence.busy_state() is None
