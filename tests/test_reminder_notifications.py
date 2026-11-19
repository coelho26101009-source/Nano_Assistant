"""Due reminders reach the PC: local notifiers, the desktop event and the UI."""
from __future__ import annotations

import sqlite3

import pytest

from plugins import reminders


class _FakeMemory:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.saved: list[str] = []

    def save_message(self, role, content, metadata=None):
        self.saved.append(content)


@pytest.fixture
def memory(monkeypatch):
    fake = _FakeMemory()
    settings: dict = {"iot.notification_webhook": ""}
    monkeypatch.setattr(reminders, "get_memory", lambda: fake)
    monkeypatch.setattr(reminders, "start_scheduler", lambda: True)
    monkeypatch.setattr(reminders, "get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr(reminders, "_notifiers", [])
    fake.settings = settings
    return fake


def test_a_due_reminder_is_handed_to_every_local_notifier(memory):
    seen_a, seen_b = [], []
    reminders.register_notifier(seen_a.append)
    reminders.register_notifier(seen_b.append)
    created = reminders.set_reminder("ligar à avó", "10 minutos")
    assert reminders._fire(created["id"], "ligar à avó", "2030-01-02T18:30:00") is True
    assert seen_a == seen_b
    assert seen_a[0]["id"] == created["id"]
    assert seen_a[0]["text"] == "ligar à avó"
    assert seen_a[0]["due_label"] == "18:30"


def test_a_failing_notifier_does_not_block_the_others_or_refire(memory):
    seen = []

    def broken(_payload):
        raise RuntimeError("toast service down")

    reminders.register_notifier(broken)
    reminders.register_notifier(seen.append)
    created = reminders.set_reminder("beber água", "5 minutos")
    assert reminders._fire(created["id"], "beber água") is True
    assert len(seen) == 1
    assert reminders._fire(created["id"], "beber água") is False
    assert len(seen) == 1


def test_registering_twice_notifies_once(memory):
    seen = []
    reminders.register_notifier(seen.append)
    reminders.register_notifier(seen.append)
    created = reminders.set_reminder("x", "5 minutos")
    reminders._fire(created["id"], "x")
    assert len(seen) == 1
    reminders.unregister_notifier(seen.append)
    assert reminders._notifiers == []


def test_desktop_notifications_can_be_turned_off(memory):
    seen = []
    memory.settings["reminders.desktop_notifications"] = False
    reminders.register_notifier(seen.append)
    created = reminders.set_reminder("x", "5 minutos")
    assert reminders._fire(created["id"], "x") is True
    assert seen == []
    # Still saved to memory: turning off the toast does not lose the reminder.
    assert memory.saved == ["⏰ Lembrete: x"]


def test_main_sends_reminders_to_the_desktop_shell_and_the_ui(monkeypatch):
    from core import main

    emitted, ui = [], []
    monkeypatch.setattr(main, "_desktop_emit", lambda event, payload=None: emitted.append((event, payload)))
    monkeypatch.setattr(main, "_notify_ui", lambda name, *args: ui.append((name, args)))
    monkeypatch.setattr(main, "_DESKTOP_BRIDGE", object())
    main._on_reminder_due({"id": 3, "text": "t" * 900, "due_label": "09:00", "secret": "x"})
    assert emitted[0][0] == "reminder_due"
    payload = emitted[0][1]
    assert set(payload) == {"id", "text", "due_label"}
    assert len(payload["text"]) == 500 and payload["due_label"] == "09:00"
    assert ui == [("on_reminder_due", (payload,))]


def test_browser_mode_shows_the_windows_notification_itself(monkeypatch):
    """NANO.bat has no Electron: the reminder must still reach Windows."""
    import threading

    from core import main, windows_toast

    shown, ui = [], []
    monkeypatch.setattr(main, "_DESKTOP_BRIDGE", None)
    monkeypatch.setattr(main, "_notify_ui", lambda name, *args: ui.append((name, args)))
    monkeypatch.setattr(windows_toast, "show", lambda title, body: shown.append((title, body)))
    main._on_reminder_due({"id": 4, "text": "beber agua", "due_label": "21:05"})
    for thread in threading.enumerate():
        if thread.name == "nano-reminder-toast":
            thread.join(timeout=5)
    assert shown == [("Lembrete · 21:05", "beber agua")]
    assert ui and ui[0][0] == "on_reminder_due"


def test_windows_toast_never_puts_text_on_the_command_line(monkeypatch):
    import subprocess

    from core import windows_toast

    calls = []

    class Done:
        returncode = 0
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append((args, kwargs["env"]))
        return Done()

    monkeypatch.setattr(windows_toast.sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", fake_run)
    hostile = r"'; Remove-Item C:\ -Recurse; '<x>"
    assert windows_toast.show("Lembrete", hostile) is True
    args, env = calls[0]
    assert all(hostile not in a for a in args)
    assert env["NANO_TOAST_BODY"] == hostile


def test_main_registers_the_notifier_with_the_running_plugin_instance(monkeypatch):
    from core import main, plugin_loader

    monkeypatch.setattr(reminders, "_notifiers", [])
    monkeypatch.setattr(plugin_loader, "_loaded_plugins", {"reminders": reminders})
    assert main._register_reminder_notifier() is True
    assert main._on_reminder_due in reminders._notifiers


def test_without_the_reminders_plugin_nothing_is_registered(monkeypatch):
    from core import main, plugin_loader

    monkeypatch.setattr(plugin_loader, "_loaded_plugins", {})
    assert main._register_reminder_notifier() is False


def test_the_loader_hands_out_the_instance_whose_scheduler_runs(tmp_path):
    from core import plugin_loader

    plugin_loader.load_all_plugins()
    loaded = plugin_loader.get_plugin("reminders")
    assert loaded is not None and loaded.__name__ == "nano.plugins.reminders"
    assert loaded is not reminders  # a plain import is a different copy
    assert plugin_loader.get_plugin("does_not_exist") is None


def test_reminder_due_is_a_desktop_event_the_shell_turns_into_a_notification():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    shell = (root / "electron" / "main.js").read_text(encoding="utf-8")
    lib = (root / "electron" / "lib" / "notifications.js").read_text(encoding="utf-8")
    assert "notifications.fromBackendEvent(event, payload)" in shell
    assert "'reminder_due'" in lib
