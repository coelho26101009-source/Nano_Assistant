"""NANO's speaking voice: engines, the fallback chain, the offline pack.

What these hold (core/speech_engines.py, core/voice_local_pack.py):

* NANO is never mute while ANY engine can speak: a failed engine hands over to
  the next one, and a failing one is skipped for a while instead of making
  every sentence wait for it.
* "auto" never spends the person's ElevenLabs credits; LOCAL mode prefers the
  offline voice only when the voice for the current language is on disk.
* A reply is spoken piece by piece, the next piece synthesised WHILE the
  current one plays, and Stop really stops the pieces that were coming.
* The offline pack only installs what the lock pins and only extracts the
  model folder, after the SHA-256 matches.
* The Windows voice never gets the text on its command line.
"""
from __future__ import annotations

import asyncio
import importlib.machinery
import io
import os
import sys
import tarfile
import types
import wave
from pathlib import Path

import pytest

from core import speech_engines as se
from core import voice_local_pack as pack_mod


# ── fakes ────────────────────────────────────────────────────────────────

class FakeEngine(se.Engine):
    def __init__(self, name: str, *, ok: bool = True, available: bool = True,
                 reason: str = "network", cooldown: float = se.COOLDOWN_NETWORK, log=None, delay=0.0):
        self.id = name
        self.ok = ok
        self._available = available
        self.reason = reason
        self.cooldown = cooldown
        self.calls: list[str] = []
        self.log = log if log is not None else []
        self.delay = delay

    def available(self) -> bool:
        return self._available

    def voice_label(self, request):
        return self.id.title()

    async def synthesize(self, text, request):
        self.calls.append(text)
        self.log.append(("synth-start", self.id, text))
        if self.delay:
            await asyncio.sleep(self.delay)
        if not self.ok:
            raise se.SynthesisError(f"{self.id} down", reason=self.reason, cooldown=self.cooldown)
        path = se.scratch_path(".wav")
        with open(path, "wb") as handle:
            handle.write(b"RIFF")
        self.log.append(("synth-end", self.id, text))
        return path


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def chain_of(*engines: FakeEngine, clock: Clock | None = None) -> se.SpeechChain:
    return se.SpeechChain(engines={e.id: e for e in engines}, clock=clock or Clock())


def run(coro):
    return asyncio.run(coro)


# ── pieces of speech ─────────────────────────────────────────────────────

def test_the_first_piece_is_short_and_the_rest_are_grouped():
    text = ("Claro. Abri o Spotify e pus a playlist de estudo. Também verifiquei o tempo: "
            "estão dezoito graus no Porto. Amanhã chove. Queres que te lembre do trabalho?")
    pieces = se.split_for_speech(text)
    assert pieces[0] == "Claro. Abri o Spotify e pus a playlist de estudo."
    assert " ".join(pieces) == " ".join(text.split())
    assert all(len(p) <= se.PIECE_MAX for p in pieces[1:])


def test_a_single_sentence_is_one_piece():
    assert se.split_for_speech("Feito.") == ["Feito."]
    assert se.split_for_speech("   ") == []


# ── settings ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [("+10%", 10), ("-5%", -5), (15, 15), ("99", 30),
                                            (-80, -30), ("rápido", 0), (True, 0), (None, 0)])
def test_speed_is_parsed_and_clamped(value, expected):
    assert se.parse_speed(value) == expected


def test_settings_are_validated():
    assert se.valid_setting("tts_engine", "local") is True
    assert se.valid_setting("tts_engine", "gemini") is False
    assert se.valid_setting("tts_local_voice", "george") is True
    assert se.valid_setting("tts_local_voice", "../x") is False
    assert se.valid_setting("tts_elevenlabs_voice", "onwK4e9ZLuTAKqWW03F9") is True
    assert se.valid_setting("tts_elevenlabs_voice", "a/b?c=1") is False
    assert se.valid_setting("tts_elevenlabs_model", "eleven_multilingual_v2") is True
    assert se.valid_setting("tts_speed", 10) is True
    assert se.valid_setting("tts_speed", 31) is False
    assert se.valid_setting("tts_speed", True) is False
    assert se.valid_setting("something_else", 1) is None


