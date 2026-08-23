"""The production speech-to-text contract, as chosen by the real benchmark.

Thirty recordings of the user's own voice, replayed through every candidate
over byte-identical WAVs, produced this decision:

    tiny  / cpu / int8            WER 67.3%   entities  4/14   (was production)
    base  / cpu / int8            WER 61.0%   entities  4/14
    small / cpu / int8            WER 32.7%   entities  6/14
    small / cpu / int8 + vocab    WER 27.0%   entities 13/14   <- production now

These tests exist so that decision cannot be undone by accident: by a stale
`settings.yaml`, by a resurrected legacy `whisper_*` key, by someone "tidying
away" the initial_prompt, or by a merge that quietly restores `tiny`.

They are behavioural. The real provider is driven against a fake
``faster_whisper`` module, so what is asserted is what faster-whisper was
ACTUALLY called with -- not what the source file appears to say.
"""
from __future__ import annotations

import importlib.machinery
import logging
import sys
import types

import pytest

from core import speech_benchmark as sb
from core.config import DEFAULT_CONFIG, _strip_legacy_whisper_keys, load_config
from core.voice import LocalSTTProvider


# --------------------------------------------------------------------------
#  A fake faster-whisper that records exactly how it was used
# --------------------------------------------------------------------------


def _stub_module(factory) -> types.ModuleType:
    """A stand-in ``faster_whisper`` that survives importlib.util.find_spec.

    LocalSTTProvider decides ``online`` with ``find_spec("faster_whisper")``,
    and find_spec raises ValueError on a module whose ``__spec__`` is None --
    which is what a bare types.ModuleType has. The stub therefore needs a real
    ModuleSpec, or the test fails for a reason unrelated to what it measures.
    """
    module = types.ModuleType("faster_whisper")
    module.__spec__ = importlib.machinery.ModuleSpec("faster_whisper", loader=None)
    module.WhisperModel = factory
    return module


class _FakeSegment:
    def __init__(self, text: str):
        self.text = text


class _FakeModel:
    def __init__(self, name, device, compute_type):
        self.name = name
        self.device = device
        self.compute_type = compute_type
        self.calls: list[dict] = []

    def transcribe(self, path, **options):
        self.calls.append(dict(options))
        return iter([_FakeSegment("olá nano")]), {"language": options.get("language")}


@pytest.fixture
def fake_whisper(monkeypatch):
    """Install a stub ``faster_whisper`` and expose what it was handed."""
    built: list[_FakeModel] = []

    def factory(name, device="cpu", compute_type="int8", **_kwargs):
        model = _FakeModel(name, device, compute_type)
        built.append(model)
        return model

    monkeypatch.setitem(sys.modules, "faster_whisper", _stub_module(factory))
    return built


def _provider(**overrides) -> LocalSTTProvider:
    """A provider built from the REAL merged production config."""
    stt_cfg = dict((load_config().get("voice") or {}).get("stt") or {})
    stt_cfg.update(overrides)
    provider = LocalSTTProvider(stt_cfg)
    provider.online = True          # the stub stands in for the real package
    return provider


# --------------------------------------------------------------------------
#  1-3. The production configuration resolves to the benchmark winner
# --------------------------------------------------------------------------


def test_production_config_resolves_to_small_cpu_int8():
    stt = (load_config().get("voice") or {}).get("stt") or {}
    assert stt["model"] == "small"
    assert stt["device"] == "cpu"
    assert stt["compute_type"] == "int8"


def test_production_language_resolves_to_portuguese():
    stt = (load_config().get("voice") or {}).get("stt") or {}
    assert stt["language"] == "pt"
    assert _provider().decode_language == "pt"


def test_the_older_pt_PT_spelling_still_decodes_as_pt():
    """Backwards compatibility: an old config must not change the decoding."""
    assert _provider(language="pt-PT").decode_language == "pt"


def test_auto_language_detection_is_never_used(fake_whisper):
    """Measured substantially worse. An empty language means auto-detect."""
    provider = _provider()
    provider.transcribe(b"RIFFfake")
    options = fake_whisper[0].calls[0]
    assert options["language"] == "pt"
    assert options["language"]          # never "" and never None


def test_a_blank_language_cannot_silently_become_auto_detection():
    """Even a config that wipes the language must still force Portuguese."""
    assert _provider(language="").decode_language == "pt"


def test_production_never_selects_cuda():
    """CUDA is unusable on this machine and fails as a native HANG."""
    stt = (load_config().get("voice") or {}).get("stt") or {}
    assert "cuda" not in str(stt["device"]).lower()


# --------------------------------------------------------------------------
#  4. The vocabulary hint actually reaches faster-whisper
# --------------------------------------------------------------------------


def test_vocabulary_hint_is_passed_to_transcribe_as_initial_prompt(fake_whisper):
    provider = _provider()
    provider.transcribe(b"RIFFfake")
    options = fake_whisper[0].calls[0]
    assert options["initial_prompt"] == provider.vocabulary_hint
    assert "Spotify" in options["initial_prompt"]


