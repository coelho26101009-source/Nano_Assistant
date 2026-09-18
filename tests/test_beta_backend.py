"""Beta failure conditions using synthetic stores, never the user's profile."""
from __future__ import annotations

import ast
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from core import atomic_storage, config, data_migration, secret_store, user_settings

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setattr(user_settings, "_PATH", tmp_path / "user_settings.json")
    monkeypatch.setattr(user_settings, "_cache", None)
    return user_settings


def test_implicit_custom_profile_never_scans_real_legacy_data(tmp_path, monkeypatch):
    from core import app_paths
    monkeypatch.setattr(app_paths, "DATA_DIR", tmp_path / "isolated")
    monkeypatch.setattr(data_migration, "legacy_candidates", lambda *_: pytest.fail("legacy profile scanned"))
    result = data_migration.migrate_user_data(force=True)
    assert result["status"] == "isolated_profile"
    assert result["copied"] == []
    assert not (tmp_path / "isolated").exists()


def test_canonical_electron_profile_retains_legacy_rescue(tmp_path, monkeypatch):
    from core import app_paths
    destination = tmp_path / "canonical"
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    (legacy / "user_settings.json").write_text('{"theme":"light"}', encoding="utf-8")
    monkeypatch.setattr(app_paths, "DATA_DIR", destination)
    monkeypatch.setattr(app_paths, "default_data_root", lambda: destination)
    monkeypatch.setattr(data_migration, "legacy_candidates", lambda *_: [legacy])
    assert data_migration.migrate_user_data()["status"] == "migrated"


@pytest.mark.parametrize("payload", ["- item", "42", "settings: [", "voice: null\nlocal: false\nmemory: []"])
def test_invalid_yaml_recovers_to_usable_sections(payload, tmp_path, monkeypatch, settings):
    path = tmp_path / "settings.yaml"
    path.write_text(payload, encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_cache", None)
    loaded = config.load_config(reload=True)
    for section in ("voice", "local", "memory"):
        assert isinstance(loaded[section], dict)
    assert isinstance(loaded["voice"]["enabled"], bool)


def test_invalid_yaml_does_not_log_source_line(tmp_path, monkeypatch, settings, caplog):
    path = tmp_path / "settings.yaml"
    value = "synthetic-sensitive-source-line"
    path.write_text(f"api_key: [{value}\n", encoding="utf-8")
    monkeypatch.setattr(config, "CONFIG_PATH", path)
    monkeypatch.setattr(config, "_cache", None)
    config.load_config(reload=True)
    assert value not in caplog.text


def test_valid_yaml_booleans_override_defaults_and_missing_config_keeps_wake_off():
    merged = config._deep_merge({"enabled": True, "nested": {"enabled": False}},
                                {"enabled": False, "nested": {"enabled": True}})
    assert merged == {"enabled": False, "nested": {"enabled": True}}
    assert config.DEFAULT_CONFIG["voice"]["wake_phrase_enabled"] is False


@pytest.mark.parametrize("key,value", [
    ("voice_enabled", "false"), ("onboarding_completed", 1),
    ("wake_phrase_cooldown_seconds", float("nan")),
    ("wake_command_timeout_seconds", "bad"), ("input_device_index", {}),
    ("preferred_cloud", []), ("provider_mode", "UNKNOWN"), ("groq_fast_model", {}),
])
def test_invalid_settings_are_rejected_before_persistence(settings, key, value):
    assert settings.set_value(key, value)["error"] == "invalid_value"
    assert settings.all_settings() == {}
    assert not settings._PATH.exists()


def test_persisted_settings_validate_without_rewriting_bad_file(settings, monkeypatch):
    payload = {"onboarding_completed": True, "theme": "light", "voice_enabled": "false", "api_key": "never_return"}
    original = json.dumps(payload)
    settings._PATH.write_text(original, encoding="utf-8")
    assert settings.all_settings() == {"onboarding_completed": True, "theme": "light"}
    assert settings._PATH.read_text(encoding="utf-8") == original


def test_onboarding_getter_is_local_and_survives_reload(settings, monkeypatch):
    # Execute only the endpoint: importing main intentionally opens the whole
    # backend, while this assertion must prove no backend/network call is used.
    tree = ast.parse((REPO / "core" / "main.py").read_text(encoding="utf-8-sig"))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "get_onboarding_status")
    node.decorator_list = []
    namespace = {"user_settings": settings}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "onboarding_endpoint", "exec"), namespace)
    assert namespace["get_onboarding_status"]() == {"completed": False}
    assert settings.set_value("onboarding_completed", True)["ok"]
    monkeypatch.setattr(settings, "_cache", None)
    assert namespace["get_onboarding_status"]() == {"completed": True}


def test_atomic_replace_failure_retains_previous_settings(settings, monkeypatch):
    assert settings.set_value("theme", "light")["ok"]
    old = settings._PATH.read_bytes()
    def fail(*_):
        raise OSError("simulated replacement failure")
    monkeypatch.setattr(atomic_storage.os, "replace", fail)
    assert settings.set_value("theme", "dark")["error"] == "write_failed"
    assert settings.reset("theme")["error"] == "write_failed"
    assert settings.get("theme") == "light"
    assert settings._PATH.read_bytes() == old
    assert sorted(p.name for p in settings._PATH.parent.iterdir()) == ["user_settings.json"]


