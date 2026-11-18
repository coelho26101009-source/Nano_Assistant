"""A Windows notification without the desktop shell.

When Nano runs from NANO_DESKTOP.bat, Electron shows due reminders as native
notifications. Started from NANO.bat (browser mode) there is no Electron, so a
reminder only appeared as a toast inside the open page: with the tab in the
background or closed, nothing reached the user at all. This module shows a
real Windows toast from Python in that case.

It uses the WinRT toast API through Windows PowerShell, so it needs no extra
package. The title and body are user/model text, so they never touch the
command line: they travel in environment variables and are XML-escaped inside
the script.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys

logger = logging.getLogger("nano.windows_toast")

#: An AppUserModelID Windows already knows (Windows PowerShell's own), so the
#: toast is shown without registering a Start-menu shortcut for Nano.
_APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$t = [System.Security.SecurityElement]::Escape($env:NANO_TOAST_TITLE)
$b = [System.Security.SecurityElement]::Escape($env:NANO_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast scenario='reminder'><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual><actions><action content='OK' arguments='dismiss' activationType='system'/></actions><audio src='ms-winsoundevent:Notification.Reminder'/></toast>")
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($env:NANO_TOAST_APPID).Show($toast)
"""


def _clean(text: str, limit: int) -> str:
    flat = " ".join("".join(ch if ch.isprintable() else " " for ch in str(text or "")).split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


def show(title: str, body: str, *, timeout: float = 15.0) -> bool:
    """Show a Windows toast. Returns False (and logs why) when it could not."""
    if sys.platform != "win32":
        return False
    title, body = _clean(title, 64), _clean(body, 240)
    if not body:
        return False
    env = {**os.environ, "NANO_TOAST_TITLE": title, "NANO_TOAST_BODY": body,
           "NANO_TOAST_APPID": _APP_ID}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", _SCRIPT],
            env=env, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("Notificação do Windows falhou: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning("Notificação do Windows falhou: %s", (result.stderr or "").strip()[:300])
        return False
    return True
