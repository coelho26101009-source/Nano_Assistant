"""Centralized paths so development and frozen Windows builds use the same layout."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_root() -> Path:
    configured = os.getenv("HELIOS_APP_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


ROOT = app_root()
CORE_DIR = ROOT / "core"
PLUGINS_DIR = ROOT / "plugins"
CONFIG_DIR = ROOT / "config"
FRONTEND_DIR = ROOT / "frontend" / "out"
DATA_DIR = Path(os.getenv("HELIOS_DATA_DIR", str(Path.home() / "AppData" / "Local" / "HELIOS")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
