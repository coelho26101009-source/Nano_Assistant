"""Settings → Voz → "Voz do NANO", end to end through core.main.

The choices land in the live ``voice.tts`` config the provider reads on every
answer, the card gets everything it shows (and never a key), and the
ElevenLabs key is checked before it is trusted.
"""
from __future__ import annotations

import pytest

from core import speech_engines, user_settings


@pytest.fixture()
def main(monkeypatch):
    import core.main as main

    keys = ("tts_engine", "tts_local_voice", "tts_speed", "tts_elevenlabs_model")
    yield main
    for key in keys:
        user_settings.reset(key)
    main._apply_tts_voice()
    main.voice.tts_provider.chain.reset()


def test_the_card_gets_everything_it_shows_and_no_key(main, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "sk_secret_value_1234567890")
    data = main.get_speech_settings()
    assert data["ok"] is True
    assert [e["id"] for e in data["engines"]] == list(speech_engines.ENGINES)
    assert data["engine"] == "auto"
    assert data["localVoice"] == "dii" and data["language"] == "pt-PT"
    assert data["elevenlabs"]["key"]["configured"] is True
    assert "sk_secret_value_1234567890" not in repr(data)
    assert {"ready", "install", "sizeMb"} <= set(data["localPack"])


def test_choices_reach_the_live_config(main):
    assert main.update_setting("tts_engine", "local")["ok"] is True
    assert main.update_setting("tts_speed", 15)["ok"] is True
    tts = main.CONFIG["voice"]["tts"]
    assert tts["engine"] == "local" and tts["speed"] == "+15%"
    request = main.voice.tts_provider.request()
    assert request.engine == "local" and request.speed == 15
    assert main.get_speech_settings()["speed"] == 15


def test_bad_choices_are_refused(main):
    assert main.update_setting("tts_engine", "powershell")["ok"] is False
    assert main.update_setting("tts_speed", 99)["ok"] is False
    assert main.update_setting("tts_local_voice", "../../etc")["ok"] is False


def test_an_invalid_key_is_not_stored(main, monkeypatch):
    stored = []
    monkeypatch.setattr(main.secret_store, "set_secret", lambda name, value: stored.append(value) or True)
    for bad in ("", "short", "has spaces in the key 12345", "x" * 300):
        assert main.set_elevenlabs_api_key(bad)["ok"] is False
    assert stored == []


def test_a_refused_key_is_rolled_back(main, monkeypatch):
    store: dict[str, str] = {}
    monkeypatch.setattr(main.secret_store, "get_secret", lambda name: store.get(name, ""))
    monkeypatch.setattr(main.secret_store, "set_secret", lambda name, value: store.__setitem__(name, value) or True)
    monkeypatch.setattr(main.secret_store, "delete_secret", lambda name: store.pop(name, None) or True)
    monkeypatch.setattr(main.secret_store, "describe", lambda name: {"configured": name in store})

    async def refused():
        raise speech_engines.SynthesisError("no", reason="auth", cooldown=0)

    engine = main.voice.tts_provider.chain.engines["elevenlabs"]
    monkeypatch.setattr(engine, "list_voices", refused)
    result = main.set_elevenlabs_api_key("sk_" + "a" * 30)
    assert result["ok"] is False and result["error"] == "auth"
    assert store == {}


def test_removing_the_key_leaves_elevenlabs(main, monkeypatch):
    monkeypatch.setattr(main.secret_store, "delete_secret", lambda name: True)
    user_settings.set_value("tts_engine", "elevenlabs")
    main._apply_tts_voice()
    main.remove_elevenlabs_api_key()
    assert main.CONFIG["voice"]["tts"]["engine"] == "auto"


def test_preview_refuses_an_unknown_engine(main):
    assert main.preview_voice("gemini")["error"] == "invalid_engine"