def test_user_settings_accept_the_speech_keys():
    from core import user_settings

    assert user_settings._valid_value("tts_engine", "elevenlabs")
    assert user_settings._valid_value("tts_speed", -10)
    assert not user_settings._valid_value("tts_engine", "rm -rf")


def test_the_local_voice_follows_the_language():
    request = se.SpeechRequest(language="en", local_voice="dii")
    assert se.local_voice_for(request) == "george"       # dii speaks Portuguese
    request = se.SpeechRequest(language="pt-PT", local_voice="")
    assert se.local_voice_for(request) == "dii"
    assert {v["id"] for v in se.local_voice_options("en")} >= {"george", "lewis"}


def test_the_request_reads_the_live_config():
    request = se.SpeechRequest.from_config({"voice": "en-GB-RyanNeural", "engine": "local",
                                            "speed": "+10%"})
    assert request.language == "en" and request.engine == "local" and request.speed == 10
    assert se.SpeechRequest.from_config({"engine": "nope"}).engine == "auto"


# ── the chain ────────────────────────────────────────────────────────────

def test_auto_never_includes_elevenlabs():
    chain = chain_of(*(FakeEngine(n) for n in ("edge", "local", "elevenlabs", "windows")))
    assert chain.order(se.SpeechRequest(engine="auto")) == ["edge", "local", "windows"]
    assert chain.order(se.SpeechRequest(engine="elevenlabs")) == ["elevenlabs", "edge", "local", "windows"]


def test_local_mode_puts_the_offline_voice_first_only_when_it_is_installed(monkeypatch):
    local = se.LocalEngine()
    chain = se.SpeechChain(engines={"edge": FakeEngine("edge"), "local": local, "windows": FakeEngine("windows")})
    request = se.SpeechRequest(engine="auto", local_mode=True, language="pt-PT")
    monkeypatch.setattr(local, "ready_for", lambda language: False)
    assert chain.order(request) == ["edge", "local", "windows"]
    monkeypatch.setattr(local, "ready_for", lambda language: True)
    assert chain.order(request) == ["local", "edge", "windows"]


def test_a_failing_engine_hands_over_to_the_next():
    edge = FakeEngine("edge", ok=False)
    local = FakeEngine("local")
    chain = chain_of(edge, local, FakeEngine("windows"))
    path, used = run(chain.synthesize("Olá.", se.SpeechRequest()))
    os.unlink(path)
    assert used == "local" and edge.calls == ["Olá."]
    assert chain.status()["engines"]["edge"]["lastError"] == "network"
    assert chain.last_fallback_reason == "edge:network"


def test_a_failed_engine_is_skipped_while_it_cools_down():
    clock = Clock()
    edge = FakeEngine("edge", ok=False)
    chain = chain_of(edge, FakeEngine("local"), clock=clock)
    for _ in range(3):
        os.unlink(run(chain.synthesize("Olá.", se.SpeechRequest()))[0])
    assert len(edge.calls) == 1, "offline, every sentence waited for Edge to fail again"
    clock.now += se.COOLDOWN_NETWORK + 1
    edge.ok = True
    path, used = run(chain.synthesize("Olá.", se.SpeechRequest()))
    os.unlink(path)
    assert used == "edge"


def test_the_last_engine_standing_is_tried_even_while_cooling():
    edge = FakeEngine("edge", ok=False)
    chain = chain_of(edge)
    with pytest.raises(se.SynthesisError):
        run(chain.synthesize("Olá.", se.SpeechRequest()))
    edge.ok = True
    os.unlink(run(chain.synthesize("Olá.", se.SpeechRequest()))[0])
    assert len(edge.calls) == 2


def test_every_engine_failing_raises():
    chain = chain_of(FakeEngine("edge", ok=False), FakeEngine("local", available=False))
    with pytest.raises(se.SynthesisError) as info:
        run(chain.synthesize("Olá.", se.SpeechRequest()))
    assert info.value.reason == "all_failed"


