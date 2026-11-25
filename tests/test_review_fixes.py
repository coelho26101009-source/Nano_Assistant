"""The bugs found in the total review of 2026-11-25, one test each.

Every test here failed before its fix. Grouped by the part of NANO they
belong to; the fix itself is explained next to the code it changed.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import threading
from datetime import datetime, timedelta

import pytest

from core import (browser_agent, notice_gate, pc_alerts, phone_camera, protocols, research,
                  secret_store, suggestions, vision)
from plugins import routines

from tests.test_proactive_phase import Clock, make_engine, make_gate
from tests.test_task_chains import (FakeExecutor, STUDY_STEPS, drain, make_brain, plan_call, run,
                                    tool_call)


# ══ The notice gate ═════════════════════════════════════════════════════

def test_over_the_hourly_limit_a_notice_waits_for_the_hour_not_twenty_seconds(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, delivered, _s, _v = make_gate(tmp_path, clock)
    for index in range(notice_gate.HOURLY_CAP["balanced"]):
        clock.advance(minutes=5)
        assert gate.submit({"kind": "download", "title": "D", "text": f"f{index}"}) == "deliver"
    assert gate.submit({"kind": "download", "title": "D", "text": "late"}) == "hold"
    clock.advance(seconds=20)
    assert gate.tick() == []                      # the hour is still full
    clock.advance(minutes=45)                     # the first one leaves the hour
    out = gate.tick()
    assert [item["text"] for item in out] == ["late"]


def test_a_held_offer_keeps_its_button_and_a_summary_keeps_once_keys(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    gate, delivered, state, _v = make_gate(tmp_path, clock, idle=900)
    gate.submit({"kind": "routine", "title": "Rotina", "text": "São 08:00: começo o modo estudo?",
                 "offer": {"label": "Começar", "send": "modo estudo"}})
    gate.submit({"kind": "download", "title": "Download", "text": "a.zip"})
    gate.submit({"kind": "disk", "title": "Disco", "text": "4 GB", "once": "disk:once"})
    gate.tick()
    state["idle"] = 3
    out = gate.tick()
    kinds = sorted(item["kind"] for item in out)
    assert kinds == ["digest", "routine"]
    assert gate.latest_offer()[1]["send"] == "modo estudo"
    assert gate.submit({"kind": "disk", "title": "Disco", "text": "outra", "once": "disk:once"}) == "drop"


def test_what_was_held_is_dropped_once_the_person_turns_it_off(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 23, 30))
    gate, delivered, _s, values = make_gate(tmp_path, clock)
    gate.submit({"kind": "weather", "title": "Tempo", "text": "chuva"})
    gate.submit({"kind": "download", "title": "Download", "text": "a.zip"})
    gate.submit({"kind": "disk", "title": "Disco", "text": "4 GB"})
    gate.mute("weather")
    values["proactive_level"] = "essential"       # downloads and disk are below it now
    clock.moment = datetime(2026, 11, 25, 8, 0)
    assert gate.tick() == []
    assert gate.held() == []


def test_a_summary_after_a_silence_does_not_say_you_were_busy(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 15, 0))
    gate, _d, _s, _v = make_gate(tmp_path, clock)
    gate.snooze(30)
    gate.submit({"kind": "download", "title": "D", "text": "a"})
    gate.submit({"kind": "disk", "title": "Disco", "text": "b"})
    clock.advance(minutes=31)
    out = gate.tick()
    assert len(out) == 1 and out[0]["kind"] == "digest"
    assert "ocupado" not in out[0]["text"] and "fora" not in out[0]["text"]


def test_uma_hora_e_meia_is_ninety_minutes():
    assert notice_gate.parse_silence("não me incomodes durante uma hora e meia") == {"minutes": 90}
    assert notice_gate.parse_silence("do not disturb for an hour and a half") == {"minutes": 90}


def test_a_bare_sim_answers_the_last_thing_nano_said_not_an_older_offer(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 10, 0))
    gate, _d, _s, _v = make_gate(tmp_path, clock)
    gate.submit({"kind": "habit", "title": "Sugestão", "text": "Ativo o modo estudo?",
                 "offer": {"label": "Ativar", "send": "modo estudo"}})
    brain, _executor, _asked = make_brain()
    brain.notice_gate = gate
    # A question to the model in between (no model here: it still ends a turn).
    run(_collect(brain.chat("há atualizações do windows?")))
    assert gate.latest_offer() is None
    assert brain._proactive_reply("sim") is None   # goes to the model, not to the routine


async def _collect(stream):
    return [token async for token in stream]


# ══ Routines and protocols ══════════════════════════════════════════════

@pytest.fixture
def store(tmp_path, monkeypatch):
    fresh = protocols.ProtocolStore(tmp_path / "protocols.json")
    monkeypatch.setattr(protocols, "STORE", fresh)
    return fresh


def _good_night(store):
    return store.save({"name": "Boa noite", "phrases": ["boa noite", "vou dormir"],
                       "close_apps": ["discord"], "volume": 10, "lock": True,
                       "message": "Dorme bem"})


def test_adding_a_step_by_voice_keeps_the_rest_of_the_routine(store):
    _good_night(store)
    result = routines.routine_save({"name": "Boa noite", "steps": [
        {"tool": "pc_media_control", "args": {"action": "play_pause"}}]})
    assert result["ok"] and result["status"] == "updated"
    saved = store.list()[0]
    assert saved["close_apps"] == ["discord"] and saved["volume"] == 10 and saved["lock"] is True
    assert saved["message"] == "Dorme bem"
    assert store.match("vou dormir")["id"] == saved["id"]
    assert [a["tool"] for a in saved["actions"]] == ["pc_media_control"]


def test_sending_the_complete_list_replaces_the_window_steps(store):
    _good_night(store)
    full = [{"tool": "pc_window_batch_close", "args": {"app": "discord"}},
            {"tool": "pc_volume_set", "args": {"level": 10}},
            {"tool": "pc_media_control", "args": {"action": "play_pause"}}]
    assert routines.routine_save({"name": "Boa noite", "steps": full})["ok"]
    saved = store.describe()[0]
    assert saved["close_apps"] == [] and saved["volume"] is None and saved["lock"] is False
    tools = [step["tool"] for step in saved["steps"]]
    assert tools.count("pc_volume_set") == 1 and tools.count("pc_window_batch_close") == 1


def test_a_routine_name_and_another_routines_phrase_cannot_collide(store):
    store.save({"name": "Noite", "phrases": ["boa noite"], "volume": 10})
    with pytest.raises(protocols.ProtocolError):
        store.save({"name": "Boa noite", "phrases": ["vou dormir"], "volume": 5})
    with pytest.raises(protocols.ProtocolError):
        store.save({"name": "Outra", "phrases": ["noite"], "volume": 5})


def test_a_saved_routine_may_name_a_window_by_its_app():
    step = protocols.validate_action({"tool": "pc_window_snap", "args": {"app": "Chrome", "position": "left"}})
    assert step["args"]["query"] == "Chrome"
    with pytest.raises(protocols.ProtocolError):
        protocols.validate_action({"tool": "pc_window_snap", "args": {"position": "left"}})


# ══ Plans (core/brain_tasks.py) ═════════════════════════════════════════

def test_a_different_volume_next_to_a_routine_is_not_called_done(store):
    routines.routine_save({"name": "Estudo", "steps": [{"tool": "pc_volume_set", "args": {"level": 40}}]})
    brain, executor, _ = make_brain()
    calls = [tool_call("routine_run", {"name": "Estudo"}), tool_call("pc_volume_set", {"level": 20})]
    results, _ = run(drain(brain, calls))
    levels = [args["level"] for name, args in executor.calls if name == "pc_volume_set"]
    assert levels == [40, 20] and results[1].get("status") != "covered_by_plan"


def test_a_planned_step_that_failed_is_tried_again_when_the_model_asks():
    from tests.test_task_chains import failed

    executor = FakeExecutor(answers={"pc_app_launch": [failed("not_found")]})
    brain, executor, _ = make_brain(executor)
    calls = [plan_call(STUDY_STEPS[:1]), tool_call("pc_app_launch", {"name": "Notion"})]
    results, _ = run(drain(brain, calls))
    launches = [name for name, _ in executor.calls if name == "pc_app_launch"]
    assert len(launches) >= 2 and results[1].get("status") != "covered_by_plan"


def test_an_approved_plan_step_covers_one_call_with_its_own_arguments():
    brain, executor, asked = make_brain(FakeExecutor(untrusted={"web_navigate_extract"}))
    run(drain(brain, [tool_call("web_navigate_extract", {"url": "https://example.com"})]))
    plan = {"goal": "Marcar", "steps": [
        {"tool": "calendar_add_event", "label": "Marcar o ensaio",
         "args": {"title": "Ensaio", "start": "2026-11-20T10:00"}}]}
    run(brain._run_tool({"function": {"name": "task_plan", "arguments": json.dumps(plan)}}))
    assert len(asked) == 1
    run(brain._run_tool(tool_call("calendar_add_event", {"title": "Outra coisa", "start": "2026-11-21T10:00"})))
    assert len(asked) == 2                       # other arguments: asked
    run(brain._run_tool(tool_call("calendar_add_event", {"title": "Ensaio", "start": "2026-11-20T10:00"})))
    assert len(asked) == 2                       # the approved step: not asked again


def test_a_planned_reminder_after_reading_a_page_is_asked_once(monkeypatch):
    brain, executor, asked = make_brain(FakeExecutor(untrusted={"web_navigate_extract"}))
    run(drain(brain, [tool_call("web_navigate_extract", {"url": "https://example.com"})]))
    plan = {"goal": "Lembrar", "steps": [{"tool": "set_reminder", "label": "Lembrete beber água"}]}
    run(brain._run_tool({"function": {"name": "task_plan", "arguments": json.dumps(plan)}}))
    assert len(asked) == 1
    run(brain._run_tool(tool_call("set_reminder", {"text": "beber água", "when": "daqui a 10 minutos"})))
    assert len(asked) == 1


# ══ PC alerts and suggestions ═══════════════════════════════════════════

def _alerts(**overrides):
    values = {k: False for k in pc_alerts.ALERT_KEYS}
    values.update(overrides.pop("enabled", {}))
    defaults = dict(battery=lambda: None, disk=lambda: None, network=lambda: None,
                    downloads=lambda: None, memory=lambda: None, top_app=lambda: None,
                    uptime_days=lambda: None)
    defaults.update(overrides)
    return pc_alerts.PcAlerts(emit=lambda _n: None, settings=lambda: values, **defaults)


def test_a_laptop_already_full_on_the_charger_is_not_told_at_every_start():
    reading = {"percent": 100, "plugged": True}
    alerts = _alerts(enabled={"battery_full": True}, battery=lambda: dict(reading))
    assert alerts.tick() == []
    reading.update(percent=99)                # charge upkeep around 100%: still the same charge
    alerts.tick()
    reading.update(percent=100)
    assert alerts.tick() == []
    reading.update(percent=90, plugged=False)  # a real new charge
    alerts.tick()
    reading.update(plugged=True)
    alerts.tick()
    reading.update(percent=100)
    assert [n["kind"] for n in alerts.tick()] == ["battery_full"]


def test_the_uptime_key_names_the_same_boot_day_at_any_time_of_day():
    keys = []
    booted = datetime(2026, 11, 16, 21, 0)
    for later in (timedelta(days=7, hours=1), timedelta(days=7, hours=12)):
        moment = booted + later
        alerts = _alerts(enabled={"uptime": True}, now=lambda m=moment: m,
                         uptime_days=lambda span=later: span.total_seconds() / 86400)
        keys.append(alerts.tick()[0]["once"])
    assert keys[0] == keys[1] == "uptime:2026-11-16:1"


def test_breaks_turned_back_on_do_not_count_the_hours_they_were_off(tmp_path):
    clock = Clock(datetime(2026, 11, 24, 9, 0))
    engine, _e, _r, _s, mono = make_engine(tmp_path, clock, settings={"proactive_breaks": True,
                                                                       "proactive_bedtime": False})
    engine.tick()
    mono[0] += 5 * 3600                      # off (or asleep) for five hours, no ticks
    assert engine.tick() == []
    for _ in range(10):
        mono[0] += 60
        assert engine.tick() == []


# ══ Memory: old conversations ═══════════════════════════════════════════

@pytest.fixture
def stack(tmp_path, monkeypatch):
    import core.memory as memory_module
    from core.memory import MemoryEngine
    from core.memory_stack import MemoryStack

    monkeypatch.setattr(memory_module, "DB_PATH", tmp_path / "nano.db")
    engine = MemoryEngine()
    built = MemoryStack(engine, background=False, capture_enabled=False)
    try:
        yield built
    finally:
        built.stop()
        engine.close()


GPU_TURNS = [
    ("Quero montar um PC novo para jogar Fortnite a 144 fps.", "Boa ideia."),
    ("Tenho 900 euros de orçamento para a placa gráfica e o processador.", "Certo."),
    ("Decidi ficar com a RTX 4060 e o Ryzen 5 7600.", "Boa escolha."),
]


def _thread(stack, turns):
    thread = stack.new_conversation()
    for user, assistant in turns:
        stack.record_user_message(user, conversation_id=thread["id"])
        if assistant:
            stack.record_assistant_message(assistant, conversation_id=thread["id"])
    return thread["id"]


def test_forget_all_covers_conversations_that_had_no_recap_yet(stack):
    small = _thread(stack, [("O orçamento para o PC é 300 euros.", "")])
    assert stack.recaps.get(small) is None          # one message: never recapped
    stack.recaps.forget_all()
    assert small in stack.recaps.excluded_ids()
    later = _thread(stack, [("Uma conversa nova depois de esquecer.", "Ok.")])
    assert later not in stack.recaps.excluded_ids()  # conversations started later are recalled


def test_a_conversation_restored_while_recall_was_off_is_rebuilt_later(stack):
    cid = _thread(stack, GPU_TURNS)
    assert stack.recaps.get(cid)["summary"]
    stack.recaps.forget(cid)
    stack.recall_enabled = False
    stack.restore_recap(cid)
    stack.recall_enabled = True
    assert cid in stack.recaps.missing()
    stack.backfill_recaps()
    assert stack.recaps.get(cid)["summary"]


def test_a_refresh_that_read_before_an_edit_does_not_overwrite_it(stack, monkeypatch):
    cid = _thread(stack, GPU_TURNS)
    stale = stack.recaps.get(cid)
    stack.recaps.update(cid, summary="O meu resumo")
    real_get = stack.recaps.get
    calls = {"n": 0}

    def get_once_stale(conversation_id):
        calls["n"] += 1
        return dict(stale, messageCount=0) if calls["n"] == 1 else real_get(conversation_id)

    monkeypatch.setattr(stack.recaps, "get", get_once_stale)
    stack.recaps.refresh(cid, stack.conversations.messages_after(cid, 0))
    assert real_get(cid)["summary"] == "O meu resumo"


# ══ Phone camera, vision, research, URLs ════════════════════════════════

def test_a_photo_promised_to_stay_on_the_pc_never_goes_to_a_cloud_model(monkeypatch):
    asked = []

    def transport(method, url, headers=None, body=None, timeout=None):
        asked.append(url)
        if "11434" in url or "ollama" in url:
            raise RuntimeError("timeout")
        return {"choices": [{"message": {"content": "um gato"}}]}

    monkeypatch.setattr(vision, "TRANSPORT", transport)
    monkeypatch.setattr(vision, "candidates",
                        lambda settings=None, prefer_local=False: [("ollama", "llava"), ("groq", "x")])
    monkeypatch.setattr(vision, "_request", lambda provider, model, *a: ("POST", f"http://{provider}:11434"
                                                                         if provider == "ollama"
                                                                         else "https://groq", {}, {}))
    monkeypatch.setattr(vision, "check_image", lambda *_a: "")
    result = vision.describe("aGVsbG8=", "image/jpeg", settings={}, prefer_local=True, local_only=True)
    assert result["ok"] is False and all("groq" not in url for url in asked)

    broker = phone_camera.CameraBroker(seen_by=lambda: {"provider": "ollama", "local": True})
    broker.ready = lambda: True
    seen = {}
    monkeypatch.setattr(vision, "describe", lambda *a, **kw: seen.update(kw) or {"ok": False})

    def answer():
        while broker._pending is None:
            pass
        broker._pending.data, broker._pending.mime = "aGVsbG8=", "image/jpeg"
        broker._pending.answered = True
        broker._pending.event.set()

    threading.Thread(target=answer, daemon=True).start()
    phone_camera.look("o que é isto?", broker=broker)
    assert seen.get("local_only") is True


def test_one_slow_search_does_not_throw_away_the_others(monkeypatch):
    async def one(client, engine, query, language):
        if engine == "duckduckgo" and "lento" in query:
            await asyncio.sleep(5)
        hit = research.Hit(url=f"https://{engine}.example/{abs(hash(query)) % 1000}",
                           title=query, snippet="texto com 2024")
        return engine, query, [hit], ""

    monkeypatch.setattr(research, "_one_search", one)
    monkeypatch.setattr(research, "SEARCH_TIMEOUT", 0.05)
    monkeypatch.setattr(research, "plan_queries", lambda question, angles: ["rapido", "lento"])

    async def no_read(client, source, terms, gate):
        return None

    monkeypatch.setattr(research, "_read_source", no_read)

    class Client:
        async def aclose(self):
            return None

    result = asyncio.run(research.run("pergunta de teste", client=Client()))
    assert result["ok"] and result["sources"]


def test_addresses_on_the_persons_own_network_are_not_public(monkeypatch):
    for address in ("100.101.102.103", "100.100.100.100", "::ffff:127.0.0.1"):
        family = socket.AF_INET6 if ":" in address else socket.AF_INET
        monkeypatch.setattr(socket, "getaddrinfo",
                            lambda *a, addr=address, fam=family, **k: [(fam, socket.SOCK_STREAM, 6, "", (addr, 80))])
        ok, error = browser_agent.validate_public_http_url("http://tailnet-box.example/admin")
        assert not ok and error == "private_or_reserved_destination", address


# ══ Voice ═══════════════════════════════════════════════════════════════

def test_streamed_speech_stops_before_the_list_of_sources():
    from core.voice import SentenceSpeaker

    said: list[str] = []

    async def speak(text, _remaining):
        said.append(text)
        return True

    async def scenario():
        speaker = SentenceSpeaker(speak)
        for token in ["O Sporting ganhou ", "a liga em 2024.", "\n\n**Fontes**\n1. Liga Portugal — "
                      "ligaportugal.pt\n2. Record — record.pt"]:
            speaker.feed(token)
        speaker.finish()
        await speaker.wait()

    asyncio.run(scenario())
    heard = " ".join(said)
    assert "2024" in heard and "Fontes" not in heard and "record" not in heard


def test_an_interrupted_answer_leaves_no_audio_file_behind(tmp_path, monkeypatch):
    from core import voice

    made: list[str] = []

    class Chain:
        def available(self):
            return True

        async def synthesize(self, text, request):
            # An offline voice writes in a thread: cancelling the wait does
            # not stop it, and its file used to be left in %TEMP%.
            def write():
                import time as _time
                _time.sleep(0.02)
                path = tmp_path / f"piece-{len(made)}.mp3"
                path.write_bytes(b"x")
                made.append(str(path))
                return str(path)
            return await asyncio.to_thread(write), "local"

    provider = voice.LocalTTSProvider({}, chain=Chain())
    provider.request = lambda: object()

    async def play(path):
        await asyncio.sleep(0.005)           # piece 1 plays; piece 2 is being made
        provider.stop()                      # the person presses stop

    monkeypatch.setattr(voice, "_play_audio_file", play)
    monkeypatch.setattr(voice.speech_engines, "split_for_speech", lambda text: ["Um.", "Dois.", "Três."])

    async def scenario():
        await provider.speak("Um. Dois. Três.")
        await asyncio.sleep(0.2)             # the piece being made when stop came ends

    asyncio.run(scenario())
    assert made and not any(os.path.exists(path) for path in made)


# ══ Keys and the relay ══════════════════════════════════════════════════

def test_restoring_a_key_never_copies_the_environment_into_the_store(monkeypatch, tmp_path):
    monkeypatch.setattr(secret_store, "_read_store", lambda: {})
    monkeypatch.setenv("ELEVENLABS_API_KEY", "env-key-1234567890")
    name = next(key for key, envs in secret_store._ENV_FALLBACK.items() if "ELEVENLABS_API_KEY" in envs)
    assert secret_store.get_secret(name) == "env-key-1234567890"
    assert secret_store.get_stored_secret(name) == ""


def test_the_pc_backs_off_when_the_relay_closes_at_once(monkeypatch):
    from core import mobile_relay

    client = mobile_relay.RelayClient(link=None, wanted=lambda: True, url=lambda: "https://r.example")
    pauses: list[float] = []
    attempts = {"n": 0}

    async def connect_once(url):
        attempts["n"] += 1
        client._close_code = mobile_relay.REPLACED_CLOSE_CODE if attempts["n"] == 1 else 1006
        if attempts["n"] >= 3:
            client._stopping = True
        return True

    async def pause(seconds):
        pauses.append(seconds)

    monkeypatch.setattr(client, "_connect_once", connect_once)
    monkeypatch.setattr(client, "_pause", pause)
    asyncio.run(client._main())
    assert attempts["n"] == 3
    assert pauses and pauses[0] == mobile_relay.RETRY_MAX_SECONDS
    assert all(seconds >= mobile_relay.RETRY_MIN_SECONDS for seconds in pauses)
