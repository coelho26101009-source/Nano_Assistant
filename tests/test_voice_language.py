"""Voice follows the interface language: transcriber, voice and wake phrase."""
from __future__ import annotations

from core import voice_language
from core.voice import LocalSTTProvider, LocalTTSProvider
from core.wake_phrase import WakePhraseEngine


def _defaults() -> dict:
    return {"stt": {"language": "pt", "vocabulary_hint": voice_language.PROFILES["pt-PT"].vocabulary_hint},
            "tts": {"voice": "pt-PT-DuarteNeural"}}


def test_english_switches_every_untouched_setting():
    cfg = _defaults()
    changed = voice_language.apply(cfg, "en")
    assert cfg["stt"]["language"] == "en"
    assert cfg["tts"]["voice"].startswith("en-")
    assert cfg["wake_phrase"] == "hey nano"
    assert cfg["stt"]["vocabulary_hint"].startswith("Vocabulary:")
    assert set(changed) == {"stt_language", "tts_voice", "wake_phrase", "vocabulary_hint"}


def test_and_back_to_portuguese():
    cfg = _defaults()
    voice_language.apply(cfg, "en")
    voice_language.apply(cfg, "pt-PT")
    assert cfg["stt"]["language"] == "pt" and cfg["tts"]["voice"] == "pt-PT-DuarteNeural"
    assert cfg["wake_phrase"] == "ei nano"
    # The Portuguese hint is the one the STT benchmark measured.
    assert cfg["stt"]["vocabulary_hint"].startswith("Vocabulário:")


def test_what_the_person_changed_is_kept():
    cfg = _defaults()
    cfg["tts"]["voice"] = "pt-BR-AntonioNeural"
    cfg["wake_phrase"] = "acorda jarvis"
    voice_language.apply(cfg, "en")
    assert cfg["tts"]["voice"] == "pt-BR-AntonioNeural"
    assert cfg["wake_phrase"] == "acorda jarvis"
    assert cfg["stt"]["language"] == "en"


def test_the_old_pt_PT_spelling_counts_as_untouched():
    cfg = {"stt": {"language": "pt-PT"}, "tts": {}}
    voice_language.apply(cfg, "en")
    assert cfg["stt"]["language"] == "en"


def test_an_unknown_language_falls_back_to_english():
    assert voice_language.profile("fr") is voice_language.PROFILES["en"]


def test_providers_read_the_language_live():
    cfg = _defaults()
    stt, tts = LocalSTTProvider(cfg["stt"]), LocalTTSProvider(cfg["tts"])
    assert stt.decode_language == "pt" and tts.voice_name == "pt-PT-DuarteNeural"
    voice_language.apply(cfg, "en")
    assert stt.decode_language == "en" and tts.voice_name.startswith("en-")
    assert stt.vocabulary_hint.startswith("Vocabulary:")


def test_the_wake_phrase_switches_while_running():
    class _Silent:
        _available = False
        online = False

    engine = WakePhraseEngine({"wake_phrase": "ei nano", "wake_phrase_allow_nano_only": False},
                              lambda _t: None, audio_provider=_Silent(), stt_provider=_Silent())
    assert engine.detector.matches("Ei, Nano!") and not engine.detector.matches("Hey Nano!")
    engine.set_phrase("hey nano")
    assert engine.detector.matches("Hey Nano!") and not engine.detector.matches("Ei, Nano!")


def test_every_english_candidate_phrase_is_matchable():
    from core.main import WAKE_PHRASE_CANDIDATES_EN
    from core.wake_phrase import WakePhraseDetector
    assert voice_language.PROFILES["en"].wake_phrase in WAKE_PHRASE_CANDIDATES_EN
    for candidate in WAKE_PHRASE_CANDIDATES_EN:
        detector = WakePhraseDetector(phrase=candidate, allow_nano_only=False, cooldown_seconds=0.0)
        assert detector.matches(f"{candidate.title()}!") and not detector.matches("nano")


def test_changing_the_interface_language_changes_the_voice(monkeypatch):
    from core import i18n, main
    cfg = main.CONFIG.setdefault("voice", {})
    monkeypatch.setitem(cfg, "stt", dict(cfg.get("stt") or {}, language="pt"))
    monkeypatch.setitem(cfg, "tts", dict(cfg.get("tts") or {}, voice="pt-PT-DuarteNeural"))
    monkeypatch.setattr(i18n, "current", lambda: "en")
    changed = main._apply_voice_language()
    assert cfg["stt"]["language"] == "en" and "stt_language" in changed
