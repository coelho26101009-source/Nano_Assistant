"""Work modes: data compiled into narrow PC Control calls, run through the executor."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from core import work_modes
from core.permission_manager import PermissionManager
from core.plugin_loader import get_all_tools, load_all_plugins
from core.tool_execution import ToolExecutor

ROOT = Path(__file__).resolve().parent.parent


def _write(folder: Path, name: str, text: str) -> None:
    (folder / f"{name}.yaml").write_text(text, encoding="utf-8")


class _Executor:
    def __init__(self, *, windows=None, deny=()):
        self.calls: list[tuple[str, dict]] = []
        self.windows = windows
        self.deny = set(deny)

    async def execute_tool_async(self, name, args=None, **_kw):
        self.calls.append((name, dict(args or {})))
        if name == "pc_window_list":
            if self.windows is None:
                return {"success": False, "status": "verification_failed",
                        "output": {"ok": False, "status": "unsupported_platform"}}
            return {"success": True, "status": "completed",
                    "output": {"ok": True, "windows": [{"process": p} for p in self.windows]}}
        if name in self.deny:
            return {"success": False, "status": "permission_denied", "error": "Recusado"}
        return {"success": True, "status": "completed", "output": {"ok": True, "message": "feito"}}


def run(coro):
    return asyncio.run(coro)


# ── the shipped modes ─────────────────────────────────────────────────────

def test_every_shipped_mode_compiles_to_narrow_tools_only():
    allowed = {"pc_window_batch_close", "pc_app_launch", "pc_web_open_url",
               "pc_volume_set", "pc_settings_open"}
    modes = work_modes.list_modes()
    assert {m["id"] for m in modes} >= {"foco", "hacker", "relax"}
    for mode in modes:
        assert not mode.get("error"), mode
        assert {s["tool"] for s in mode["steps"]} <= allowed
        for step in mode["steps"]:
            if step["tool"] == "pc_window_batch_close":
                assert step["asks"] is True


def test_shipped_modes_name_no_person_and_no_terminal():
    for path in (ROOT / "config" / "modes").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        body = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        assert "Simão" not in body
        assert '"wt"' not in body and "powershell" not in body.lower()


# ── validation ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,fragment", [
    ({"open_apps": ["C:\\\\Windows\\\\System32\\\\cmd.exe"]}, "nome de aplicação"),
    ({"open_apps": ["powershell"]}, "nome de aplicação"),
    ({"open_apps": ["wt"]}, "nome de aplicação"),
    ({"close_apps": ["../evil"]}, "nome de aplicação"),
    ({"open_urls": ["file:///C:/Windows"]}, "Endereço recusado"),
    ({"open_urls": ["javascript:alert(1)"]}, "Endereço recusado"),
    ({"volume": 150}, "volume"),
    ({"volume": True}, "volume"),
    ({"settings_section": "registry"}, "secção"),
    ({"open_apps": ["a"] * 9}, "mais de"),
    ({}, "não faz nada"),
    ("not a mapping", "formato"),
])
def test_bad_mode_files_are_refused(raw, fragment):
    with pytest.raises(work_modes.ModeError) as exc:
        work_modes.compile_mode("x", raw)
    assert fragment in str(exc.value)


def test_exe_suffix_is_dropped_from_app_names():
    plan = work_modes.compile_mode("x", {"close_apps": ["Discord.exe"]})
    assert plan["steps"][0]["args"] == {"app": "Discord"}


def test_mode_ids_are_an_allow_list_never_a_path(tmp_path):
    _write(tmp_path, "foco", "volume: 10\n")
    (tmp_path / "sub").mkdir()
    _write(tmp_path / "sub", "hidden", "volume: 10\n")
    assert work_modes.available_ids(tmp_path) == ["foco"]
    for bad in ("../foco", "sub/hidden", "..\\..\\Windows\\win", "FOCO.yaml", "", None):
        with pytest.raises(work_modes.ModeError):
            work_modes.load(bad, tmp_path)
    assert work_modes.load("FOCO", tmp_path)["id"] == "foco"


def test_a_broken_mode_is_listed_with_its_error(tmp_path):
    _write(tmp_path, "ok", "volume: 10\n")
    _write(tmp_path, "mau", "open_apps: [powershell]\n")
    modes = {m["id"]: m for m in work_modes.list_modes(tmp_path)}
    assert not modes["ok"].get("error")
    assert modes["mau"]["error"] and modes["mau"]["steps"] == []


# ── running ───────────────────────────────────────────────────────────────

MODE = """
name: Teste
message: Pronto!
close_apps: [discord, slack]
open_apps: [Spotify]
open_urls: [https://example.com]
volume: 40
settings_section: notifications
"""


def test_running_a_mode_sends_every_step_through_the_executor(tmp_path):
    _write(tmp_path, "teste", MODE)
    executor = _Executor(windows=["Discord.exe", "explorer.exe"])
    result = run(work_modes.run("teste", executor, tmp_path))
    names = [name for name, _ in executor.calls]
    assert names == ["pc_window_list", "pc_window_batch_close", "pc_app_launch",
                     "pc_web_open_url", "pc_volume_set", "pc_settings_open"]
    # slack had no window: skipped, and no confirmation was asked for it.
    statuses = {s["label"]: s["status"] for s in result["steps"]}
    assert statuses["Fechar slack"] == "skipped"
    assert result["done"] == 5 and result["skipped"] == 1 and result["ok"] is True
    assert "Pronto!" in result["message"]


def test_without_a_window_list_every_close_is_attempted(tmp_path):
    _write(tmp_path, "teste", MODE)
    executor = _Executor(windows=None)
    run(work_modes.run("teste", executor, tmp_path))
    closes = [args for name, args in executor.calls if name == "pc_window_batch_close"]
    assert closes == [{"app": "discord"}, {"app": "slack"}]


def test_a_declined_step_does_not_stop_the_rest_and_is_reported(tmp_path):
    _write(tmp_path, "teste", MODE)
    executor = _Executor(windows=["discord.exe", "slack.exe"], deny={"pc_window_batch_close"})
    result = run(work_modes.run("teste", executor, tmp_path))
    by_label = {s["label"]: s for s in result["steps"]}
    assert by_label["Fechar discord"]["status"] == "denied"
    assert by_label["Volume a 40%"]["status"] == "ok"
    assert result["done"] == 4


def test_an_unknown_mode_runs_nothing(tmp_path):
    executor = _Executor(windows=[])
    result = run(work_modes.run("../../etc/passwd", executor, tmp_path))
    assert result["ok"] is False and executor.calls == []


# ── what the model can reach ──────────────────────────────────────────────

def test_the_model_can_only_read_modes():
    load_all_plugins()
    advertised = {(t.get("function") or {}).get("name") for t in get_all_tools()}
    assert {"work_mode_list", "work_mode_plan"} <= advertised
    assert not {n for n in advertised if n.startswith("work_mode_") and n not in {"work_mode_list", "work_mode_plan"}}


def test_reading_a_plan_is_autonomous_and_low_risk():
    load_all_plugins()
    executor = ToolExecutor(permission_manager=PermissionManager())
    executor.register_plugin_tools()
    result = run(executor.execute_tool_async("work_mode_plan", {"mode": "foco"}))
    assert result["success"] is True
    steps = result["output"]["steps"]
    assert steps and all("tool" in s and "arguments" in s for s in steps)


def test_the_plan_tool_refuses_a_path():
    from plugins import work_modes as plugin
    assert plugin.work_mode_plan("../../Windows/win")["ok"] is False


def test_the_ui_runs_modes_off_the_bridge_thread():
    from core import main
    assert main.activate_work_mode("../x")["accepted"] is False
    source = (ROOT / "core" / "main.py").read_text(encoding="utf-8")
    assert "asyncio.run_coroutine_threadsafe(_run_work_mode(" in source
    assert '_notify_ui("on_work_mode_done", result)' in source
