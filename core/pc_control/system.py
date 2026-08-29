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


# --------------------------------------------------------------------------
#  Network and storage
#
#  The same subtraction as `info()` above, applied to two areas where the
#  identifying data is the DEFAULT output of every library. Deliberately absent
#  here: MAC addresses, IPv4 and IPv6 addresses, gateways, DNS servers, the
#  Wi-Fi network name, saved network profiles, and volume serial numbers. None
#  of them help answer "tenho internet?" or "quanto disco me resta?", all of
#  them are durable identifiers, and this output is exactly the sort of thing
#  that ends up pasted into a chat log or sent to a cloud model.
# --------------------------------------------------------------------------

#: Bounds. A machine can have a surprising number of virtual adapters.
MAX_INTERFACES = 8
MAX_VOLUMES = 8


def network_status() -> dict:
    """Whether the machine is connected, and by what kind of link.

    `InternetGetConnectedState` is a LOCAL query -- it reports what Windows
    already believes about connectivity and sends no traffic to anybody, which
    is why there is no "reachability probe" here quietly contacting a server on
    the user's behalf. That also means it answers "is there a connection",
    not "does the internet work"; the result says so rather than overclaiming.
    """
    import psutil

    from core.pc_control import winapi

    snapshot: dict = {"connected": None, "connection_type": None,
                      "interfaces": [], "note": None}

    if winapi.IS_WINDOWS:
        try:
            connected, flags = winapi.internet_connection()
            kinds = []
            if flags & winapi.INTERNET_CONNECTION_LAN:
                kinds.append("cabo/rede local")
            if flags & winapi.INTERNET_CONNECTION_MODEM:
                kinds.append("modem")
            if flags & winapi.INTERNET_CONNECTION_PROXY:
                kinds.append("proxy")
            snapshot["connected"] = connected
            snapshot["connection_type"] = ", ".join(kinds) or None
        except Exception:
            logger.debug("connectivity query failed", exc_info=True)

    try:
        for name, stats in list(psutil.net_if_stats().items()):
            if len(snapshot["interfaces"]) >= MAX_INTERFACES:
                break
            if "loopback" in name.lower():
                continue
            snapshot["interfaces"].append({
                "name": name,
                "up": bool(stats.isup),
                "speed_mbps": int(stats.speed) or None,
            })
    except Exception:
        logger.debug("interface enumeration failed", exc_info=True)

    snapshot["note"] = ("Estado local da ligação, tal como o Windows o reporta. "
                        "Não foi contactado nenhum servidor para o confirmar.")
    return snapshot


def storage_info() -> dict:
    """Space on every fixed volume. No serial numbers, no volume identifiers."""
    import psutil

    volumes = []
    total_bytes = used_bytes = 0
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        logger.debug("partition enumeration failed", exc_info=True)
        partitions = []

    for partition in partitions[:MAX_VOLUMES]:
        try:
            usage = psutil.disk_usage(partition.mountpoint)
        except OSError:
            # A card reader with no card, or a disconnected network drive.
            continue
        total_bytes += usage.total
        used_bytes += usage.used
        volumes.append({
            "drive": partition.device,
            "filesystem": partition.fstype or None,
            "total_gb": round(usage.total / (1024 ** 3), 1),
            "used_gb": round(usage.used / (1024 ** 3), 1),
            "free_gb": round(usage.free / (1024 ** 3), 1),
            "percent_used": usage.percent,
        })

    return {
        "volumes": volumes,
        "count": len(volumes),
        "total_gb": round(total_bytes / (1024 ** 3), 1),
        "used_gb": round(used_bytes / (1024 ** 3), 1),
        "free_gb": round((total_bytes - used_bytes) / (1024 ** 3), 1),
    }


__all__ = ["MAX_INTERFACES", "MAX_VOLUMES", "info", "network_status", "storage_info"]
