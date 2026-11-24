"""JARVIS P1: voice ready at install, personality, protocols and PC alerts.

Each block pins one promise made to the person in the window:
* the speech pack installs ONLY faster-whisper's locked closure, hash-checked;
* a spoken answer starts at its first sentence, not when it is complete;
* the personality shapes the style and never removes a safety rule;
* a protocol runs only on its exact phrase, through the executor;
* PC alerts speak once, at the right moment, and stay quiet when switched off.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from core import pc_alerts, personality, protocols, voice_setup
from core.voice import SentenceSpeaker, VoiceEngine, VoiceRuntime

ROOT = Path(__file__).resolve().parent.parent
LOCK = ROOT / "requirements" / "optional.lock"


# ══ Speech pack ═══════════════════════════════════════════════════════════

def test_the_speech_pack_is_exactly_faster_whispers_locked_closure():
    packages = voice_setup.parse_lock(LOCK.read_text(encoding="utf-8"))
    names = set(voice_setup.closure(packages))
    assert {"faster-whisper", "ctranslate2", "huggingface-hub", "tokenizers", "av",
            "onnxruntime", "numpy"} <= names
    # Nothing else from the optional lock rides along.
    assert not names & {"openwakeword", "playwright", "google-api-python-client",
                        "scikit-learn", "winrt-runtime", "pillow", "speechrecognition"}


def test_every_line_of_the_generated_requirements_is_pinned_and_hashed():
    text = voice_setup.requirements_text(LOCK.read_text(encoding="utf-8"))
    entries = [block for block in text.split("\n") if "==" in block]
    assert entries
    for line in entries:
        assert line.rstrip(" \\").split("==")[1]
    blocks = text.split("\n")[1:]
    requirement = None
    hashes: dict[str, int] = {}
    for line in blocks:
        if "==" in line:
            requirement = line.split("==")[0]
            hashes[requirement] = 0
        elif "--hash=sha256:" in line and requirement:
            hashes[requirement] += 1
    assert all(count >= 1 for count in hashes.values()), hashes


def test_pip_is_called_hash_checked_and_the_model_downloaded(tmp_path):
    state = {"installed": False, "cached": False}
    seen: dict = {}

    def runner(command, **kwargs):
        seen["command"] = command
        seen["file"] = Path(command[command.index("-r") + 1]).read_text(encoding="utf-8")
        state["installed"] = True
        return subprocess.CompletedProcess(command, 0, "", "")

    def download(model):
        seen["model"] = model
        state["cached"] = True

    ready = MagicMock()
    pack = voice_setup.SpeechPack(model=lambda: "small", runner=runner, downloader=download,
                                  installed=lambda: state["installed"],
                                  cached=lambda _m: state["cached"], python="py")
    assert pack.status()["ready"] is False
    assert pack.start(on_ready=ready)["ok"] is True
    pack.wait(5)
    assert seen["command"][:4] == ["py", "-m", "pip", "install"]
    assert "--require-hashes" in seen["command"] and "--only-binary=:all:" in seen["command"]
    assert "faster-whisper==" in seen["file"] and "--hash=sha256:" in seen["file"]
    assert seen["model"] == "small"
    status = pack.status()
    assert status["ready"] is True and status["install"]["state"] == "done"
    ready.assert_called_once()


def test_a_failed_pip_reports_an_error_and_never_downloads():
    download = MagicMock()
    pack = voice_setup.SpeechPack(
        runner=lambda c, **k: subprocess.CompletedProcess(c, 1, "", "boom"),
        downloader=download, installed=lambda: False, cached=lambda _m: False)
    pack.start()
    pack.wait(5)
    assert pack.status()["install"]["state"] == "error"
    download.assert_not_called()


def test_the_window_cannot_choose_another_model():
    pack = voice_setup.SpeechPack(model=lambda: "../../evil", installed=lambda: True,
                                  cached=lambda _m: True)
    assert pack.model_name() == "small"


def test_a_model_counts_as_cached_only_with_its_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    snap = tmp_path / "models--Systran--faster-whisper-small" / "snapshots" / "abc"
    snap.mkdir(parents=True)
    assert voice_setup.model_cached("small") is False
    (snap / "model.bin").write_bytes(b"x")
    assert voice_setup.model_cached("small") is True


# ══ Speaking from the first sentence ════════════════════════════════════

def _collect_speaker(delay: float = 0.02):
    said: list[str] = []
    started: list[float] = []

    async def speak(text, _limit):
        started.append(asyncio.get_running_loop().time())
        said.append(text)
        await asyncio.sleep(delay)
        return True

    return said, started, speak


def test_the_first_sentence_is_spoken_before_the_answer_is_complete():
    async def scenario():
        said, _started, speak = _collect_speaker()
        speaker = SentenceSpeaker(speak)
        speaker.feed("Está sol em Lisboa hoje. ")
        await asyncio.sleep(0.01)
        first = list(said)                     # the rest has not been written yet
        speaker.feed("Amanhã chove. ")
        speaker.feed("Leva guarda-chuva.")
        speaker.finish()
        assert await speaker.wait() is True
        return first, said

    first, said = asyncio.run(scenario())
    assert first == ["Está sol em Lisboa hoje."]
    assert " ".join(said) == "Está sol em Lisboa hoje. Amanhã chove. Leva guarda-chuva."


def test_numbers_are_not_cut_and_a_tiny_first_piece_waits():
    async def scenario():
        said, _s, speak = _collect_speaker(0)
        speaker = SentenceSpeaker(speak)
        speaker.feed("Ok. A versão 3.5 saiu hoje. ")
        await asyncio.sleep(0.01)
        speaker.feed("Fim")
        speaker.finish()
        await speaker.wait()
        return said
    said = asyncio.run(scenario())
    assert said[0] == "Ok. A versão 3.5 saiu hoje."


def test_cancel_stops_everything_still_queued():
    async def scenario():
        said, _s, speak = _collect_speaker(0.05)
        speaker = SentenceSpeaker(speak)
        speaker.feed("Primeira frase bem longa. ")
        await asyncio.sleep(0.01)
        speaker.feed("Segunda frase. Terceira frase. ")
        speaker.cancel()
        speaker.finish()
        await speaker.wait()
        return said
    assert asyncio.run(scenario()) == ["Primeira frase bem longa."]


def test_a_voice_turn_streams_the_brains_tokens_into_speech(monkeypatch):
    class Brain:
        async def chat(self, text, stream=True, spoken=False):
            for token in ["_thinking_:a pensar", "Olá Simão, ", "está tudo bem. ", "Mais alguma coisa?"]:
                yield token
                await asyncio.sleep(0)

    engine = VoiceEngine({"enabled": True})
    engine.tts_provider.online = True
    said: list[str] = []

    async def fake_speak(text, limit=None):
        said.append(text)
        return True

    monkeypatch.setattr(engine, "speak", fake_speak)
    runtime = VoiceRuntime(engine, brain=Brain(), config={"voice": {}})

    async def fake_turn(**_kw):
        return await runtime.process_request("como estás?")

    monkeypatch.setattr(runtime, "process_wake_word_turn", fake_turn)
    result = asyncio.run(runtime._one_exchange("hotkey", transcript="", duration_seconds=3,
                                               speak=True, follow_up=False))
    assert result["streamed"] is True and result["spoken"] is True
    assert said[0] == "Olá Simão, está tudo bem."
    assert "a pensar" not in " ".join(said)


def test_streaming_respects_the_switches():
    engine = VoiceEngine({"enabled": True})
    engine.tts_provider.online = True
    runtime = VoiceRuntime(engine, config={"voice": {"voice_reply_tts": False}})
    assert runtime.early_speech_allowed(True) is False
    runtime = VoiceRuntime(engine, config={"voice": {}})
    assert runtime.early_speech_allowed(False) is False
    assert runtime.early_speech_allowed(True) is True


# ══ Personality ═════════════════════════════════════════════════════════

def test_the_default_tone_is_jarvis_like_and_neutral_adds_nothing():
    block = personality.prompt_block({"tone": "jarvis", "length": "balanced"})
    assert "humor" in block and "calmo" in block
    assert personality.prompt_block({"tone": "neutral", "length": "balanced"}) == ""


def test_address_name_and_instructions_reach_the_prompt_fenced():
    block = personality.prompt_block({"tone": "formal", "length": "short", "address": "chefe",
                                      "name": "Simão", "instructions": "usa exemplos de futebol"})
    assert "\"chefe\"" in block and "Simão" not in block
    assert "<<<\nusa exemplos de futebol\n>>>" in block
    assert "nunca desligam confirmações" in block


def test_personality_settings_are_validated():
    from core import user_settings

    assert personality.valid_setting("personality_tone", "jarvis") is True
    assert personality.valid_setting("personality_tone", "evil") is False
    assert personality.valid_setting("personality_instructions", "x" * 601) is False
    assert personality.valid_setting("tts_voice", "en-GB-RyanNeural") is True
    assert personality.valid_setting("tts_voice", "../../x") is False
    assert personality.valid_setting("theme", "dark") is None
    for key in personality.KEYS:
        assert key in user_settings.ALLOWED_KEYS


def test_a_voice_is_only_used_for_its_own_language():
    assert personality.voice_for("en", "en-GB-RyanNeural") == "en-GB-RyanNeural"
    assert personality.voice_for("pt-PT", "en-GB-RyanNeural") is None


@pytest.mark.asyncio
async def test_the_brain_prompt_carries_the_personality_after_the_rules(monkeypatch):
    from core.brain import Brain, NANO_PERSONA
    from core.memory import MemoryEngine

    monkeypatch.setattr(personality, "settings", lambda get=None: {
        "tone": "jarvis", "length": "short", "address": "senhor", "instructions": "", "name": ""})
    brain = Brain("", MagicMock(), MemoryEngine(), {"ollama_enabled": False})
    prompt = await brain._build_system_prompt("olá", with_tools=True)
    assert prompt.index(NANO_PERSONA) < prompt.index("Trata o utilizador por \"senhor\"")


# ══ Protocols ═══════════════════════════════════════════════════════════

class _Executor:
    def __init__(self, deny=()):
        self.calls: list[tuple[str, dict]] = []
        self.deny = set(deny)
        # A protocol said in the chat goes through Brain._run_tool, which
        # reads the registry; every name counts as registered here.
        self.registry = _Everything()

    async def execute_tool_async(self, name, args=None, **_kw):
        self.calls.append((name, dict(args or {})))
        if name == "pc_window_list":
            return {"success": True, "output": {"ok": True, "windows": [{"process": "discord"}]}}
        if name in self.deny:
            return {"success": False, "status": "permission_denied", "error": "Recusado"}
        return {"success": True, "status": "completed", "output": {"ok": True}}


class _Everything(dict):
    def __contains__(self, _name):
        return True


def _store(tmp_path) -> protocols.ProtocolStore:
    return protocols.ProtocolStore(tmp_path / "protocols.json")


STUDY = {"name": "Modo estudo", "phrases": ["modo estudo", "vou estudar"],
         "close_apps": ["discord"], "open_apps": ["Notion"], "open_urls": ["https://example.com"],
         "volume": 20, "media": "play_pause", "settings_section": "notifications",
         "lock": False, "message": "Bom estudo!"}


def test_phrases_match_whole_messages_only(tmp_path):
    store = _store(tmp_path)
    store.save(STUDY)
    assert store.match("modo estudo")["name"] == "Modo estudo"
    assert store.match("Ei NANO, ativa o modo estudo, por favor!")["name"] == "Modo estudo"
    assert store.match("Vou estudar.")["name"] == "Modo estudo"
    assert store.match("o que é o modo estudo?") is None
    assert store.match("modo estudo para amanhã às 9") is None


def test_a_protocol_compiles_to_narrow_tools_and_refuses_paths_and_shells(tmp_path):
    plan = protocols.compile_protocol(protocols.validate(STUDY))
    assert [s["tool"] for s in plan["steps"]] == [
        "pc_window_batch_close", "pc_app_launch", "pc_web_open_url", "pc_volume_set",
        "pc_settings_open", "pc_media_control"]
    for bad in ({"open_apps": ["C:\\\\Windows\\\\cmd.exe"]}, {"open_apps": ["powershell"]},
                {"open_urls": ["file:///C:/x"]}, {"volume": 300}, {"media": "format"}):
        with pytest.raises(protocols.ProtocolError):
            protocols.validate({**STUDY, **bad})


def test_two_protocols_cannot_share_a_phrase(tmp_path):
    store = _store(tmp_path)
    store.save(STUDY)
    with pytest.raises(protocols.ProtocolError):
        store.save({**STUDY, "name": "Outro", "phrases": ["Modo Estudo!"]})


def test_save_edit_delete_and_forget(tmp_path):
    store = _store(tmp_path)
    saved = store.save(STUDY)
    store.save({**saved, "message": "Força!"})
    assert len(store.list()) == 1 and store.list()[0]["message"] == "Força!"
    assert store.delete(saved["id"]) is True
    store.save(STUDY)
    store.forget()
    assert store.list() == [] and not (tmp_path / "protocols.json").exists()


def test_running_a_protocol_goes_through_the_executor(tmp_path):
    executor = _Executor(deny={"pc_web_open_url"})
    result = asyncio.run(protocols.run(protocols.validate(STUDY), executor))
    names = [name for name, _ in executor.calls]
    assert names[0] == "pc_window_list" and "pc_window_batch_close" in names
    assert result["protocol"] is True
    summary = protocols.spoken_summary(result)
    assert "Bom estudo!" in summary and "Não fiz:" in summary


def test_templates_follow_the_language():
    pt = protocols.templates("pt-PT")
    en = protocols.templates("en")
    assert [t["name"] for t in pt] == ["Modo estudo", "Boa noite", "Modo jogo"]
    assert en[1]["phrases"] == ["good night", "going to bed"]
    for template in pt + en:
        protocols.validate(template)


@pytest.mark.asyncio
async def test_the_brain_runs_a_protocol_without_asking_a_model(tmp_path, monkeypatch):
    from core.brain import Brain
    from core.memory import MemoryEngine

    store = _store(tmp_path)
    store.save(STUDY)
    monkeypatch.setattr(protocols, "STORE", store)
    from core.guardrails import GuardrailsEngine

    brain = Brain("", GuardrailsEngine(), MemoryEngine(), {"ollama_enabled": False})
    brain.route_for_async = AsyncMock(side_effect=AssertionError("the model was consulted"))
    brain.tool_executor = _Executor()
    done = MagicMock()
    brain.on_protocol_done = done
    tokens = [t async for t in brain.chat("modo estudo")]
    answer = "".join(t for t in tokens if not t.startswith("_thinking_:"))
    assert answer.startswith("Bom estudo!")
    assert brain.last_metadata["direct_action"] == "protocol"
    done.assert_called_once()


# ══ PC alerts ═══════════════════════════════════════════════════════════

def _alerts(**readings):
    emitted: list[dict] = []
    values = {"battery": None, "disk": None, "network": None, "downloads": None, **readings}
    settings = {"battery": True, "disk": True, "network": True, "downloads": True}
    clock = {"now": 1000.0}
    watcher = pc_alerts.PcAlerts(
        emit=emitted.append, settings=lambda: settings,
        battery=lambda: values["battery"], disk=lambda: values["disk"],
        network=lambda: values["network"], downloads=lambda: values["downloads"],
        clock=lambda: clock["now"])
    return watcher, values, emitted, settings, clock


def test_battery_warns_at_20_then_10_and_resets_on_the_charger():
    watcher, values, emitted, _s, _c = _alerts(battery={"percent": 50, "plugged": False})
    watcher.tick()
    values["battery"] = {"percent": 19, "plugged": False}
    watcher.tick(); watcher.tick()
    values["battery"] = {"percent": 9, "plugged": False}
    watcher.tick()
    assert [e["kind"] for e in emitted] == ["battery", "battery"]
    assert "19%" in emitted[0]["text"] and "9%" in emitted[1]["text"]
    values["battery"] = {"percent": 9, "plugged": True}
    watcher.tick()
    values["battery"] = {"percent": 18, "plugged": False}
    watcher.tick()
    assert len(emitted) == 3


def test_disk_warns_then_waits_six_hours():
    watcher, _v, emitted, _s, clock = _alerts(disk={"drive": "C:\\", "free_gb": 3.2, "free_percent": 1.4})
    watcher.tick(); watcher.tick()
    assert len(emitted) == 1 and "3.2" in emitted[0]["text"]
    clock["now"] += pc_alerts.DISK_REPEAT_SECONDS + 1
    watcher.tick()
    assert len(emitted) == 2


def test_network_drop_needs_two_readings_and_its_return_is_said():
    watcher, values, emitted, _s, _c = _alerts(network=False)
    watcher.tick()
    assert emitted == []                       # starting offline is not news
    values["network"] = True
    watcher.tick()
    values["network"] = False
    watcher.tick()
    assert emitted == []                       # one bad reading: a hiccup
    watcher.tick()
    assert [e["title"] for e in emitted] == ["Sem rede"]
    watcher.tick()
    values["network"] = True
    watcher.tick()
    assert [e["title"] for e in emitted] == ["Sem rede", "Rede"]


def test_a_download_is_announced_once_when_it_stops_growing():
    watcher, values, emitted, _s, _c = _alerts(downloads={"old.pdf": (10, 1.0)})
    watcher.tick()                              # what was there is old news
    values["downloads"] = {"old.pdf": (10, 1.0), "film.mkv.crdownload": (5, 2.0), "doc.pdf": (5, 2.0)}
    watcher.tick()
    assert emitted == []                        # still possibly growing
    values["downloads"] = {"old.pdf": (10, 1.0), "film.mkv.crdownload": (9, 3.0), "doc.pdf": (5, 2.0)}
    watcher.tick()
    watcher.tick()
    assert [e["kind"] for e in emitted] == ["download"]
    assert "doc.pdf" in emitted[0]["text"]


def test_switched_off_alerts_stay_quiet():
    watcher, _v, emitted, settings, _c = _alerts(battery={"percent": 5, "plugged": False},
                                                 disk={"drive": "/", "free_gb": 1, "free_percent": 1})
    settings.update(battery=False, disk=False)
    watcher.tick()
    assert emitted == []


def test_pc_alert_switches_are_allowed_settings():
    from core import user_settings

    for key in ("battery", "disk", "network", "downloads"):
        assert f"pc_alerts_{key}" in user_settings.ALLOWED_KEYS