def test_an_unexpected_engine_bug_does_not_silence_nano():
    class Broken(FakeEngine):
        async def synthesize(self, text, request):
            raise KeyError("bug")

    chain = chain_of(Broken("edge"), FakeEngine("local"))
    path, used = run(chain.synthesize("Olá.", se.SpeechRequest()))
    os.unlink(path)
    assert used == "local"


def test_reset_forgets_failures():
    edge = FakeEngine("edge", ok=False)
    chain = chain_of(edge, FakeEngine("local"))
    os.unlink(run(chain.synthesize("Olá.", se.SpeechRequest()))[0])
    chain.reset("edge")
    edge.ok = True
    assert run(chain.synthesize("Olá.", se.SpeechRequest()))[1] == "edge"


# ── the provider: pipelined pieces, stop ─────────────────────────────────

class FakeMusic:
    def __init__(self, log):
        self.log = log
        self.loaded = None

    def load(self, path):
        self.loaded = path

    def play(self):
        self.log.append(("play", os.path.basename(self.loaded)))

    def get_busy(self):
        return False

    def unload(self):
        pass

    def stop(self):
        self.log.append(("stop",))


@pytest.fixture
def fake_pygame(monkeypatch):
    log: list = []
    module = types.ModuleType("pygame")
    module.__spec__ = importlib.machinery.ModuleSpec("pygame", None)
    module.mixer = types.SimpleNamespace(get_init=lambda: True, init=lambda **k: None, music=FakeMusic(log))
    monkeypatch.setitem(sys.modules, "pygame", module)
    return log


def test_the_next_piece_is_synthesised_while_the_current_one_plays(fake_pygame, monkeypatch):
    from core import voice

    log = fake_pygame
    engine = FakeEngine("edge", log=log)
    provider = voice.LocalTTSProvider({}, chain=chain_of(engine))
    provider.online = True
    played: list[str] = []

    async def play(path, **_):
        log.append(("play-start", path))
        await asyncio.sleep(0.05)
        played.append(path)

    monkeypatch.setattr(voice, "_play_audio_file", play)
    text = "Primeira frase bastante comprida aqui. Segunda frase. " + "Terceira frase longa. " * 20
    assert run(provider.speak(text)) is True
    pieces = se.split_for_speech(text)
    assert len(pieces) >= 3 and engine.calls == pieces
    first_play = next(i for i, e in enumerate(log) if e[0] == "play-start")
    second_synth = [i for i, e in enumerate(log) if e[0] == "synth-start"][1]
    assert second_synth < first_play + 2, "piece 2 must start while piece 1 is playing"
    assert all(not os.path.exists(p) for p in played)


def test_stop_ends_the_pieces_that_were_coming(fake_pygame, monkeypatch):
    from core import voice

    engine = FakeEngine("edge")
    provider = voice.LocalTTSProvider({}, chain=chain_of(engine))
    provider.online = True
    plays: list[str] = []

    async def play(path, **_):
        plays.append(path)
        provider.stop()                     # the person pressed Stop mid-sentence

    monkeypatch.setattr(voice, "_play_audio_file", play)
    text = "Primeira frase bastante comprida aqui. " + "Mais uma frase. " * 30

    async def go():
        result = await provider.speak(text)
        await asyncio.sleep(0.05)           # let the cancelled piece settle
        return result

    assert run(go()) is True
    assert len(plays) == 1


def test_when_every_engine_fails_the_provider_reports_it(fake_pygame):
    from core import voice

    provider = voice.LocalTTSProvider({}, chain=chain_of(FakeEngine("edge", ok=False)))
    provider.online = True
    with pytest.raises(voice.ProviderError):
        run(provider.speak("Olá."))


