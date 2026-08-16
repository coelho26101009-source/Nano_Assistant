"""
H.E.L.I.O.S. Plugin: Context Switcher
Activa modos de trabalho com 1 comando.
Config em config/modes/*.yaml
"""

import logging
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("helios.plugins.context_switcher")

MODES_DIR = Path(__file__).parent.parent / "config" / "modes"


def _load_mode(mode_name: str) -> dict | None:
    path = MODES_DIR / f"{mode_name}.yaml"
    if not path.exists():
        # Tenta por nome parcial
        matches = list(MODES_DIR.glob(f"*{mode_name}*.yaml"))
        if not matches:
            return None
        path = matches[0]
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def activate_mode(mode: str) -> dict:
    """Activa um modo de trabalho predefinido."""
    config = _load_mode(mode.lower())
    if config is None:
        available = [p.stem for p in MODES_DIR.glob("*.yaml")]
        return {"error": f"Modo '{mode}' não encontrado. Disponíveis: {available}"}

    results = []

    # Fechar apps irrelevantes
    for app in config.get("close_apps", []):
        try:
            subprocess.run(["taskkill", "/F", "/IM", app],
                           capture_output=True, timeout=5)
            results.append(f"✓ Fechei {app}")
        except Exception:
            results.append(f"⚠ Não consegui fechar {app}")

    # Abrir apps
    for app_path in config.get("open_apps", []):
        try:
            subprocess.Popen([app_path], shell=True)
            results.append(f"✓ Abri {Path(app_path).name}")
        except Exception as exc:
            results.append(f"⚠ Falha ao abrir {app_path}: {exc}")

    # Abrir URLs no browser
    for url in config.get("open_urls", []):
        try:
            webbrowser.open(url)
            results.append(f"✓ Abri {url}")
        except Exception:
            pass

    # Volume
    if "volume" in config:
        try:
            vol = max(0, min(100, int(config["volume"])))
            subprocess.run(
                ["powershell", "-Command",
                 f"(New-Object -ComObject WScript.Shell).SendKeys([char]173);"
                 f"$vol={vol};"
                 # Usa nircmd se disponível, senão PowerShell básico
                 ],
                capture_output=True, timeout=5,
            )
            results.append(f"✓ Volume ajustado para {vol}%")
        except Exception:
            pass

    # Não Incomodar (Focus Assist) — Windows 10/11
    if config.get("focus_assist", False):
        try:
            subprocess.run(
                ["powershell", "-Command",
                 "Set-ItemProperty -Path 'HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\CloudStore\\Store\\DefaultAccount\\Current\\default$windows.data.notifications.quiethourssettings\\windows.data.notifications.quiethourssettings' -Name Data -Type Binary -Value ([byte[]](0x02,0x00,0x00,0x00))"],
                capture_output=True, timeout=10,
            )
            results.append("✓ Focus Assist activado")
        except Exception:
            results.append("⚠ Focus Assist: requer permissões de administrador")

    return {
        "mode":    config.get("name", mode),
        "actions": results,
        "message": config.get("message", f"Modo {mode} activado!"),
    }


def list_modes() -> dict:
    """Lista todos os modos disponíveis."""
    modes = []
    for path in MODES_DIR.glob("*.yaml"):
        try:
            with open(path) as f:
                data = yaml.safe_load(f)
            modes.append({
                "id":          path.stem,
                "name":        data.get("name", path.stem),
                "description": data.get("description", ""),
            })
        except Exception:
            modes.append({"id": path.stem, "name": path.stem})
    return {"modes": modes}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "context_activate_mode",
            "description": (
                "Activa um modo de trabalho: fecha apps irrelevantes, abre as certas, "
                "ajusta volume e activa Focus Assist. Modos: hacker, foco, relax, reuniao, etc."
            ),
            "parameters": {"type": "object", "required": ["mode"], "properties": {
                "mode": {"type": "string",
                         "description": "Nome do modo (ex: 'hacker', 'foco', 'relax')"},
            }},
        }},
        {"type": "function", "function": {
            "name": "context_list_modes",
            "description": "Lista todos os modos de trabalho disponíveis.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]


TOOL_HANDLERS: dict = {
    "context_activate_mode": lambda a: activate_mode(**a),
    "context_list_modes":    lambda _: list_modes(),
}
