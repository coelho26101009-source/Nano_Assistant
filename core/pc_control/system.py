"""A read-only snapshot of the machine, with the identifying bits left out.

WHAT IS DELIBERATELY ABSENT: serial numbers, product keys, MAC addresses,
IP addresses, the Windows licence, environment variables, and the user account
name. None of it helps answer "como está a RAM do meu computador?", all of it
is durable identifying data, and this snapshot is the kind of thing that ends
up pasted into a chat log or sent to a cloud model.

The hostname is included because Nano is a local assistant on a personal
machine and "which computer am I on" is a reasonable thing for it to know.
"""
from __future__ import annotations

import logging
import platform
import time

logger = logging.getLogger("nano.pc_control.system")


def _gpu_name() -> str | None:
    """GPU model, only if it can be read without shelling out.

    nvidia-smi is a fixed argument vector -- no shell, no user input reaching
    it -- and it is simply absent on machines without an NVIDIA card, in which
    case this returns None rather than guessing.
    """
    import shutil
    import subprocess

    executable = shutil.which("nvidia-smi")
    if not executable:
        return None
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=5,
        )
        if completed.returncode != 0:
            return None
        return (completed.stdout.strip().splitlines() or [None])[0]
    except Exception:
        return None


def info() -> dict:
    import psutil

    memory = psutil.virtual_memory()
    snapshot: dict = {
        "os": f"{platform.system()} {platform.release()}",
        "os_build": platform.version(),
        "hostname": platform.node(),
        "architecture": platform.machine(),
        "cpu": platform.processor() or None,
        "cpu_cores_physical": psutil.cpu_count(logical=False),
        "cpu_cores_logical": psutil.cpu_count(logical=True),
        # A short interval so the reading is real rather than the meaningless
        # 0.0 that a non-blocking first call returns.
        "cpu_percent": psutil.cpu_percent(interval=0.2),
        "ram_total_gb": round(memory.total / (1024 ** 3), 1),
        "ram_used_gb": round(memory.used / (1024 ** 3), 1),
        "ram_percent": memory.percent,
    }

    try:
        disk = psutil.disk_usage("C:\\" if platform.system() == "Windows" else "/")
        snapshot.update({
            "disk_total_gb": round(disk.total / (1024 ** 3), 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
            "disk_percent": disk.percent,
        })
    except Exception:
        logger.debug("disk usage unavailable", exc_info=True)

    gpu = _gpu_name()
    if gpu:
        snapshot["gpu"] = gpu

    try:
        battery = psutil.sensors_battery()
        if battery is not None:
            snapshot["battery_percent"] = round(battery.percent)
            snapshot["battery_plugged"] = bool(battery.power_plugged)
    except Exception:
        logger.debug("battery status unavailable", exc_info=True)

    try:
        uptime = max(0.0, time.time() - psutil.boot_time())
        snapshot["uptime_hours"] = round(uptime / 3600, 1)
    except Exception:
        logger.debug("uptime unavailable", exc_info=True)

    return snapshot


__all__ = ["info"]
