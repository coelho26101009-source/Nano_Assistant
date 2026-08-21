"""Provider routing, mode selection, and secret handling.

The rule these tests defend: an API key may live in an OS-protected store and
nowhere else. Not in settings.yaml, not in Git, not in the frontend bundle, not
in a log line, and never on its way back to the UI. The UI gets a mask and a
boolean, which is enough to render "configured" and nothing else.

They also pin the AUTO / CLOUD / LOCAL contract, because a mode that silently
degrades is a lie about which model answered.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from core import providers, secret_store
from core.providers import ProviderMode, ProviderState

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
NAME = providers.GROQ_SECRET_NAME
BULLET = "•"


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    """Point the store at a temp file so tests never touch the real key."""
    monkeypatch.setattr(secret_store, "_STORE_PATH", tmp_path / "secrets.dat")
    for name in ("NANO_API_KEY", "HELIOS_API_KEY", "GROQ_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "secrets.dat"


def _frontend_files() -> list[Path]:
    return (
        list((FRONTEND / "components").glob("*.tsx"))
        + list((FRONTEND / "pages").glob("*.tsx"))
        + list((FRONTEND / "lib").glob("*.ts"))
    )


# ============================================================ secret masking

def test_mask_never_reveals_enough_to_reconstruct_the_key():
    key = "gsk_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    masked = secret_store.mask(key)
    assert key not in masked
    assert masked.count(BULLET) >= 8
    # A mask that leaked the middle would be worse than no mask at all.
    assert "MNOPQRST" not in masked
    assert len(masked) < len(key)


def test_short_secrets_are_masked_entirely():
    """A short key has no safe prefix to show."""
    assert secret_store.mask("abc12345") == BULLET * 8
    assert "abc" not in secret_store.mask("abc12345")


def test_mask_of_empty_is_empty():
    assert secret_store.mask("") == ""


# =========================================================== secret storage

def test_a_stored_secret_round_trips(isolated_store):
    assert secret_store.set_secret(NAME, "gsk_round_trip_value_1234") is True
    assert secret_store.get_secret(NAME) == "gsk_round_trip_value_1234"


def test_the_raw_secret_is_never_readable_on_disk(isolated_store):
    """On Windows this is DPAPI; the plaintext must not appear in the file."""
    secret = "gsk_plaintext_probe_value_9876"
    secret_store.set_secret(NAME, secret)

    raw = isolated_store.read_bytes()
    assert secret.encode("utf-8") not in raw, "the API key is sitting in plaintext on disk"
    if secret_store.is_encrypted():
        # Not merely obfuscated: it must not parse back as the JSON we wrote.
        with pytest.raises(Exception):
            json.loads(raw.decode("utf-8"))


def test_describe_reports_metadata_and_never_the_secret(isolated_store):
    secret = "gsk_describe_probe_value_5555"
    secret_store.set_secret(NAME, secret)

    described = secret_store.describe(NAME)
    assert described["configured"] is True
    assert described["source"] == "encrypted_store"
    assert secret not in json.dumps(described), "describe() leaked the secret"
    assert described["masked"] and described["masked"] != secret


def test_describe_of_an_absent_secret_is_honest(isolated_store):
    described = secret_store.describe(NAME)
    assert described["configured"] is False
    assert described["masked"] == ""
    assert described["source"] == "none"


def test_deleting_a_secret_removes_it(isolated_store):
    secret_store.set_secret(NAME, "gsk_delete_me_0000")
    assert secret_store.delete_secret(NAME) is True
    assert secret_store.get_secret(NAME) == ""
    assert secret_store.has_secret(NAME) is False


def test_setting_an_empty_value_deletes_rather_than_storing_blank(isolated_store):
    secret_store.set_secret(NAME, "gsk_something_real_1111")
    secret_store.set_secret(NAME, "   ")
    assert secret_store.get_secret(NAME) == ""


def test_environment_is_a_read_only_fallback(isolated_store, monkeypatch):
    """An existing .env keeps working, but writes never go back to it."""
    monkeypatch.setenv("GROQ_API_KEY", "gsk_from_environment_2222")
    assert secret_store.get_secret(NAME) == "gsk_from_environment_2222"
    assert secret_store.describe(NAME)["source"] == "environment"

    # The store wins once a key is set through the UI.
    secret_store.set_secret(NAME, "gsk_from_the_store_3333")
    assert secret_store.get_secret(NAME) == "gsk_from_the_store_3333"


# ====================================================== secrets never escape

def test_no_secret_is_committed_to_the_repository():
    """The store lives in the data directory, which is outside the repo."""
    from core.app_paths import DATA_DIR

    text = (REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8")
    assert "gsk_" not in text, "settings.yaml carries a Groq key"
    for line in text.splitlines():
        if "api_key" in line and ":" in line:
            value = line.split(":", 1)[1].strip().strip("\"'")
            assert not value, "settings.yaml carries an inline secret: " + repr(line)

    assert DATA_DIR.resolve() != REPO_ROOT.resolve(), "secrets would live inside the repo"


def _strip_comments(source: str) -> str:
    """Drop // and /* */ comments.

    Prose explaining that a key is never cached in the browser would otherwise
    read as the very call it warns against.
    """
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", source, flags=re.M)


def test_the_frontend_never_names_a_secret_storage_mechanism():
    """The key must not be cached anywhere the browser can read it back."""
    banned = ("localStorage", "sessionStorage", "document.cookie", "indexedDB")
    for path in _frontend_files():
        code = _strip_comments(path.read_text(encoding="utf-8"))
        for token in banned:
            assert token not in code, path.name + " uses " + token + ", which can hold a secret"


def test_the_frontend_never_echoes_a_key_back_to_the_user():
    """SecretField is write-only: there is no reveal affordance."""
    ui = (FRONTEND / "components" / "ui.tsx").read_text(encoding="utf-8")
    assert "SecretField" in ui

    settings = (FRONTEND / "components" / "SettingsPage.tsx").read_text(encoding="utf-8")
    for token in ("showKey", "revealKey", "showSecret", "unmask"):
        assert token not in settings, "SettingsPage offers " + token + ", revealing a stored key"


def test_backend_endpoints_never_return_the_key(isolated_store):
    """get_settings and get_providers are the two payloads the UI polls."""
    import core.main as main

    secret = "gsk_endpoint_leak_probe_4444"
    secret_store.set_secret(NAME, secret)
    for payload in (main.get_settings(), main.get_providers()):
        assert secret not in json.dumps(payload, default=str), "an endpoint returned the raw key"


# ======================================================== provider state map

def _provider(state: ProviderState, model: str = "m", detail: str = "d") -> dict:
    return {"state": state.value, "model": model, "detail": detail}


def test_missing_key_maps_to_setup_required_not_error(isolated_store):
    """No key yet is a setup step, not a failure."""
    described = providers.describe_groq("openai/gpt-oss-20b")
    assert described["state"] == ProviderState.SETUP_REQUIRED.value
    assert described["secret"]["configured"] is False
    assert described["detail"], "SETUP_REQUIRED must explain what to do"


def test_a_described_provider_always_carries_a_detail_the_ui_can_show():
    """Every non-ready state has to explain itself; a bare badge is useless."""
    described = providers.describe_ollama("nope:0b", "http://127.0.0.1:1", local_enabled=True)
    assert described["state"] in {state.value for state in ProviderState}
    if described["state"] != ProviderState.READY.value:
        assert described["detail"], "a non-ready provider must say why"


def test_disabled_local_provider_reports_disabled_not_broken():
    described = providers.describe_ollama("qwen3:8b", "http://127.0.0.1:11434", local_enabled=False)
    assert described["state"] == ProviderState.DISABLED.value


def test_ollama_state_mapping_covers_every_service_state():
    """A new OllamaState must not fall through to a generic UNAVAILABLE."""
    from core import ollama_service

    mapped = {
        ollama_service.OllamaState.READY,
        ollama_service.OllamaState.MODEL_UNAVAILABLE,
        ollama_service.OllamaState.OLLAMA_UNAVAILABLE,
        ollama_service.OllamaState.NOT_INSTALLED,
        ollama_service.OllamaState.DISABLED,
    }
    declared = {
        value for name, value in vars(ollama_service.OllamaState).items()
        if not name.startswith("_") and isinstance(value, str)
    }
    unmapped = declared - mapped
    assert not unmapped, "describe_ollama has no mapping for " + repr(unmapped)


# ============================================================ routing modes

def test_auto_prefers_groq_when_it_is_ready():
    route = providers.resolve_route(
        ProviderMode.AUTO,
        _provider(ProviderState.READY, "gpt-oss-20b"),
        _provider(ProviderState.READY, "qwen3:8b"),
    )
    assert route["provider"] == "groq"
    assert route["usable"] is True
    assert route["fallback"] is False


def test_auto_falls_back_to_ollama_and_says_so():
    route = providers.resolve_route(
        ProviderMode.AUTO,
        _provider(ProviderState.UNAVAILABLE),
        _provider(ProviderState.READY, "qwen3:8b"),
    )
    assert route["provider"] == "ollama"
    assert route["usable"] is True
    assert route["fallback"] is True, "a silent fallback hides which model answered"
    assert route["reason"]


def test_auto_with_nothing_available_is_unusable_not_optimistic():
    route = providers.resolve_route(
        ProviderMode.AUTO,
        _provider(ProviderState.SETUP_REQUIRED),
        _provider(ProviderState.NOT_INSTALLED),
    )
    assert route["usable"] is False
    assert route["provider"] == "none"
    assert route["reason"]


def test_cloud_mode_never_silently_downgrades_to_local():
    """CLOUD means CLOUD. Falling back would answer from a different model."""
    route = providers.resolve_route(
        ProviderMode.CLOUD,
        _provider(ProviderState.UNAVAILABLE),
        _provider(ProviderState.READY, "qwen3:8b"),
    )
    assert route["provider"] == "groq"
    assert route["usable"] is False
    assert route["fallback"] is False


def test_local_mode_never_reaches_for_the_cloud():
    """LOCAL is also a privacy choice: it must not send anything to Groq."""
    route = providers.resolve_route(
        ProviderMode.LOCAL,
        _provider(ProviderState.READY, "gpt-oss-20b"),
        _provider(ProviderState.UNAVAILABLE),
    )
    assert route["provider"] == "ollama"
    assert route["usable"] is False
    assert route["fallback"] is False


def test_every_mode_is_parseable_from_the_ui_value():
    for value in ("AUTO", "CLOUD", "LOCAL", "auto", "cloud", "local"):
        assert ProviderMode.parse(value).value == value.upper()


def test_an_unknown_mode_falls_back_to_auto_rather_than_crashing():
    assert ProviderMode.parse("nonsense") == ProviderMode.AUTO
    assert ProviderMode.parse("") == ProviderMode.AUTO


def test_a_route_always_carries_the_fields_the_inspector_renders():
    route = providers.resolve_route(
        ProviderMode.AUTO, _provider(ProviderState.READY), _provider(ProviderState.READY),
    )
    for field in ("provider", "model", "usable", "fallback", "mode", "reason"):
        assert field in route, "route is missing " + field


def test_the_brain_honours_the_configured_mode():
    """The mode is not decoration: chat() has to read it."""
    source = (REPO_ROOT / "core" / "brain.py").read_text(encoding="utf-8")
    assert "provider_mode" in source, "brain.py ignores the provider mode"


def test_groq_model_discovery_is_never_a_hardcoded_literal():
    """A pinned model id is exactly how the last 404 happened."""
    tree = ast.parse((REPO_ROOT / "core" / "providers.py").read_text(encoding="utf-8"))
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    pinned = [text for text in literals if "llama-3.3-70b" in text or "llama3-70b" in text]
    assert not pinned, "providers.py pins a decommissioned model: " + repr(pinned)