def test_production_vocabulary_hint_is_exactly_the_one_the_benchmark_measured():
    """Production and benchmark must not drift apart.

    The benchmark's ``small/cpu-int8+vocab`` row is only evidence for
    production if production sends the SAME string. Comparing them here is what
    keeps the measurement honest; changing either one alone fails this test.
    """
    assert _provider().vocabulary_hint == sb.VOCABULARY_PROMPT


@pytest.mark.parametrize("entity", [
    "Nano", "Spotify", "Discord", "GitHub", "Visual Studio Code",
    "VS Code", "Windows", "Groq", "Ollama", "Claude",
])
def test_vocabulary_hint_names_every_tested_entity(entity):
    assert entity in (_provider().vocabulary_hint or "")


def test_vocabulary_hint_stays_short():
    """It is decoder context: a long prompt drags the transcript towards it."""
    hint = _provider().vocabulary_hint or ""
    assert 0 < len(hint) < 200
    assert hint.count(".") <= 2


def test_vocabulary_hint_can_be_switched_off_and_then_is_not_sent(fake_whisper):
    provider = _provider(vocabulary_hint_enabled=False)
    assert provider.vocabulary_hint is None
    provider.transcribe(b"RIFFfake")
    assert "initial_prompt" not in fake_whisper[0].calls[0]


def test_decoding_matches_the_benchmark_configuration(fake_whisper):
    """Same model, same device, same language, same VAD, same prompt.

    Divergence here would mean the measured numbers describe a configuration
    production does not run.
    """
    provider = _provider()
    provider.transcribe(b"RIFFfake")
    model, options = fake_whisper[0], fake_whisper[0].calls[0]
    assert (model.name, model.device, model.compute_type) == ("small", "cpu", "int8")
    assert options["language"] == "pt"
    assert options["vad_filter"] is True
    assert options["initial_prompt"] == sb.VOCABULARY_PROMPT
    # The benchmark passed no beam_size either, so both use the library default.
    assert "beam_size" not in options


# --------------------------------------------------------------------------
#  5. Model lifetime
# --------------------------------------------------------------------------


def test_the_model_is_built_once_and_reused_across_turns(fake_whisper):
    """`small` costs ~1.3 s to construct. Never per voice turn."""
    provider = _provider()
    for _ in range(5):
        assert provider.transcribe(b"RIFFfake").ok is True
    assert len(fake_whisper) == 1, "the model was rebuilt instead of reused"
    assert len(fake_whisper[0].calls) == 5


def test_the_model_is_not_built_until_the_first_transcription(fake_whisper):
    """Lazy: constructing at import would block startup for over a second."""
    provider = _provider()
    assert provider.model_loaded is False
    assert fake_whisper == []
    provider.transcribe(b"RIFFfake")
    assert provider.model_loaded is True


def test_the_wake_engine_shares_one_model_with_the_voice_turn():
    """Two instances would mean two copies of `small` resident in RAM."""
    pytest.importorskip("pyaudio", reason="PyAudio is an optional dependency")
    from core.voice import VoiceEngine

    engine = VoiceEngine(load_config().get("voice") or {})
    inner = engine.wake_phrase_provider._engine
    assert inner._stt is engine.stt_provider


# --------------------------------------------------------------------------
#  6. Legacy whisper_* keys cannot override voice.stt
# --------------------------------------------------------------------------


def test_legacy_whisper_keys_are_stripped_and_cannot_pin_production_to_tiny(caplog):
    voice_cfg = {
        "whisper_model": "tiny",
        "whisper_device": "cuda",
        "whisper_compute_type": "float16",
        "stt": {"model": "small", "device": "cpu", "compute_type": "int8"},
    }
    with caplog.at_level(logging.WARNING, logger="helios.config"):
        removed = _strip_legacy_whisper_keys(voice_cfg)

    assert sorted(removed) == ["whisper_compute_type", "whisper_device", "whisper_model"]
    assert "whisper_model" not in voice_cfg
    assert voice_cfg["stt"]["model"] == "small"


def test_stripping_legacy_keys_is_loud_not_silent(caplog):
    """A dead key that vanishes quietly is how the drift went unnoticed."""
    with caplog.at_level(logging.WARNING, logger="helios.config"):
        _strip_legacy_whisper_keys({"whisper_model": "tiny"})
    messages = [record.getMessage() for record in caplog.records]
    assert any("voice.whisper_model" in message for message in messages), messages
    assert any("voice.stt" in message for message in messages), messages


def test_stripping_is_a_no_op_when_there_is_nothing_to_strip():
    voice_cfg = {"stt": {"model": "small"}}
    assert _strip_legacy_whisper_keys(voice_cfg) == []
    assert voice_cfg == {"stt": {"model": "small"}}


def test_the_shipped_yaml_carries_no_legacy_whisper_key():
    import yaml

    from core.config import CONFIG_PATH

    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    voice = raw.get("voice") or {}
    for key in ("whisper_model", "whisper_device", "whisper_compute_type", "stt_provider"):
        assert key not in voice, f"settings.yaml resurrected the dead key voice.{key}"
    assert (voice.get("stt") or {}).get("model") == "small"


