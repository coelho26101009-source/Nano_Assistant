"""Security review 2026-11-25: paths, the permission gate, keys and plugins.

Each test names one hole found in the review and checks it stays closed.
"""
from __future__ import annotations

import concurrent.futures
import threading
from pathlib import Path

import pytest

from core import execution_scope, plugin_loader, secret_store
from core.execution_scope import network_or_device_path, resolve_target
from core.pc_control import applications, fileops, files, windows
from core.pc_control.results import PCControlError
from core.permission_manager import PermissionManager
from core.policy_engine import AuthorityDecision, AutonomyMode, PolicyEngine, execution_is_mutating
from core.tool_execution import ToolExecutor, _pc_control_target
import core.tool_execution as tool_execution


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    root.mkdir()
    monkeypatch.setenv("NANO_WORKSPACE_ROOT", str(root))
    return root


@pytest.fixture
def manager(tmp_path):
    return PermissionManager(policy_store_path=tmp_path / "policies.json")


# ══ Network and device paths ═════════════════════════════════════════════

@pytest.mark.parametrize("raw, kind", [
    ("\\\\evil\\share", "unc"),
    ("//evil/share", "unc"),
    ("\\/evil/share", "unc"),            # mixed separators: Windows reads both
    ("/\\evil\\share", "unc"),
    ("///evil/share", "unc"),
    ("\\\\?\\C:\\Windows", "device"),
    ("/\\?\\C:\\Windows", "device"),
    ("\\\\.\\PhysicalDrive0", "device"),
    ("C:\\Users\\simao", None),
    ("/home/simao", None),
    ("Documentos", None),
    ("", None),
])
def test_network_and_device_paths_are_recognised_in_any_spelling(raw, kind):
    assert network_or_device_path(raw) == kind


def test_a_folder_on_another_machine_is_refused_before_it_is_touched(monkeypatch):
    touched: list[str] = []
    real_exists = Path.exists

    def exists(self, *args, **kwargs):
        touched.append(str(self))
        return real_exists(self, *args, **kwargs)

    monkeypatch.setattr(Path, "exists", exists)
    monkeypatch.setenv("NANO_TEST_SHARE", "//attacker.example/drop")
    for raw in ("\\\\attacker.example\\drop", "\\/attacker.example/drop", "$NANO_TEST_SHARE"):
        for call in (files.resolve_folder, files.open_file, fileops._existing):
            with pytest.raises(PCControlError) as caught:
                call(raw)
            assert caught.value.status == "network_path_refused", (call, raw)
    assert not any("attacker" in item for item in touched)


def test_a_copy_cannot_land_on_another_machine(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(PCControlError) as caught:
        fileops._destination_for(source, "\\\\attacker.example\\drop")
    assert caught.value.status == "network_path_refused"


def test_home_relative_paths_mean_the_users_home(workspace, monkeypatch, tmp_path):
    home = tmp_path / "home"
    (home / "Documentos").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))      # what Windows reads for "~"
    target = resolve_target("~/Documentos")
    assert target.path == (home / "Documentos").resolve()


def test_cloud_credentials_under_dot_config_are_protected(tmp_path):
    assert execution_scope._is_protected(tmp_path / ".config" / "gcloud" / "credentials.db")
    assert execution_scope._is_protected(tmp_path / ".config" / "gcloud" / "access_tokens")
    assert not execution_scope._is_protected(tmp_path / ".config" / "other" / "settings.json")


# ══ Protected files and executable types ═════════════════════════════════

def test_writing_or_deleting_a_protected_file_is_blocked_not_asked():
    engine = PolicyEngine()
    for capability in ("filesystem.write", "filesystem.delete"):
        result = engine.evaluate(capability, target="/x/nano.db", scope="current_workspace",
                                 context={"protected_target": True})
        assert result.decision == AuthorityDecision.BLOCKED, capability
    read = engine.evaluate("filesystem.read", target="/x/.env", scope="current_workspace",
                           context={"protected_target": True})
    assert read.decision == AuthorityDecision.APPROVAL_REQUIRED


