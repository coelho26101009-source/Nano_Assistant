"""User-changeable settings, stored outside the repository.

`config/settings.yaml` is project source: it ships defaults and belongs in Git.
Anything the user changes through the Settings UI is written here instead, to
`user_settings.json` in the app data directory, and overlaid on top of the
YAML at load time.

That separation matters for two reasons: a `git pull` never clobbers the user's
choices, and the user's choices never appear as uncommitted changes in the
repository. Secrets are not stored here — those live in core.secret_store.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Any

from core.app_paths import DATA_DIR

logger = logging.getLogger("nano.user_settings")

_PATH = DATA_DIR / "user_settings.json"
_LOCK = threading.RLock()
_cache: dict[str, Any] | None = None

# Only these keys may be written from the UI. An allow-list keeps a bug (or a
# malicious page reaching the local bridge) from rewriting arbitrary config.
ALLOWED_KEYS: frozenset[str] = frozenset({
    "provider_mode",           # AUTO | CLOUD | LOCAL
    "groq_model",
    "local_model",
    "wake_phrase_enabled",
    "wake_phrase_allow_nano_only",
    "wake_phrase_cooldown_seconds",
    "wake_command_timeout_seconds",
    "voice_enabled",
    "tts_enabled",
    "input_device_index",
    "output_device_index",
    "theme",
    "reduce_motion",
})


def _load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    data: dict[str, Any] = {}
    if _PATH.exists():
        try:
            parsed = json.loads(_PATH.read_text(encoding="utf-8"))
            if isinstance(parsed, dict):
                data = parsed
        except Exception as exc:
            logger.warning("user_settings.json unreadable (%s); using defaults.", exc)
    _cache = data
    return _cache


def _save(data: dict[str, Any]) -> bool:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        _PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except OSError as exc:
        logger.error("Could not write user settings: %s", exc)
        return False


def get(key: str, default: Any = None) -> Any:
    with _LOCK:
        return _load().get(key, default)


def set_value(key: str, value: Any) -> dict[str, Any]:
    """Persist one setting. Rejects keys outside the allow-list."""
    if key not in ALLOWED_KEYS:
        return {"ok": False, "error": "unknown_setting", "key": key}
    with _LOCK:
        data = dict(_load())
        data[key] = value
        if not _save(data):
            return {"ok": False, "error": "write_failed", "key": key}
        globals()["_cache"] = data
        return {"ok": True, "key": key, "value": value}


def all_settings() -> dict[str, Any]:
    with _LOCK:
        return dict(_load())


def reset(key: str) -> dict[str, Any]:
    with _LOCK:
        data = dict(_load())
        data.pop(key, None)
        _save(data)
        globals()["_cache"] = data
        return {"ok": True, "key": key}


def apply_overlay(config: dict[str, Any]) -> dict[str, Any]:
    """Overlay stored user choices onto the loaded YAML config, in place.

    Called once at startup so the rest of the app reads a single merged config
    and never has to know a setting came from the user rather than the file.
    """
    stored = all_settings()
    if not stored:
        return config

    voice = config.setdefault("voice", {})
    local = config.setdefault("local", {})

    if "groq_model" in stored:
        config["groq_model"] = stored["groq_model"]
    if "local_model" in stored:
        local["model"] = stored["local_model"]
    if "provider_mode" in stored:
        config["provider_mode"] = stored["provider_mode"]

    for key in (
        "wake_phrase_enabled",
        "wake_phrase_allow_nano_only",
        "wake_phrase_cooldown_seconds",
        "wake_command_timeout_seconds",
        "tts_enabled",
    ):
        if key in stored:
            voice[key] = stored[key]
    if "voice_enabled" in stored:
        voice["enabled"] = stored["voice_enabled"]
    if "input_device_index" in stored:
        voice.setdefault("microphone", {})["device_index"] = stored["input_device_index"]

    return config


__all__ = ["ALLOWED_KEYS", "all_settings", "apply_overlay", "get", "reset", "set_value"]