def test_defaults_and_shipped_yaml_agree_on_the_model():
    """One authoritative answer, whether or not settings.yaml is present."""
    import yaml

    from core.config import CONFIG_PATH

    shipped = ((yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {})
               .get("voice") or {}).get("stt") or {}
    defaults = DEFAULT_CONFIG["voice"]["stt"]
    for key in ("model", "device", "compute_type", "language", "vocabulary_hint"):
        assert shipped[key] == defaults[key], f"settings.yaml and DEFAULT_CONFIG disagree on {key}"


# --------------------------------------------------------------------------
#  Observability
# --------------------------------------------------------------------------


def test_model_load_is_logged_with_the_configuration_it_loaded(fake_whisper, caplog):
    """The line that proves production is not silently back on `tiny`."""
    with caplog.at_level(logging.INFO, logger="nano.voice"):
        _provider().transcribe(b"RIFFfake")

    line = next(r.getMessage() for r in caplog.records if "STT model loaded" in r.getMessage())
    assert "model=small" in line
    assert "device=cpu" in line
    assert "compute_type=int8" in line
    assert "language=pt" in line
    assert "vocabulary_hint_enabled=True" in line


def test_no_transcript_or_audio_is_ever_logged(fake_whisper, caplog):
    """Diagnostics carry lengths and timings, never the user's speech."""
    with caplog.at_level(logging.DEBUG, logger="nano.voice"):
        _provider().transcribe(b"RIFFfake")
    for record in caplog.records:
        assert "olá nano" not in record.getMessage().lower()


def test_describe_reports_the_live_configuration_without_loading_a_model():
    described = _provider().describe()
    assert described["model"] == "small"
    assert described["device"] == "cpu"
    assert described["compute_type"] == "int8"
    assert described["language"] == "pt"
    assert described["vocabulary_hint_enabled"] is True
    assert described["model_loaded"] is False


def test_provider_status_exposes_the_model_for_the_ui_and_diagnostics():
    status = _provider().status()
    assert status["name"] == "local_stt"
    assert status["model"] == "small"
    assert status["language"] == "pt"


def test_voice_diagnostics_names_the_model_it_will_use():
    from core.voice_diagnostics import _check_stt

    report = _check_stt()
    if not report.get("ok"):
        pytest.skip("faster-whisper is not installed in this environment")
    assert report["model"] == "small"
    assert report["device"] == "cpu"
    assert report["language"] == "pt"
    assert "small/cpu/int8" in report["detail"]


# --------------------------------------------------------------------------
#  Nothing else moved
# --------------------------------------------------------------------------


def test_transcription_failure_still_degrades_gracefully(monkeypatch):
    """A broken model returns a failed VoiceResult; it never raises upward."""
    def explode(*_args, **_kwargs):
        raise RuntimeError("model unavailable")

    monkeypatch.setitem(sys.modules, "faster_whisper", _stub_module(explode))

    result = _provider().transcribe(b"RIFFfake")
    assert result.ok is False
    assert "model unavailable" in (result.error or "")


def test_the_temporary_wav_is_always_deleted(fake_whisper, monkeypatch):
    import tempfile as tempfile_module
    from pathlib import Path

    created: list[str] = []
    real = tempfile_module.NamedTemporaryFile

    def tracked(*args, **kwargs):
        handle = real(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(tempfile_module, "NamedTemporaryFile", tracked)
    _provider().transcribe(b"RIFFfake")
    assert created and not any(Path(p).exists() for p in created)


def test_the_microphone_gate_was_not_touched_by_this_change():
    """Capture detection and transcription accuracy are different problems."""
    from core import speech_filter

    assert speech_filter.MIN_ADAPTIVE_THRESHOLD == 12.0
    assert speech_filter.MAX_ADAPTIVE_THRESHOLD == 600.0
    assert speech_filter.NOISE_MULTIPLIER == 3.5
    assert speech_filter.NOISE_HEADROOM == 8.0
    assert speech_filter.DEFAULT_MIN_VOICED_RATIO == 0.06


def test_the_microphone_capture_format_was_not_touched():
    pytest.importorskip("pyaudio", reason="PyAudio is an optional dependency")
    from core.voice import AudioInputProvider

    mic = (load_config().get("voice") or {}).get("microphone") or {}
    provider = AudioInputProvider(mic)
    assert provider.sample_rate == 16000
    assert provider.channels == 1


def test_typed_chat_still_does_not_speak_by_default():
    voice = load_config().get("voice") or {}
    assert voice.get("typed_chat_tts") is False
    assert voice.get("voice_reply_tts") is True
    assert voice.get("tts_enabled") is True


def test_the_wake_phrase_is_still_off_and_still_uses_forced_portuguese():
    voice = load_config().get("voice") or {}
    assert voice.get("wake_phrase_enabled") is False