def test_a_full_path_in_file_path_goes_through_the_central_checks(workspace, manager):
    (workspace / "nano.db").write_bytes(b"")
    seen: list[dict] = []
    manager.confirmation_callback = lambda *_: True
    executor = ToolExecutor(permission_manager=manager)
    executor.register_tool(        # the real tool's name, so the real capability
        "rename_file_smart", "test", {"type": "object", "properties": {
            "file_path": {"type": "string"}, "new_name": {"type": "string"}}},
        handler=lambda args: seen.append(args) or {"ok": True},
        capabilities=["filesystem.write"])
    result = executor.execute_tool("rename_file_smart", {"file_path": "nano.db", "new_name": "x"})
    assert result["status"] == "permission_denied"
    assert not seen


@pytest.mark.parametrize("name", ["script.ahk", "x.au3", "pack.appinstaller", "fix.msu",
                                  "a.ppkg", "dark.theme", "find.search-ms", "disk.iso", "d.vhdx"])
def test_more_file_types_that_run_or_install_are_refused(name):
    assert Path(name).suffix.lower() in files.EXECUTABLE_EXTENSIONS


# ══ The permission gate ══════════════════════════════════════════════════

def test_an_emergency_stop_pressed_during_the_question_wins(workspace, manager):
    def answer(*_):
        manager.set_emergency_stop(True)      # pressed while the card was open
        return True

    manager.confirmation_callback = answer
    try:
        assert manager.ask_for_confirmation("filesystem.write", {"path": str(workspace / "a.txt")}) is False
    finally:
        manager.set_emergency_stop(False)


def test_an_emergency_stop_also_wins_on_the_async_path(workspace, manager):
    import asyncio

    async def answer(*_):
        manager.set_emergency_stop(True)
        return True

    manager.async_confirmation_callback = answer
    try:
        approved = asyncio.run(manager.ask_for_confirmation_async(
            "filesystem.write", {"path": str(workspace / "a.txt")}))
        assert approved is False
    finally:
        manager.set_emergency_stop(False)


def test_a_blocked_action_is_never_put_to_the_user_as_a_question(manager):
    asked: list[str] = []
    manager.confirmation_callback = lambda name, args: asked.append(name) or True
    assert manager.ask_for_confirmation("shell.execute", {"command": "dir"}) is False
    assert asked == []


def test_full_supervision_asks_before_anything_that_changes_the_pc():
    for capability in ("pc.app.launch", "pc.input.key", "pc.input.hotkey", "pc.web.open",
                       "pc.web.search", "pc.volume.control", "pc.media.control",
                       "nano.reminder.create", "project.test", "phone.camera.look",
                       "some.future.capability"):
        assert execution_is_mutating(capability), capability
    for capability in ("pc.window.read", "pc.app.search", "pc.file.search", "filesystem.read",
                       "nano.data.read", "nano.plan.show", "project.inspect"):
        assert not execution_is_mutating(capability), capability
    engine = PolicyEngine()
    engine.autonomy_mode = AutonomyMode.FULL_SUPERVISION
    launch = engine.evaluate("pc.app.launch", target="app:Spotify", scope="current_workspace")
    assert launch.decision == AuthorityDecision.APPROVAL_REQUIRED


def test_a_wildcard_is_not_a_target_for_a_destructive_action():
    engine = PolicyEngine()
    for target in ("window:*", "windows:*", "file:* -> C:\\x", "*"):
        result = engine.evaluate("pc.window.close", target=target, scope="current_workspace")
        assert result.decision == AuthorityDecision.BLOCKED, target
    titled = engine.evaluate("pc.window.close", target="window:*Sem título - Bloco de notas",
                             scope="current_workspace")
    assert titled.decision != AuthorityDecision.BLOCKED


def test_forgetting_a_fact_and_cleaning_the_cache_ask_instead_of_always_failing(manager):
    plugin_loader.load_all_plugins()
    for tool, args in (("forget_fact", {"key": "cor favorita"}), ("clean_windows_cache", {})):
        decision = manager.evaluate(tool, args)
        assert not decision.blocked and decision.requires_confirmation, tool


