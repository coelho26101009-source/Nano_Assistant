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
    "groq_model": "llama-3.3-70b-versatile",
    "ollama_enabled": True,
    "local": {
        "enabled": True,
        "model": "auto",
        "url": "http://127.0.0.1:11434",
        "max_context": 4096,
    },
    "memory": {
        "history_messages": 20,
        "max_history_chars": 8000,
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
    return _cache


def get_setting(path: str, default: Any = None) -> Any:
    node: Any = load_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node