def test_a_failure_after_the_first_piece_keeps_what_was_said(fake_pygame, monkeypatch):
    from core import voice

    class Flaky(FakeEngine):
        async def synthesize(self, text, request):
            if self.calls:
                self.calls.append(text)
                raise se.SynthesisError("down")
            return await super().synthesize(text, request)

    provider = voice.LocalTTSProvider({}, chain=chain_of(Flaky("edge")))
    provider.online = True

    async def play(path, **_):
        return None

    monkeypatch.setattr(voice, "_play_audio_file", play)
    assert run(provider.speak("Primeira frase bastante comprida aqui. Segunda frase que falha.")) is True


def test_the_name_in_capitals_is_not_spelled_out():
    from core.voice import speech_text

    assert speech_text("O NANO está pronto.") == "O Nano está pronto."
    assert "NANOS" in speech_text("NANOS")


# ── ElevenLabs ───────────────────────────────────────────────────────────

class FakeResponse:
    def __init__(self, status: int, content: bytes = b"", data=None, text: str = ""):
        self.status_code = status
        self.content = content
        self._data = data
        self.text = text

    def json(self):
        return self._data


class FakeClient:
    def __init__(self, response: FakeResponse, seen: list):
        self.response = response
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, params=None, headers=None, json=None):
        self.seen.append(("post", url, params, headers, json))
        return self.response

    async def get(self, url, headers=None):
        self.seen.append(("get", url, headers))
        return self.response


def eleven(response: FakeResponse, key: str = "sk_test_key_123456"):
    seen: list = []
    return se.ElevenLabsEngine(key=lambda: key, client_factory=lambda: FakeClient(response, seen)), seen


def test_elevenlabs_speaks_with_the_chosen_voice_and_language():
    engine, seen = eleven(FakeResponse(200, b"ID3audio"))
    request = se.SpeechRequest(language="pt-PT", elevenlabs_voice="abcdefgh1234", speed=10)
    path = run(engine.synthesize("Olá.", request))
    with open(path, "rb") as handle:
        assert handle.read() == b"ID3audio"
    os.unlink(path)
    _kind, url, params, headers, body = seen[0]
    assert url.endswith("/text-to-speech/abcdefgh1234")
    assert headers["xi-api-key"] == "sk_test_key_123456"
    assert body["language_code"] == "pt" and body["model_id"] == "eleven_flash_v2_5"
    assert body["voice_settings"]["speed"] == 1.1


def test_elevenlabs_multilingual_v2_gets_no_language_code():
    engine, _ = eleven(FakeResponse(200, b"x"))
    body = engine.payload("Hi.", se.SpeechRequest(elevenlabs_model="eleven_multilingual_v2"))
    assert "language_code" not in body


def test_an_invalid_voice_id_falls_back_to_the_default_voice():
    engine, seen = eleven(FakeResponse(200, b"x"))
    os.unlink(run(engine.synthesize("Hi.", se.SpeechRequest(elevenlabs_voice="../../v1/user"))))
    assert seen[0][1].endswith("/" + se.DEFAULT_ELEVENLABS_VOICE)


@pytest.mark.parametrize("status,reason,cooldown", [(401, "auth", se.COOLDOWN_AUTH),
                                                   (429, "quota", se.COOLDOWN_QUOTA),
                                                   (402, "quota", se.COOLDOWN_QUOTA),
                                                   (500, "network", se.COOLDOWN_NETWORK)])
def test_elevenlabs_failures_are_classified(status, reason, cooldown):
    engine, _ = eleven(FakeResponse(status))
    with pytest.raises(se.SynthesisError) as info:
        run(engine.synthesize("Olá.", se.SpeechRequest()))
    assert info.value.reason == reason and info.value.cooldown == cooldown


def test_elevenlabs_without_a_key_is_unavailable():
    engine, _ = eleven(FakeResponse(200), key="")
    assert engine.available() is False


