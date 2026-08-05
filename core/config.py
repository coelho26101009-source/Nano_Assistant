"""
H.E.L.I.O.S. Config Loader
Leitura central do config/settings.yaml com cache, para que qualquer módulo
(core ou plugin) leia a mesma configuração sem a recarregar do disco.
"""

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("helios.config")

CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"

_cache: dict | None = None


def load_config(reload: bool = False) -> dict:
    """Devolve o settings.yaml completo (vazio se o ficheiro faltar ou for inválido)."""
    global _cache
    if _cache is not None and not reload:
        return _cache
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            _cache = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        logger.warning(f"settings.yaml não encontrado em {CONFIG_PATH}. A usar defaults.")
        _cache = {}
    except Exception as exc:
        logger.error(f"settings.yaml inválido: {exc}. A usar defaults.")
        _cache = {}
    return _cache


def get_setting(path: str, default: Any = None) -> Any:
    """Lê uma opção aninhada por caminho pontuado, ex: get_setting('voice.stt_provider')."""
    node: Any = load_config()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return default if node is None else node
