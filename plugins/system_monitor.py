"""
H.E.L.I.O.S. Plugin: System Monitor
Vigia CPU / RAM / disco em background e avisa proactivamente quando algo passa
dos limites definidos em config/settings.yaml.

Impacto mínimo: uma thread daemon que acorda a cada 60s (configurável) e usa
apenas leituras do psutil.
"""

import asyncio
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

import psutil

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import get_setting
from core.memory import get_memory
from plugins.smart_life import phone_notification

logger = logging.getLogger("helios.plugins.system_monitor")

_monitor: threading.Thread | None = None
_stop = threading.Event()
_last_alert: dict[str, datetime] = {}


def _cfg(key: str, default):
    return get_setting(f"monitor.{key}", default)


def snapshot() -> dict:
    """Leitura instantânea de CPU, RAM, disco e bateria."""
    if sys.platform == "win32":
        disk_path = os.environ.get("SystemDrive", "C:") + os.sep
    else:
        disk_path = "/"
    try:
        disk = psutil.disk_usage(disk_path)
    except Exception:
        disk = psutil.disk_usage("/")

    ram = psutil.virtual_memory()
    data = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "ram_percent": ram.percent,
        "ram_used_gb": round(ram.used / 1024**3, 1),
        "ram_total_gb": round(ram.total / 1024**3, 1),
        "disk_percent": disk.percent,
        "disk_free_gb": round(disk.free / 1024**3, 1),
    }

    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            data["battery_percent"] = round(battery.percent)
            data["battery_plugged"] = battery.power_plugged
    except Exception:
        pass
    return data


def _check_thresholds(stats: dict) -> list[str]:
    alerts = []
    if stats["cpu_percent"] >= float(_cfg("cpu_threshold", 90)):
        alerts.append(f"CPU a {stats['cpu_percent']:.0f}%")
    if stats["ram_percent"] >= float(_cfg("ram_threshold", 90)):
        alerts.append(f"RAM a {stats['ram_percent']:.0f}% ({stats['ram_used_gb']}GB)")
    if stats["disk_percent"] >= float(_cfg("disk_threshold", 90)):
        alerts.append(f"disco a {stats['disk_percent']:.0f}% (só {stats['disk_free_gb']}GB livres)")
    battery = stats.get("battery_percent")
    if battery is not None and not stats.get("battery_plugged") and battery <= float(_cfg("battery_threshold", 15)):
        alerts.append(f"bateria a {battery}%")
    return alerts


def _cooldown_passed(key: str) -> bool:
    minutes = float(_cfg("alert_cooldown_minutes", 30))
    last = _last_alert.get(key)
    if last and (datetime.now() - last).total_seconds() < minutes * 60:
        return False
    _last_alert[key] = datetime.now()
    return True


def _alert(alerts: list[str]):
    message = "⚠️ Atenção Simão: " + ", ".join(alerts) + "."
    logger.warning(message)
    get_memory().save_message("assistant", message, {"source": "system_monitor"})

    webhook = get_setting("iot.notification_webhook", "")
    if webhook:
        try:
            asyncio.run(phone_notification(message, webhook))
        except Exception as exc:
            logger.warning(f"Notificação de alerta falhou: {exc}")


def _loop():
    while not _stop.is_set():
        try:
            alerts = [a for a in _check_thresholds(snapshot()) if _cooldown_passed(a.split()[0])]
            if alerts:
                _alert(alerts)
        except Exception as exc:
            logger.error(f"Monitor de sistema: {exc}")
        _stop.wait(float(_cfg("interval_seconds", 60)))


def start_monitor() -> dict:
    """Arranca a vigilância proactiva em background (idempotente)."""
    global _monitor
    if _monitor and _monitor.is_alive():
        return {"running": True, "message": "A monitorização já estava activa."}
    _stop.clear()
    _monitor = threading.Thread(target=_loop, name="helios-system-monitor", daemon=True)
    _monitor.start()
    logger.info("Monitorização proactiva do sistema activa.")
    return {"running": True, "message": "Monitorização proactiva activada."}


def stop_monitor() -> dict:
    _stop.set()
    return {"running": False, "message": "Monitorização proactiva desligada."}


def monitor_status() -> dict:
    stats = snapshot()
    return {
        "running": bool(_monitor and _monitor.is_alive()),
        "stats": stats,
        "alerts": _check_thresholds(stats),
        "thresholds": {
            "cpu": _cfg("cpu_threshold", 90),
            "ram": _cfg("ram_threshold", 90),
            "disk": _cfg("disk_threshold", 90),
            "battery": _cfg("battery_threshold", 15),
        },
    }


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "monitor_status",
            "description": "Estado actual do PC (CPU, RAM, disco, bateria) e se algum limite está a ser ultrapassado.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "monitor_start",
            "description": "Liga a vigilância proactiva do sistema (alertas automáticos).",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "monitor_stop",
            "description": "Desliga a vigilância proactiva do sistema.",
            "parameters": {"type": "object", "properties": {}},
        }},
    ]


TOOL_HANDLERS: dict = {
    "monitor_status": lambda _: monitor_status(),
    "monitor_start": lambda _: start_monitor(),
    "monitor_stop": lambda _: stop_monitor(),
}


if get_setting("monitor.enabled", True):
    start_monitor()