def test_windows_dpapi_failure_cannot_write_plaintext(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    path.write_bytes(b"previous-encrypted-store")
    monkeypatch.setattr(secret_store, "_STORE_PATH", path)
    monkeypatch.setattr(secret_store, "_dpapi_available", lambda: True)
    monkeypatch.setattr(secret_store, "_dpapi", lambda *_: None)
    assert secret_store.set_secret("groq_api_key", "synthetic_new_secret_1234") is False
    assert path.read_bytes() == b"previous-encrypted-store"


def test_log_formatters_redact_message_arguments_and_traceback():
    from core.log_safety import register_secret
    from core.logger import ColorFormatter, SafeFormatter
    opaque = "opaque-mistral-test-credential"
    register_secret(opaque)
    try:
        raise RuntimeError(f"Authorization: Bearer {opaque}; api_key=synthetic_query_secret")
    except RuntimeError:
        record = logging.LogRecord("nano.test", logging.ERROR, "synthetic", 1,
                                   "failed with %s and gsk_synthetic_secret_12345", (opaque,), sys.exc_info())
    for formatter in (SafeFormatter("%(levelname)s %(message)s"), ColorFormatter()):
        output = formatter.format(record)
        assert "[REDACTED]" in output
        for value in (opaque, "synthetic_query_secret", "gsk_synthetic_secret_12345"):
            assert value not in output
    assert record.levelname == "ERROR"


def test_windows_legacy_plaintext_is_not_reported_as_encrypted_credentials(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    original = b'{"groq_api_key":"synthetic_plaintext_key"}'
    path.write_bytes(original)
    monkeypatch.setattr(secret_store, "_STORE_PATH", path)
    monkeypatch.setattr(secret_store, "_dpapi_available", lambda: True)
    monkeypatch.setattr(secret_store, "_dpapi", lambda *_: None)
    assert secret_store._read_store() == {}
    assert path.read_bytes() == original


def test_provider_network_errors_never_echo_untrusted_exception(monkeypatch):
    from core import providers
    def fail(*_, **__):
        raise httpx.ConnectError("https://server/?api_key=synthetic_sensitive_value")
    monkeypatch.setattr(providers.httpx, "get", fail)
    models, error = providers.list_groq_models("synthetic-key")
    assert models == []
    assert error == "network_error: ConnectError"


@pytest.mark.parametrize("raw", [b"\xffbroken", b'{"filesystem.write":{"risk":"broken"}}', b'{"filesystem.write":{"risk":[]}}'])
def test_corrupt_permission_store_keeps_safe_builtin_policy(tmp_path, raw):
    from core.permission_manager import PermissionManager
    path = tmp_path / "permission_policies.json"
    path.write_bytes(raw)
    manager = PermissionManager(policy_store_path=path)
    assert manager.is_approval_gated("filesystem.write")


def test_new_user_state_survives_real_process_restart(tmp_path):
    # Two real Python processes, real SQLite migrations and durable stores. No
    # HTTP, microphone, GUI, or OS-changing tool is invoked.
    env = dict(os.environ)
    env.update(NANO_DATA_DIR=str(tmp_path / "profile"), NANO_SKIP_DOTENV="1", PYTHONIOENCODING="utf-8")
    for key in secret_store._ENV_FALLBACK.values():
        for name in key:
            env.pop(name, None)
    script = r'''
import json, sys
from core import data_migration, memory, user_settings
from core.memory_stack import MemoryStack
from core.task_engine import TaskEngine
from core.permission_manager import PermissionManager
assert data_migration.migrate_user_data()["status"] == "isolated_profile"
db = memory.MemoryEngine()
stack = MemoryStack(db, background=False)
assert stack.ready
tasks = TaskEngine()
permissions = PermissionManager()
if sys.argv[1] == "write":
    assert db.count_messages() == 0
    assert stack.memories.stats()["total"] == 0
    stack.record_user_message("Synthetic beta conversation")
    stack.record_assistant_message("Synthetic beta answer")
    db.set_fact("beta_marker", "durable")
    assert stack.remember("O meu projeto chama-se Teste Beta", origin="explicit")["ok"]
    tasks.create_task("Synthetic durable task")
    permissions.register_policy("filesystem.read", decision="BLOCKED", risk="medium")
    assert user_settings.set_value("onboarding_completed", True)["ok"]
else:
    assert db.get_fact("beta_marker") == "durable"
    assert stack.ensure_active()
    assert len(stack.recent_messages()) == 2
    assert stack.memories.stats()["total"] >= 1
    assert tasks.list_tasks()[0]["title"] == "Synthetic durable task"
    assert permissions.get_policy("filesystem.read")["decision"].lower() == "blocked"
    assert user_settings.get("onboarding_completed") is True
stack.stop()
db.close()
print("synthetic state verified")
'''
    for phase in ("write", "read"):
        result = subprocess.run([sys.executable, "-c", script, phase], cwd=REPO, env=env,
                                capture_output=True, text=True, timeout=45)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "synthetic state verified" in result.stdout


def test_detached_ollama_does_not_lock_nano_install_directory(tmp_path, monkeypatch):
    from core import ollama_service
    executable = tmp_path / "Ollama" / "ollama.exe"
    monkeypatch.setattr(ollama_service, "find_executable", lambda: str(executable))
    probes = iter([False, True])
    monkeypatch.setattr(ollama_service, "api_available", lambda *a, **k: next(probes))
    calls = []
    monkeypatch.setattr(ollama_service.subprocess, "Popen", lambda *a, **k: calls.append((a, k)))
    result = ollama_service.ensure_running()
    assert result["started"] is True
    assert calls[0][1]["cwd"] == str(executable.parent.resolve())
    assert calls[0][0][0] == [str(executable), "serve"]