def test_a_tool_that_timed_out_while_queued_never_runs_later(workspace, manager, monkeypatch):
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    monkeypatch.setattr(tool_execution, "_TOOL_THREADS", pool)
    release = threading.Event()
    pool.submit(release.wait, 5)             # another tool holds the only worker
    ran: list[bool] = []
    executor = ToolExecutor(permission_manager=manager)
    executor.register_tool("system_stats", "test", {"type": "object", "properties": {}},
                           handler=lambda args: ran.append(True) or {"ok": True},
                           timeout=0.2)           # read-only: runs without a question
    result = executor.execute_tool("system_stats", {})
    assert result["status"] == "failed" or result.get("error") == "tool_timeout", result
    release.set()
    pool.shutdown(wait=True)
    assert ran == []


def test_a_folder_name_binds_the_grant_even_when_a_path_comes_too():
    target = _pc_control_target("pc_folder_open", {"folder": "Transferências", "path": "C:\\x"})
    assert target == "folder:Transferências"


# ══ Keys ═════════════════════════════════════════════════════════════════

def test_a_store_that_cannot_be_opened_is_kept_aside_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    path.write_bytes(b"\x00not json at all")
    monkeypatch.setattr(secret_store, "_STORE_PATH", path)
    monkeypatch.setattr(secret_store, "_dpapi_available", lambda: False)
    monkeypatch.setattr(secret_store, "_dpapi", lambda *_: None)
    assert secret_store.set_secret("groq_api_key", "gsk_new_value_1234567890")
    kept = list(tmp_path.glob("secrets.dat.unreadable-*"))
    assert len(kept) == 1 and kept[0].read_bytes() == b"\x00not json at all"
    assert secret_store._read_store() == {"groq_api_key": "gsk_new_value_1234567890"}


def test_a_busy_store_is_not_replaced_by_a_single_key(tmp_path, monkeypatch):
    path = tmp_path / "secrets.dat"
    original = b'{"groq_api_key": "a", "mistral_api_key": "b"}'
    path.write_bytes(original)
    monkeypatch.setattr(secret_store, "_STORE_PATH", path)
    monkeypatch.setattr(secret_store, "_dpapi_available", lambda: False)
    monkeypatch.setattr(secret_store, "_dpapi", lambda *_: None)

    def busy():
        raise secret_store._StoreBusy("locked")

    monkeypatch.setattr(secret_store, "_load_store", busy)
    assert secret_store.set_secret("groq_api_key", "c") is False
    assert secret_store.delete_secret("mistral_api_key") is False
    assert path.read_bytes() == original


# ══ Odds and ends ════════════════════════════════════════════════════════

def test_the_plugin_viewer_only_reads_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (tmp_path / "secret.py").write_text("KEY = 'x'", encoding="utf-8")
    assert plugin_loader.get_plugin_source("../secret", plugins)["ok"] is False
    (plugins / "ok_plugin.py").write_text("X = 1", encoding="utf-8")
    assert plugin_loader.get_plugin_source("ok_plugin", plugins)["ok"] is True


def test_the_audit_log_does_not_grow_forever(manager):
    for index in range(2100):
        manager.log_decision("pc.window.read", "allow", target=f"window:{index}")
    assert len(manager.get_audit_log()) <= 2000


def test_the_powershell_ise_counts_as_a_terminal():
    assert "powershell_ise.exe" in windows._TERMINAL_PROCESSES


def test_a_start_menu_shortcut_to_a_command_line_is_never_launched(tmp_path, monkeypatch):
    shortcut = tmp_path / "Windows PowerShell.lnk"
    shortcut.write_bytes(b"")
    launched: list[str] = []
    monkeypatch.setattr(applications.winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(applications.winapi, "resolve_shortcut",
                        lambda _: "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe")
    monkeypatch.setattr(applications.winapi, "shell_execute",
                        lambda target, **_: launched.append(target) or 1)
    entry = applications.AppEntry(name="Windows PowerShell", launch_target=str(shortcut),
                                  source="start_menu_system")
    with pytest.raises(PCControlError) as caught:
        applications.launch(entry)
    assert caught.value.status == "interpreter_refused"
    assert launched == []
