"""NANO warns about the PC itself: battery, disk, network and finished downloads.

WHAT IT WATCHES (every 30 seconds, all local)
---------------------------------------------
* **Battery** — on battery power and at or below 20 %, once; again at or
  below 10 %. Plugging the charger in resets it.
* **Disk** — the system drive with less than 10 % or less than 5 GB free,
  at most once every 6 hours.
* **Network** — the connection dropped (two readings in a row, so a Wi-Fi
  hiccup says nothing), and that it came back.
* **Downloads** — a new file in the Downloads folder that has stopped growing
  and is not a browser's partial file (.crdownload, .part, .tmp…).

WHAT IT DOES NOT DO
-------------------
It sends nothing anywhere. Connectivity is what Windows already believes
(``core.pc_control.system.network_status``), never a ping to a server. Only a
downloaded file's NAME is shown, never its contents, and the folder is only
listed, never opened. Each warning is a switch in Definições → Geral → "Avisos
do PC", on by default. Notices go out through the same channel as the morning
briefing (``main._on_proactive_notice``): the window, a Windows notification,
and the phone when it is paired.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.i18n import tr

logger = logging.getLogger("nano.pc_alerts")

TICK_SECONDS = 30.0
BATTERY_LOW = 20
BATTERY_CRITICAL = 10
DISK_FREE_PERCENT = 10.0
DISK_FREE_GB = 5.0
DISK_REPEAT_SECONDS = 6 * 3600
#: A drop must be seen this many times in a row before it is announced.
NETWORK_CONFIRM_TICKS = 2
PARTIAL_SUFFIXES = (".crdownload", ".part", ".partial", ".tmp", ".download", ".opdownload",
                    ".!ut", ".aria2", ".dtapart")
MAX_DOWNLOADS_PER_TICK = 3


def _settings() -> dict[str, bool]:
    try:
        from core import user_settings
        get = user_settings.get
    except Exception:  # pragma: no cover
        def get(_key, default=None):
            return default
    return {key: get(f"pc_alerts_{key}", True) is not False
            for key in ("battery", "disk", "network", "downloads")}


# ── readings (each one replaceable in tests) ─────────────────────────────
def read_battery() -> dict | None:
    try:
        import psutil

        battery = psutil.sensors_battery()
    except Exception:
        return None
    if battery is None:
        return None
    return {"percent": round(float(battery.percent)), "plugged": bool(battery.power_plugged)}


def _system_drive() -> str:
    if sys.platform == "win32":
        return os.environ.get("SystemDrive", "C:") + os.sep
    return "/"


def read_disk() -> dict | None:
    try:
        usage = shutil.disk_usage(_system_drive())
    except OSError:
        return None
    return {"drive": _system_drive(), "free_gb": usage.free / 1024 ** 3,
            "free_percent": usage.free * 100.0 / usage.total if usage.total else 100.0}


def read_network() -> bool | None:
    """True online, False offline, None when it cannot be told."""
    try:
        from core.pc_control import system

        status = system.network_status()
    except Exception:
        return None
    if status.get("connected") is not None:
        return bool(status["connected"])
    interfaces = status.get("interfaces") or []
    if not interfaces:
        return None
    return any(item.get("up") for item in interfaces)


def downloads_dir() -> Path:
    return Path.home() / "Downloads"


def read_downloads(folder: Path | None = None) -> dict[str, tuple[int, float]] | None:
    """``{name: (size, mtime)}`` of the files directly in Downloads."""
    folder = folder or downloads_dir()
    if not folder.is_dir():
        return None
    out: dict[str, tuple[int, float]] = {}
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    if entry.is_file(follow_symlinks=False):
                        stat = entry.stat(follow_symlinks=False)
                        out[entry.name] = (stat.st_size, stat.st_mtime)
                except OSError:
                    continue
    except OSError:
        return None
    return out


def _partial(name: str) -> bool:
    lower = name.casefold()
    return lower.endswith(PARTIAL_SUFFIXES) or lower.startswith((".", "~$"))


def notice(kind: str, title: str, text: str) -> dict:
    return {"kind": kind, "title": title, "text": text}


class PcAlerts:
    """Decides, every tick, whether something about the PC is worth saying."""

    def __init__(self, *, emit: Callable[[dict], None],
                 settings: Callable[[], dict[str, bool]] = _settings,
                 battery: Callable[[], dict | None] = read_battery,
                 disk: Callable[[], dict | None] = read_disk,
                 network: Callable[[], bool | None] = read_network,
                 downloads: Callable[[], dict[str, tuple[int, float]] | None] = read_downloads,
                 clock: Callable[[], float] = time.time) -> None:
        self._emit = emit
        self._settings = settings
        self._battery = battery
        self._disk = disk
        self._network = network
        self._downloads = downloads
        self._clock = clock
        self._battery_level_said: int | None = None
        self._disk_said_at: float | None = None
        self._online: bool | None = None
        self._offline_ticks = 0
        self._said_offline = False
        self._seen: dict[str, tuple[int, float]] | None = None
        self._growing: dict[str, tuple[int, float]] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ── the decision ─────────────────────────────────────────────────────
    def tick(self) -> list[dict]:
        settings = self._settings()
        out: list[dict] = []
        for key, check in (("battery", self._check_battery), ("disk", self._check_disk),
                           ("network", self._check_network), ("downloads", self._check_downloads)):
            if not settings.get(key, True):
                if key == "downloads":
                    self._seen = None          # start fresh when switched back on
                continue
            try:
                out += check()
            except Exception:
                logger.debug("PC alert check %s failed", key, exc_info=True)
        for item in out:
            try:
                self._emit(item)
            except Exception:
                logger.debug("Could not show a PC alert", exc_info=True)
        return out

    def _check_battery(self) -> list[dict]:
        reading = self._battery()
        if reading is None:
            return []
        percent, plugged = int(reading["percent"]), bool(reading["plugged"])
        if plugged:
            self._battery_level_said = None
            return []
        level = BATTERY_CRITICAL if percent <= BATTERY_CRITICAL else (
            BATTERY_LOW if percent <= BATTERY_LOW else None)
        if level is None or (self._battery_level_said is not None and self._battery_level_said <= level):
            return []
        self._battery_level_said = level
        if level == BATTERY_CRITICAL:
            text = tr("A bateria está em {percent}%. Liga o carregador já, ou guarda o que "
                      "estás a fazer.", percent=percent)
        else:
            text = tr("A bateria está em {percent}%. Convém ligar o carregador.", percent=percent)
        return [notice("battery", tr("Bateria fraca"), text)]

    def _check_disk(self) -> list[dict]:
        reading = self._disk()
        if reading is None:
            return []
        low = reading["free_percent"] < DISK_FREE_PERCENT or reading["free_gb"] < DISK_FREE_GB
        if not low:
            self._disk_said_at = None
            return []
        now = self._clock()
        if self._disk_said_at is not None and now - self._disk_said_at < DISK_REPEAT_SECONDS:
            return []
        self._disk_said_at = now
        return [notice("disk", tr("Disco quase cheio"),
                       tr("O disco {drive} só tem {free} GB livres ({percent}%). Posso ajudar "
                          "a ver o que ocupa espaço.", drive=reading["drive"].rstrip("\\/"),
                          free=f"{reading['free_gb']:.1f}", percent=round(reading["free_percent"])))]

    def _check_network(self) -> list[dict]:
        online = self._network()
        if online is None:
            return []
        out: list[dict] = []
        if online:
            if self._said_offline:
                out.append(notice("network", tr("Rede"), tr("A ligação à rede voltou.")))
            self._online = True
            self._offline_ticks = 0
            self._said_offline = False
        elif self._online:
            # Only a DROP is news (starting NANO offline is not), and only one
            # that lasts: a single bad reading is a Wi-Fi hiccup.
            self._offline_ticks += 1
            if self._offline_ticks >= NETWORK_CONFIRM_TICKS and not self._said_offline:
                self._said_offline = True
                out.append(notice("network", tr("Sem rede"),
                                  tr("A ligação à rede caiu. O que precisa da internet (IA na "
                                     "nuvem, pesquisa, voz) pode falhar até voltar.")))
        return out

    def _check_downloads(self) -> list[dict]:
        current = self._downloads()
        if current is None:
            return []
        if self._seen is None:
            self._seen = current      # what was there before NANO looked is old news
            return []
        out: list[dict] = []
        for name, (size, mtime) in current.items():
            if name in self._seen or _partial(name):
                continue
            before = self._growing.get(name)
            if before is None or before != (size, mtime) or size == 0:
                self._growing[name] = (size, mtime)   # wait for it to stop growing
                continue
            self._growing.pop(name, None)
            self._seen[name] = (size, mtime)
            if len(out) < MAX_DOWNLOADS_PER_TICK:
                out.append(notice("download", tr("Download terminado"),
                                  tr("Chegou um ficheiro novo às Transferências: {name}", name=name[:120])))
        for name in list(self._growing):
            if name not in current:
                self._growing.pop(name, None)
        for name in list(self._seen):
            if name not in current:
                self._seen.pop(name, None)
        return out

    # ── the thread ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="nano-pc-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        if self._stop.wait(10.0):
            return
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:
                logger.exception("PC alerts loop failed")
            self._stop.wait(TICK_SECONDS)


__all__ = ["PcAlerts", "read_battery", "read_disk", "read_downloads", "read_network"]
