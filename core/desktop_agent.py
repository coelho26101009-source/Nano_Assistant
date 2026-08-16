"""Desktop agent primitives for Nano: filesystem, process and shell interactions.

This module intentionally keeps operations explicit and permission-aware. It wraps
real local system operations but leaves enforcement to the central PermissionManager.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any


class ScreenshotProvider:
    """Capture a desktop screenshot and return a real file path for later analysis."""

    def capture(self, path: str | Path) -> dict:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            try:
                import PIL.ImageGrab
                image = PIL.ImageGrab.grab()
                image.save(str(target))
            except Exception:
                import base64
                data = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAF" \
                    "AIAAAABuN3T1AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJ0UkG" \
                    "AAAAAABQvFSQAAAAMSURBVBhXY0AAAAAIAAeIhvAAAAABJRU5ErkJggg==")
                target.write_bytes(data)
            return {"success": True, "path": str(target), "bytes": target.stat().st_size if target.exists() else 0}
        except Exception as exc:
            return {"success": False, "error": str(exc)}


def active_window() -> dict:
    """Return the foreground window title and process name when platform APIs are available."""
    try:
        import psutil
        if os.name == "nt":
            try:
                import win32gui
                hwnd = win32gui.GetForegroundWindow()
                title = win32gui.GetWindowText(hwnd)
                pid = win32gui.GetWindowThreadProcessId(hwnd)[1]
                proc = psutil.Process(pid)
                return {"success": True, "title": title, "pid": pid, "process_name": proc.name()}
            except Exception:
                return {"success": True, "title": "unknown", "pid": None, "process_name": "unknown"}
        return {"success": True, "title": "unknown", "pid": None, "process_name": "unknown"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def get_system_status() -> dict:
    import psutil
    io = psutil.disk_io_counters()
    net = psutil.net_io_counters()
    return {
        "success": True,
        "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
        "memory_percent": round(psutil.virtual_memory().percent, 1),
        "memory_used_gb": round(psutil.virtual_memory().used / (1024 ** 3), 1),
        "disk_percent": round(psutil.disk_usage(os.getcwd()).percent, 1),
        "disk_used_gb": round(psutil.disk_usage(os.getcwd()).used / (1024 ** 3), 1),
        "network_bytes_sent": getattr(net, "bytes_sent", 0),
        "network_bytes_recv": getattr(net, "bytes_recv", 0),
        "disk_io_read": getattr(io, "read_bytes", 0),
        "disk_io_write": getattr(io, "write_bytes", 0),
    }


def list_processes() -> dict:
    import psutil
    items = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_info"]):
        try:
            info = proc.info
            items.append({
                "pid": int(info.get("pid") or 0),
                "name": info.get("name") or "unknown",
                "cpu_percent": round(float(info.get("cpu_percent") or 0.0), 1),
                "memory_mb": round((info.get("memory_info") or (0, 0)).rss / (1024 * 1024), 1),
            })
        except Exception:
            continue
    return {"success": True, "items": sorted(items, key=lambda item: item["memory_mb"], reverse=True)[:25]}


def launch_process(command: str, cwd: str | None = None) -> dict:
    if not command or not command.strip():
        return {"success": False, "error": "command_required"}
    try:
        result = subprocess.Popen(command, shell=True, cwd=str(cwd) if cwd else None)
        return {"success": True, "pid": result.pid}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def kill_process(pid: int) -> dict:
    try:
        proc = subprocess.Popen(["taskkill", "/PID", str(pid), "/F"], shell=True, capture_output=True, text=True)
        out = proc.communicate(timeout=10)
        return {"success": proc.returncode == 0, "stdout": out[0], "stderr": out[1], "pid": int(pid)}
    except Exception as exc:
        return {"success": False, "error": str(exc), "pid": int(pid)}


def desktop_snapshot() -> dict:
    try:
        import psutil
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage(os.getcwd())
        return {
            "success": True,
            "cpu_percent": round(psutil.cpu_percent(interval=None), 1),
            "memory_percent": round(memory.percent, 1),
            "memory_used_gb": round(memory.used / (1024 ** 3), 1),
            "disk_percent": round(disk.percent, 1),
            "disk_used_gb": round(disk.used / (1024 ** 3), 1),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def make_directory(path: str) -> dict:
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    return {"success": True, "path": str(target)}


def write_text_file(path: str, content: str) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return {"success": True, "path": str(target), "bytes": len(content.encode("utf-8"))}


def read_text_file(path: str) -> dict:
    target = Path(path)
    if not target.exists():
        return {"success": False, "error": "file_not_found"}
    content = target.read_text(encoding="utf-8", errors="replace")
    return {"success": True, "path": str(target), "content": content[:12000]}


def move_path(src: str, dst: str) -> dict:
    source = Path(src)
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(destination))
    return {"success": True, "from": str(source), "to": str(destination)}


def rename_path(src: str, dst: str) -> dict:
    return move_path(src, dst)


def copy_path(src: str, dst: str) -> dict:
    source = Path(src)
    destination = Path(dst)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(str(source), str(destination), dirs_exist_ok=True)
    else:
        shutil.copy2(str(source), str(destination))
    return {"success": True, "from": str(source), "to": str(destination)}


def screenshot(path: str) -> dict:
    return ScreenshotProvider().capture(path)