def test_elevenlabs_voices_are_listed_and_sanitised():
    data = {"voices": [
        {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel", "labels": {"accent": "british", "gender": "male"}},
        {"voice_id": "bad/id", "name": "Evil"},
        {"voice_id": "abcdefgh1234", "name": "  Joana \n PT ", "labels": {}},
    ]}
    engine, _ = eleven(FakeResponse(200, data=data))
    voices = run(engine.list_voices())
    assert voices == [{"id": "onwK4e9ZLuTAKqWW03F9", "label": "Daniel (british, male)"},
                      {"id": "abcdefgh1234", "label": "Joana PT"}]


# ── Windows voice ────────────────────────────────────────────────────────

def test_the_windows_voice_reads_the_text_from_a_file_never_the_command_line(monkeypatch):
    captured = {}

    def runner(command, **kwargs):
        env = kwargs["env"]
        with open(env["NANO_TTS_IN"], encoding="utf-8") as handle:
            captured["text"] = handle.read()
        captured["command"] = command
        captured["env"] = env
        with open(env["NANO_TTS_OUT"], "wb") as handle:
            handle.write(b"\x00" * 100)
        return types.SimpleNamespace(returncode=0)

    engine = se.WindowsEngine(runner=runner)
    path = run(engine.synthesize("Olá, \"; Remove-Item C:\\ -Recurse #", se.SpeechRequest(speed=20)))
    os.unlink(path)
    assert captured["text"].startswith("Olá")
    assert all("Remove-Item" not in part for part in captured["command"])
    assert captured["env"]["NANO_TTS_CULTURE"] == "pt" and captured["env"]["NANO_TTS_RATE"] == "2"
    assert not os.path.exists(captured["env"]["NANO_TTS_IN"]), "the text file is deleted"


def test_the_windows_voice_failing_cleans_up():
    engine = se.WindowsEngine(runner=lambda command, **kw: types.SimpleNamespace(returncode=1))
    with pytest.raises(se.SynthesisError):
        run(engine.synthesize("Olá.", se.SpeechRequest()))


def test_the_windows_voice_is_off_in_the_test_suite():
    assert se.WindowsEngine().available() is False


# ── the offline voice ────────────────────────────────────────────────────

def test_write_wav_makes_a_valid_16_bit_mono_file(tmp_path):
    path = tmp_path / "a.wav"
    se.write_wav(str(path), [0.0, 0.5, -0.5, 2.0], 24000)
    with wave.open(str(path)) as reader:
        assert (reader.getnchannels(), reader.getsampwidth(), reader.getframerate(), reader.getnframes()) == (1, 2, 24000, 4)


def _fake_model(base: Path, model: str) -> None:
    spec = pack_mod.MODELS[model]
    folder = base / spec.name
    (folder / "espeak-ng-data").mkdir(parents=True)
    for name in pack_mod._REQUIRED[spec.kind]:
        if name != "espeak-ng-data":
            (folder / name).write_text("x")
    if spec.kind == "piper":
        (folder / "pt_PT-dii-high.onnx").write_text("x")


def test_the_local_engine_synthesises_with_sherpa(monkeypatch, tmp_path):
    _fake_model(tmp_path, "piper-pt_PT-dii-high")
    made = {}

    class OfflineTts:
        def __init__(self, config):
            made["config"] = config

        def generate(self, text, sid=0, speed=1.0):
            made["call"] = (text, sid, speed)
            return types.SimpleNamespace(samples=[0.0, 0.1, -0.1], sample_rate=22050)

    fake = types.ModuleType("sherpa_onnx")
    fake.__spec__ = importlib.machinery.ModuleSpec("sherpa_onnx", None)
    fake.OfflineTts = OfflineTts
    for name in ("OfflineTtsConfig", "OfflineTtsModelConfig", "OfflineTtsVitsModelConfig",
                 "OfflineTtsKokoroModelConfig"):
        setattr(fake, name, lambda **kw: dict(kw))
    monkeypatch.setitem(sys.modules, "sherpa_onnx", fake)
    engine = se.LocalEngine(models_dir=lambda: tmp_path, threads=2)
    assert engine.available() and engine.ready_for("pt-PT") and not engine.ready_for("en")
    path = run(engine.synthesize("Olá.", se.SpeechRequest(language="pt-PT", speed=-10)))
    with wave.open(path) as reader:
        assert reader.getframerate() == 22050
    os.unlink(path)
    assert made["call"] == ("Olá.", 0, 0.9)
    assert made["config"]["model"]["vits"]["model"].endswith("pt_PT-dii-high.onnx")
    with pytest.raises(se.SynthesisError) as info:
        run(engine.synthesize("Hi.", se.SpeechRequest(language="en")))
    assert info.value.reason == "not_installed" and info.value.cooldown == 0


def test_the_pack_installs_only_the_locked_sherpa_closure():
    lock = pack_mod.voice_setup.OPTIONAL_LOCK.read_text(encoding="utf-8")
    text = pack_mod.voice_setup.requirements_text(lock, root=pack_mod.PACKAGE)
    names = sorted(line.split("==")[0] for line in text.splitlines() if "==" in line)
    assert names == ["sherpa-onnx", "sherpa-onnx-core"]
    assert "sherpa-onnx==1.13." in text and "--hash=sha256:" in text


def _archive(tmp_path: Path, model: str, *, extra=()) -> bytes:
    spec = pack_mod.MODELS[model]
    src = tmp_path / "src"
    _fake_model(src, model)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:bz2") as tar:
        tar.add(src / spec.name, arcname=spec.name)
        for name, data in extra:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"{spec.name}/evil-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
    return buffer.getvalue()


def _pack(tmp_path: Path, payload: bytes, model: str, monkeypatch):
    import hashlib

    base = tmp_path / "voices"
    spec = pack_mod.MODELS[model]
    monkeypatch.setitem(pack_mod.MODELS, model, pack_mod.VoiceModel(
        name=spec.name, kind=spec.kind, language=spec.language,
        sha256=hashlib.sha256(payload).hexdigest(), size_mb=1, licence=spec.licence))
    fetched = []

    def fetch(url, target, progress):
        fetched.append(url)
        target.write_bytes(payload)
        progress(len(payload))

    pack = pack_mod.LocalVoicePack(language=lambda: spec.language, base=lambda: base,
                                   fetch=fetch, installed=lambda: True)
    return pack, base, fetched


def test_the_pack_downloads_verifies_and_extracts_only_the_model(tmp_path, monkeypatch):
    model = "piper-pt_PT-dii-high"
    payload = _archive(tmp_path, model, extra=[("../outside.txt", b"no"), ("other/x.txt", b"no")])
    pack, base, fetched = _pack(tmp_path, payload, model, monkeypatch)
    assert pack.start()["ok"]
    pack.wait(10)
    status = pack.status()
    assert status["install"]["state"] == "done" and status["ready"] is True
    assert fetched == [pack_mod.MODELS[model].url]
    assert fetched[0].startswith("https://github.com/k2-fsa/sherpa-onnx/releases/download/")
    assert not (tmp_path / "outside.txt").exists() and not (base / "outside.txt").exists()
    assert not (base / "other").exists()
    assert not (base / pack_mod.MODELS[model].name / "evil-link").exists()
    assert sorted(p.name for p in base.iterdir()) == [pack_mod.MODELS[model].name], "no temp folder left"


def test_a_checksum_mismatch_installs_nothing(tmp_path, monkeypatch):
    model = "piper-pt_PT-dii-high"
    payload = _archive(tmp_path, model)
    pack, base, _ = _pack(tmp_path, payload, model, monkeypatch)
    monkeypatch.setitem(pack_mod.MODELS, model, pack_mod.MODELS[model].__class__(
        **{**pack_mod.MODELS[model].__dict__, "sha256": "0" * 64}))
    pack.start()
    pack.wait(10)
    assert pack.status()["install"]["state"] == "error"
    assert not pack_mod.model_ready(model, base)


def test_the_real_model_list_is_pinned():
    for model in pack_mod.MODELS.values():
        assert len(model.sha256) == 64 and model.url.endswith(".tar.bz2")
    assert {v[0] for v in se.LOCAL_VOICES.values()} == set(pack_mod.MODELS)
    assert pack_mod.LANGUAGE_MODEL == {"pt-PT": "piper-pt_PT-dii-high", "en": "kokoro-multi-lang-v1_0"}
