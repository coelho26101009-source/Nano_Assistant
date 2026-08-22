"""Central HELIOS configuration loader with safe defaults and caching."""
from __future__ import annotations

import copy
import logging
from typing import Any

import yaml

from core.app_paths import CONFIG_DIR

logger = logging.getLogger("helios.config")
CONFIG_PATH = CONFIG_DIR / "settings.yaml"
_cache: dict | None = None

DEFAULT_CONFIG: dict[str, Any] = {
    # Two Groq tiers. Conversation must never pay for the big model, and the
    # big model is reached only by an explicit COMPLEX classification (see
    # core.model_selection) -- never because a message happens to be long.
    # These ids were chosen from a measured benchmark on the real account:
    # llama-3.1-8b-instant / llama-3.3-70b-versatile are NOT available there.
    "groq_model": "openai/gpt-oss-20b",          # legacy alias of the fast model
    "groq_fast_model": "openai/gpt-oss-20b",
    "groq_complex_model": "openai/gpt-oss-120b",
    "groq_api_key": "",
    "ollama_enabled": True,
    "local": {
        "enabled": True,
        "model": "auto",
        "url": "http://127.0.0.1:11434",
        "max_context": 4096,
        # Nano starts the Ollama SERVER if it is not already running. It never
        # preloads or pulls a model: the server idles at tens of MB, a model
        # costs gigabytes, so loading waits for a real request.
        "autostart": True,
        # How long Ollama keeps a model resident after its last use. Matters on
        # 16 GB: without it an idle 8B model holds RAM indefinitely.
        "keep_alive": "5m",
    },
    "model_router": {
        "enabled": True,
        "default_provider": "ollama",
        "local_first": True,
        "default_privacy": "normal",
        "routing": {
            "capability_weight": 4.0,
            "quality_weight": 2.5,
            "speed_weight": 2.0,
            "privacy_weight": 4.0,
            "context_weight": 1.5,
            "resource_weight": 1.5,
        },
        "hardware": {
            "gpu_vram_gb": 6.0,
            "ram_gb": 16.0,
            "preferred_quantization": "Q4",
        },
    },
    "voice": {
        "enabled": False,
        "local_first": True,
        "cloud_audio": False,
        "listen_seconds": 5,
        "session_timeout_seconds": 30,
        # Spoken output is split in three so typing never talks back at you.
        # A single global tts_enabled meant every typed reply was read aloud,
        # which is how a delayed "Olá" arrived a minute after a chat message.
        "tts_enabled": True,        # master switch
        "typed_chat_tts": False,    # replies to messages the user TYPED
        "voice_reply_tts": True,    # replies to messages the user SPOKE
        "wake_word": {
            "enabled": False,
            "provider": "openwakeword",
            "phrase": "Nano",
            "model_path": "",
            "threshold": 0.7,
            "cooldown": 2.0,
            "cooldown_seconds": 2.0,
            "backend": "auto",
            "framework": "onnx",
            "keyword_path": "",
            "custom_verifier_model_path": "",
        },
        # Simple local wake-phrase detector ("Ei Nano"): local STT phrase
        # spotting, no trained model required. Independent of "wake_word" above.
        #
        # Portuguese on purpose. faster-whisper-tiny forced to Portuguese does
        # not reliably produce the English "Hey": real recordings of "Hey Nano"
        # came back as "Ei, nano!", "Ei, não.", "E ai, no." and "NÃO!", so the
        # matcher never fired. "Ei" is a native interjection the model knows.
        "wake_phrase": "ei nano",
        "wake_phrase_enabled": True,
        # Bare "nano" is OFF by default. It caused real false activations: a
        # single hallucinated or overheard "nano" was enough to wake Nano.
        # Requiring the full "hey nano" is dramatically more selective.
        "wake_phrase_allow_nano_only": False,
        "wake_phrase_cooldown_seconds": 3.0,
        "wake_phrase_chunk_seconds": 2.5,
        # How long to wait for a command after the wake chime before cancelling
        # and going back to listening. Silence is never sent to the Brain.
        "wake_command_timeout_seconds": 7,
        "microphone": {
            "enabled": True,
            "sample_rate": 16000,
            "channels": 1,
            "device_index": None,
        },
        "stt": {
            "provider": "local",
            "model": "tiny",
            "device": "cpu",
            "compute_type": "int8",
            "language": "pt-PT",
        },
        "tts": {
            "provider": "local",
            "voice": "pt-PT-DuarteNeural",
            "speed": "+0%",
            "volume": "+0%",
        },
    },
    "memory": {
        "history_messages": 20,
        "max_history_chars": 8000,
        # The conversation budget that actually binds, in TOKENS -- the unit the
        # Groq tokens-per-minute ceiling is denominated in. See
        # Brain._trim_conversation: a character budget let one message reserve
        # most of a minute's allowance, which is how rate limiting kept coming
        # back after tool scoping had already fixed the tool half of the cost.
        "max_history_tokens": 1200,
        "facts_enabled": True,
        "rag_enabled": True,
        "rag_results": 3,
        "rag_max_chars": 1500,
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _normalize_voice_config(cfg: dict) -> dict:
    voice_cfg = cfg.setdefault("voice", {})
    if not isinstance(voice_cfg, dict):
        return cfg
    wake_cfg = voice_cfg.setdefault("wake_word", {})
    if not isinstance(wake_cfg, dict):
        wake_cfg = {}
        voice_cfg["wake_word"] = wake_cfg

    def apply_legacy(container: dict, target: dict) -> None:
        legacy_keys = {
            "wake_word_enabled": ("enabled",),
            "wake_word_phrase": ("phrase",),
            "wake_word_sensitivity": ("threshold",),
            "wake_word_backend": ("backend",),
            "wake_word_framework": ("framework",),
            "wake_word_cooldown_seconds": ("cooldown",),
            "wake_word_keyword_path": ("keyword_path",),
            "wake_word_provider": ("provider",),
            "wake_word_model_path": ("model_path",),
        }
        for old_key, (new_key,) in legacy_keys.items():
            if old_key in container and new_key not in target:
                target[new_key] = container[old_key]

    apply_legacy(cfg, wake_cfg)
    apply_legacy(voice_cfg, wake_cfg)

    if bool(voice_cfg.get("wake_word_enabled")) and not voice_cfg.get("enabled"):
        voice_cfg["enabled"] = True
    if bool(voice_cfg.get("wake_word_enabled")) and not wake_cfg.get("enabled"):
        wake_cfg["enabled"] = True
    if "enabled" in cfg and not voice_cfg.get("enabled"):
        voice_cfg["enabled"] = bool(cfg["enabled"])
    if "voice_enabled" in cfg and not voice_cfg.get("enabled"):
        voice_cfg["enabled"] = bool(cfg["voice_enabled"])
    if "wake_word_enabled" in cfg and not wake_cfg.get("enabled"):
        wake_cfg["enabled"] = bool(cfg["wake_word_enabled"])
    if bool(wake_cfg.get("enabled")) and not voice_cfg.get("enabled"):
        voice_cfg["enabled"] = True
    if "wake_word_phrase" in cfg and "phrase" not in wake_cfg:
        wake_cfg["phrase"] = cfg["wake_word_phrase"]
    if "wake_word_sensitivity" in cfg and "threshold" not in wake_cfg:
        wake_cfg["threshold"] = cfg["wake_word_sensitivity"]
    if "wake_word_cooldown_seconds" in cfg and "cooldown" not in wake_cfg:
        wake_cfg["cooldown"] = cfg["wake_word_cooldown_seconds"]
    if "wake_word_backend" in cfg and "backend" not in wake_cfg:
        wake_cfg["backend"] = cfg["wake_word_backend"]
    if "wake_word_framework" in cfg and "framework" not in wake_cfg:
        wake_cfg["framework"] = cfg["wake_word_framework"]
    if "wake_word_keyword_path" in cfg and "keyword_path" not in wake_cfg:
        wake_cfg["keyword_path"] = cfg["wake_word_keyword_path"]
    if "wake_word_provider" in cfg and "provider" not in wake_cfg:
        wake_cfg["provider"] = cfg["wake_word_provider"]
    if "wake_word_model_path" in cfg and "model_path" not in wake_cfg:
        wake_cfg["model_path"] = cfg["wake_word_model_path"]
    if not wake_cfg.get("provider"):
        wake_cfg["provider"] = "openwakeword"
    if not wake_cfg.get("model_path") and wake_cfg.get("keyword_path"):
        wake_cfg["model_path"] = wake_cfg["keyword_path"]
    if not wake_cfg.get("cooldown_seconds"):
        wake_cfg["cooldown_seconds"] = wake_cfg.get("cooldown", 2.0)
    return cfg


def load_config(reload: bool = False) -> dict:
    global _cache
    if _cache is not None and not reload:
        return _cache
    loaded: dict = {}
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
            if not isinstance(loaded, dict):
                raise ValueError("settings.yaml tem de conter um objeto YAML")
    except FileNotFoundError:
        logger.warning("settings.yaml não encontrado em %s; a usar defaults", CONFIG_PATH)
    except Exception as exc:
        logger.error("settings.yaml inválido: %s; a usar defaults", exc)
    _cache = _deep_merge(DEFAULT_CONFIG, loaded)
    _normalize_voice_config(_cache)
    # Choices the user made in the Settings UI live outside the repository and
    # win over the shipped YAML, so a git pull never overwrites them and their
    # preferences never show up as uncommitted changes.
    try:
        from core import user_settings

        user_settings.apply_overlay(_cache)
    except Exception:  # pragma: no cover - config must load even if the overlay fails
        logger.exception("Could not apply user settings overlay; using file config.")
    return _cache


def get_setting(path: str, default: Any = None) -> Any:
    node: Any = load_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node
