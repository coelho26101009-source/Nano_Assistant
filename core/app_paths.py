"""Centralized paths for development and installed HELIOS builds."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    configured = os.getenv("HELIOS_APP_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_root() -> Path:
    configured = os.getenv("HELIOS_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return Path(os.getenv("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")) / "HELIOS"
    return Path(os.getenv("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "HELIOS"


ROOT = app_root()
CORE_DIR = ROOT / "core"
PLUGINS_DIR = ROOT / "plugins"
CONFIG_DIR = ROOT / "config"
FRONTEND_DIR = ROOT / "frontend" / "out"
DATA_DIR = data_root()
