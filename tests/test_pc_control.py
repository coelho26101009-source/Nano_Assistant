"""PC Control V1: the security contract, and the real Windows behaviour.

The premise of PC Control is that the model chooses a TOOL and TYPED ARGUMENTS,
and those arguments reach a Win32 call as values -- never as syntax, never as a
path of the model's choosing, and never without Policy and Permission having
said yes first. These tests exist to make that premise falsifiable.

They are behavioural. Real handlers run through the real ToolExecutor against
the real PermissionManager; the Windows layer is stubbed only where a test
needs to observe a call it must not make (process termination) or to force an
outcome the host will not produce on demand.
"""
from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest

from core import plugin_loader
from core.pc_control import applications, audio, files, screen, windows
from core.pc_control import results as pc_results
from core.pc_control.results import PCControlError
from core.permission_manager import PermissionManager
from core.tool_execution import ToolExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
PC_PACKAGE = REPO_ROOT / "core" / "pc_control"
PC_PLUGIN = REPO_ROOT / "plugins" / "pc_control.py"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

WINDOWS_ONLY = pytest.mark.skipif(
    not __import__("core.pc_control.winapi", fromlist=["winapi"]).IS_WINDOWS,
    reason="PC control targets Windows",
)

PC_TOOLS = (
    "pc_app_search", "pc_app_launch", "pc_app_switch", "pc_app_list_running",
    "pc_window_list", "pc_window_focus", "pc_window_minimize",
    "pc_window_maximize", "pc_window_restore", "pc_window_close",
    "pc_window_move", "pc_window_resize", "pc_window_center", "pc_window_snap",
    "pc_window_move_monitor", "pc_window_set_topmost",
    "pc_window_batch_state", "pc_window_batch_close",
    "pc_volume_get", "pc_volume_set", "pc_volume_change",
    "pc_volume_mute", "pc_volume_unmute", "pc_media_control",
    "pc_display_info", "pc_display_set_brightness", "pc_display_change_brightness",
    "pc_clipboard_read", "pc_clipboard_write", "pc_clipboard_clear",
    "pc_input_type_text", "pc_input_press_key", "pc_input_hotkey",
    "pc_pointer_scroll",
    "pc_folder_open", "pc_file_search", "pc_file_open",
    "pc_folder_create", "pc_file_create_text",
    "pc_file_copy", "pc_file_move", "pc_file_rename",
    "pc_file_recycle", "pc_folder_recycle",
    "pc_web_open_url", "pc_web_search", "pc_settings_open",
    "pc_system_info", "pc_network_status", "pc_storage_info",
    "pc_session_lock", "pc_power_sleep", "pc_power_restart",
    "pc_power_shutdown", "pc_session_logoff",
    "pc_screenshot_capture",
)


@pytest.fixture
def executor():
    """A real executor with a recording confirmation callback that says yes."""
    plugin_loader.load_all_plugins()
    asked: list[tuple[str, dict]] = []

    def confirm(capability, args):
        asked.append((capability, dict(args)))
        return True

    manager = PermissionManager(confirmation_callback=confirm)
    tool_executor = ToolExecutor(manager)
    tool_executor.register_plugin_tools()
    tool_executor.asked = asked          # type: ignore[attr-defined]
    return tool_executor


@pytest.fixture
def refusing_executor():
    plugin_loader.load_all_plugins()
    manager = PermissionManager(confirmation_callback=lambda *_: False)
    tool_executor = ToolExecutor(manager)
    tool_executor.register_plugin_tools()
    return tool_executor


# --------------------------------------------------------------------------
#  1-2. The pipeline is unavoidable
# --------------------------------------------------------------------------


def test_every_pc_tool_is_registered_in_the_executor(executor):
    for name in PC_TOOLS:
        assert name in executor.registry, f"{name} is not reachable through ToolExecutor"


def test_a_plugin_handler_cannot_be_invoked_outside_the_execution_authority():
    """The model's dispatch path must fail closed, not merely be discouraged."""
    plugin_loader.load_all_plugins()
    with pytest.raises(plugin_loader.UnauthorizedExecution):
        plugin_loader.execute_tool("pc_app_launch", {"name": "Calculadora"})
    with pytest.raises(plugin_loader.UnauthorizedExecution):
        plugin_loader.execute_tool("pc_window_close", {"query": "x"}, authority=object())


def test_every_pc_tool_carries_a_pc_capability(executor):
    for name in PC_TOOLS:
        capability = executor.registry[name]["capabilities"][0]
        assert capability.startswith("pc."), f"{name} resolved to {capability}"


def test_unknown_pc_operations_fail_closed(executor):
    result = executor.execute_tool("pc_window_teleport", {})
    assert result["success"] is False
    assert result["status"] == "unknown_tool"


# --------------------------------------------------------------------------
#  3. Permission is actually checked
# --------------------------------------------------------------------------


def test_sensitive_tools_require_confirmation_and_readonly_tools_do_not(executor):
    manager = executor.permission_manager
    for name, expected in (
        ("pc_system_info", False), ("pc_volume_get", False), ("pc_window_list", False),
        ("pc_app_search", False), ("pc_window_close", True), ("pc_screenshot_capture", True),
    ):
        capability = manager.resolve_tool_capability(name, {})
        evaluation = manager.policy_engine.evaluate(capability, target="x", scope="current_workspace")
        assert evaluation.requires_confirmation is expected, f"{name} -> {capability}"


def test_a_refused_confirmation_stops_the_action(refusing_executor, monkeypatch):
    killed: list[int] = []
    monkeypatch.setattr(windows, "close", lambda hwnd: killed.append(hwnd))
    result = refusing_executor.execute_tool("pc_window_close", {"window_id": 4242})
    assert result["success"] is False
    assert result["status"] == "permission_denied"
    assert killed == [], "the action ran despite the user refusing"


def test_emergency_stop_blocks_every_pc_action(executor):
    executor.permission_manager.set_emergency_stop(True)
    try:
        result = executor.execute_tool("pc_system_info", {})
        assert result["success"] is False
        assert result["status"] == "permission_denied"
    finally:
        executor.permission_manager.set_emergency_stop(False)


# --------------------------------------------------------------------------
#  4-5. Grants: exactly once, and bound to the target
# --------------------------------------------------------------------------


def test_allow_once_authorises_exactly_one_execution(executor, monkeypatch):
    """The second identical call must prompt again."""
    monkeypatch.setattr(windows, "resolve_window",
                        lambda **kw: {"window_id": 11, "title": "Bloco", "process": "notepad.exe",
                                      "visible": True, "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "close", lambda hwnd: {"closed": True, "title": "Bloco"})

    manager = executor.permission_manager
    request = manager.request_permission("pc.window.close", {"target": "window:11"})
    manager.resolve_permission(request, "ALLOW_ONCE")

    prompts_before = len(executor.asked)
    executor.execute_tool("pc_window_close", {"window_id": 11})
    assert len(executor.asked) == prompts_before, "the granted call should not have prompted"

    executor.execute_tool("pc_window_close", {"window_id": 11})
    assert len(executor.asked) == prompts_before + 1, "ALLOW_ONCE was reusable"


def test_a_grant_for_one_window_does_not_authorise_another(executor, monkeypatch):
    """Approving 'close Calculator' must never close Discord."""
    monkeypatch.setattr(windows, "resolve_window",
                        lambda window_id=None, **kw: {
                            "window_id": int(window_id), "title": f"w{window_id}",
                            "process": "x.exe", "visible": True, "state": "normal",
                            "focused": False})
    monkeypatch.setattr(windows, "close", lambda hwnd: {"closed": True, "title": "w"})

    manager = executor.permission_manager
    request = manager.request_permission("pc.window.close", {"target": "window:100"})
    manager.resolve_permission(request, "ALLOW_ONCE")

    prompts_before = len(executor.asked)
    executor.execute_tool("pc_window_close", {"window_id": 200})
    assert len(executor.asked) == prompts_before + 1, (
        "a grant for window 100 silently authorised window 200")


def test_a_launch_grant_binds_to_the_named_application(executor):
    """ALLOW_ONCE for Spotify must not become permission to launch anything."""
    from core.tool_execution import _pc_control_target

    assert _pc_control_target("pc_app_launch", {"name": "Spotify"}) == "app:Spotify"
    assert _pc_control_target("pc_app_launch", {"name": "powershell.exe"}) == "app:powershell.exe"
    assert (_pc_control_target("pc_app_launch", {"name": "Spotify"})
            != _pc_control_target("pc_app_launch", {"name": "powershell.exe"}))


def test_the_permission_target_reaches_the_confirmation_prompt(executor, monkeypatch):
    monkeypatch.setattr(windows, "resolve_window",
                        lambda **kw: {"window_id": 77, "title": "Calculadora",
                                      "process": "calc.exe", "visible": True,
                                      "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "close", lambda hwnd: {"closed": True, "title": "Calculadora"})
    executor.execute_tool("pc_window_close", {"window_id": 77})
    capability, args = executor.asked[-1]
    assert capability == "pc.window.close"
    assert args.get("target") == "window:77"


def test_non_pc_tools_keep_their_own_target_resolution():
    """The PC binding must not change how any existing tool resolves a target."""
    from core.tool_execution import _pc_control_target

    assert _pc_control_target("filesystem.write_file", {"path": "x"}) is None
    assert _pc_control_target("system_files", {"path": "x"}) is None
    assert _pc_control_target("browser.fetch_url", {"url": "http://x"}) is None


# --------------------------------------------------------------------------
#  6-8. No shell, no injection, no model-supplied paths
# --------------------------------------------------------------------------


def _python_sources() -> list[Path]:
    return sorted(PC_PACKAGE.glob("*.py")) + [PC_PLUGIN]


def test_pc_control_never_uses_a_shell_or_builds_a_command_line():
    """Parsed from the AST, so a comment mentioning shell=True cannot pass it.

    system.py is allowed one fixed-argv subprocess call for the GPU name; what
    is forbidden everywhere is shell=True and the shell-invoking os.* helpers.
    """
    for source in _python_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "shell":
                value = getattr(node.value, "value", None)
                assert value is not True, f"{source.name} passes shell=True"
            if isinstance(node, ast.Attribute) and node.attr in {"system", "popen"}:
                owner = getattr(node.value, "id", "")
                assert owner != "os", f"{source.name} calls os.{node.attr}"


def test_only_the_gpu_lookup_may_touch_subprocess():
    used = [s.name for s in _python_sources()
            if any(isinstance(n, (ast.Import, ast.ImportFrom))
                   and "subprocess" in ast.dump(n) for n in ast.walk(
                       ast.parse(s.read_text(encoding="utf-8"))))]
    assert used == ["system.py"], f"unexpected subprocess users: {used}"


def test_app_launch_refuses_a_path_supplied_by_the_model(executor):
    """The catalogue is the only source of launchable targets."""
    for attempt in (r"C:\Windows\System32\cmd.exe",
                    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                    "cmd.exe & calc.exe",
                    "../../../Windows/System32/cmd.exe"):
        result = executor.execute_tool("pc_app_launch", {"name": attempt})
        assert result["success"] is False, f"{attempt!r} launched"
        assert result["output"]["status"] in {"not_found", "ambiguous"}


def test_app_launch_refuses_an_app_id_that_is_not_in_the_catalogue(executor):
    result = executor.execute_tool("pc_app_launch",
                                   {"app_id": r"builtin:C:\Windows\System32\cmd.exe"})
    assert result["success"] is False
    assert result["output"]["status"] == "not_found"


def test_command_separators_do_not_survive_app_resolution():
    for attempt in ("Calculadora & calc.exe", "Calculadora; shutdown /s",
                    "Calculadora | powershell", "Calculadora`ncalc"):
        entry, _matches = applications.resolve(attempt)
        assert entry is None, f"{attempt!r} resolved to {entry}"


def test_app_scoring_never_matches_a_similar_sounding_word():
    """"apaga" must not reach Paint. No fuzzy matcher, by construction."""
    for spoken in ("apaga", "apagar", "paga", "pain", "calc ulad"):
        entry, matches = applications.resolve(spoken)
        assert entry is None and matches == [], f"{spoken!r} matched {matches}"


# --------------------------------------------------------------------------
#  9. file.open refuses executables
# --------------------------------------------------------------------------


@pytest.mark.parametrize("extension", [
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".scr", ".reg", ".lnk",
])
def test_file_open_refuses_every_executable_and_script_type(executor, tmp_path, extension):
    target = tmp_path / f"payload{extension}"
    target.write_text("echo hello", encoding="utf-8")
    result = executor.execute_tool("pc_file_open", {"path": str(target)})
    assert result["success"] is False
    assert result["output"]["status"] == "executable_refused"


def test_file_open_classifies_documents_as_openable(tmp_path):
    for name in ("relatorio.pdf", "notas.txt", "foto.png", "folha.xlsx"):
        assert files.classify_file(tmp_path / name) == "document"


def test_file_open_rejects_a_missing_file(executor, tmp_path):
    result = executor.execute_tool("pc_file_open", {"path": str(tmp_path / "nope.txt")})
    assert result["success"] is False
    assert result["output"]["status"] == "not_found"


# --------------------------------------------------------------------------
#  10. window.close is graceful, never a kill
# --------------------------------------------------------------------------


def test_window_close_uses_wm_close_and_never_terminates_a_process():
    source = (PC_PACKAGE / "windows.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    called = {node.func.attr for node in ast.walk(tree)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)}
    for forbidden in ("terminate", "kill", "TerminateProcess", "taskkill"):
        assert forbidden not in called, f"windows.py calls {forbidden}"
    assert "post_close" in called


def test_no_pc_module_can_terminate_a_process():
    for source in _python_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in {"terminate", "kill", "TerminateProcess"}, \
                    f"{source.name} calls {node.func.attr}"


def test_a_window_that_refuses_to_close_is_reported_as_refused(executor, monkeypatch):
    """An application showing "save your work?" must not be reported as closed."""
    monkeypatch.setattr(windows, "resolve_window",
                        lambda **kw: {"window_id": 9, "title": "Bloco de Notas",
                                      "process": "notepad.exe", "visible": True,
                                      "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "close",
                        lambda hwnd: {"closed": False, "title": "Bloco de Notas",
                                      "detail": "A aplicação não fechou."})
    result = executor.execute_tool("pc_window_close", {"window_id": 9})
    assert result["success"] is False
    assert result["output"]["status"] == "refused"


# --------------------------------------------------------------------------
#  11-12. Search is bounded; protected paths stay protected
# --------------------------------------------------------------------------


def test_file_search_is_bounded_in_results_and_time(executor):
    result = executor.execute_tool("pc_file_search", {"query": "a", "max_results": 5})
    payload = result["output"]
    assert len(payload.get("results", [])) <= 5
    assert payload["elapsed_seconds"] <= files.MAX_SEARCH_SECONDS + 2


def test_file_search_caps_an_absurd_max_results(executor):
    result = executor.execute_tool("pc_file_search", {"query": "a", "max_results": 10_000_000})
    assert len(result["output"].get("results", [])) <= pc_results.MAX_FILE_RESULTS


def test_file_search_defaults_to_user_folders_never_the_whole_drive():
    roots = files._search_roots(None)
    assert roots, "no default search roots"
    for root in roots:
        assert str(root).lower() != "c:\\"
        assert root.name in files.DEFAULT_SEARCH_FOLDERS


def test_file_search_does_not_follow_symlinks():
    source = (PC_PACKAGE / "files.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    walks = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Attribute) and n.func.attr == "walk"]
    assert walks, "file search no longer uses os.walk"
    for call in walks:
        followlinks = [k for k in call.keywords if k.arg == "followlinks"]
        assert followlinks and followlinks[0].value.value is False


@pytest.mark.parametrize("folder", ["C:\\Windows", "C:\\Windows\\System32", "C:\\Program Files"])
def test_protected_system_folders_cannot_be_opened(executor, folder):
    if not Path(folder).exists():
        pytest.skip(f"{folder} does not exist here")
    result = executor.execute_tool("pc_folder_open", {"path": folder})
    assert result["success"] is False
    assert result["output"]["status"] == "protected_path"


def test_nano_s_own_data_directory_is_protected():
    from core.app_paths import DATA_DIR

    assert files._protected(Path(DATA_DIR)) is True


def test_known_folders_resolve_through_the_environment_not_a_hardcoded_user():
    source = (PC_PACKAGE / "files.py").read_text(encoding="utf-8")
    assert "C:\\Users\\" not in source
    assert "os.path.expanduser" in source


# --------------------------------------------------------------------------
#  13. Screenshot capability
# --------------------------------------------------------------------------


def test_screenshot_requires_confirmation(executor):
    manager = executor.permission_manager
    capability = manager.resolve_tool_capability("pc_screenshot_capture", {})
    assert capability == "pc.screen.capture"
    evaluation = manager.policy_engine.evaluate(capability, target="screen", scope="system")
    assert evaluation.requires_confirmation is True


def test_a_refused_screenshot_captures_nothing(refusing_executor, monkeypatch):
    captured: list[int] = []
    monkeypatch.setattr(screen, "capture", lambda: captured.append(1))
    result = refusing_executor.execute_tool("pc_screenshot_capture", {})
    assert result["success"] is False
    assert captured == []


def test_a_screenshot_result_never_contains_image_data(executor):
    result = executor.execute_tool("pc_screenshot_capture", {})
    if not result["success"]:
        pytest.skip(f"capture unavailable here: {result['output'].get('status')}")
    payload = result["output"]
    assert "path" in payload and payload["width"] > 0
    for value in payload.values():
        assert not isinstance(value, (bytes, bytearray))
    # No base64 blob smuggled through a string field either.
    assert all(len(str(v)) < 1000 for v in payload.values())
    Path(payload["path"]).unlink(missing_ok=True)


def test_screenshot_cleanup_bounds_how_many_are_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(screen, "SCREENSHOT_DIR", tmp_path)
    for index in range(15):
        (tmp_path / f"screenshot-2026-{index:04d}.png").write_bytes(b"x")
        time.sleep(0.002)
    screen.cleanup(retention_seconds=10_000, max_retained=10)
    assert len(list(tmp_path.glob("screenshot-*.png"))) == 10


# --------------------------------------------------------------------------
#  14. Malformed arguments fail closed
# --------------------------------------------------------------------------


@pytest.mark.parametrize("level", [float("nan"), float("inf"), float("-inf"),
                                   -1, 101, 999999, "muito alto", None, [], {}])
def test_volume_rejects_every_malformed_level(executor, level):
    result = executor.execute_tool("pc_volume_set", {"level": level})
    assert result["success"] is False
    assert result["output"]["status"] == "invalid_input"


def test_volume_never_coerces_nan_into_a_real_level():
    with pytest.raises(PCControlError) as excinfo:
        audio.parse_level(float("nan"))
    assert excinfo.value.status == "invalid_input"


def test_volume_delta_is_bounded_but_the_result_is_clamped():
    assert audio.parse_delta(None) == audio.DEFAULT_STEP == 10
    assert audio.parse_delta(10) == 10
    assert audio.parse_delta(-10) == -10
    for out_of_range in (101, -101, float("nan")):
        with pytest.raises(PCControlError):
            audio.parse_delta(out_of_range)


@pytest.mark.parametrize("tool,args", [
    ("pc_app_launch", {}),
    ("pc_app_search", {"query": ""}),
    ("pc_window_focus", {}),
    ("pc_folder_open", {"path": ""}),
    ("pc_file_search", {"query": ""}),
    ("pc_file_open", {"path": ""}),
])
def test_missing_or_empty_arguments_fail_closed(executor, tool, args):
    result = executor.execute_tool(tool, args)
    assert result["success"] is False
    assert result["output"]["status"] in {"invalid_input", "not_found"}


def test_a_window_id_that_is_not_a_number_fails_closed(executor):
    result = executor.execute_tool("pc_window_minimize", {"window_id": "; shutdown"})
    assert result["success"] is False


# --------------------------------------------------------------------------
#  15. Results are bounded
# --------------------------------------------------------------------------


def test_window_list_is_capped(executor, monkeypatch):
    monkeypatch.setattr(windows, "list_windows",
                        lambda **kw: [{"window_id": i, "title": "x" * 400, "process": "p.exe",
                                       "visible": True, "state": "normal", "focused": False}
                                      for i in range(500)])
    result = executor.execute_tool("pc_window_list", {})
    assert len(result["output"]["windows"]) <= pc_results.MAX_FILE_RESULTS
    assert all(len(w["title"]) <= pc_results.MAX_STRING_CHARS
               for w in result["output"]["windows"])


def test_an_oversized_result_is_clamped_below_the_task_store_limit():
    import json

    payload = pc_results.ok("listed", "many",
                            windows=[{"title": "t" * 500, "path": "p" * 500}
                                     for _ in range(5000)])
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= pc_results.MAX_RESULT_BYTES
    assert payload.get("truncated") is True


def test_deep_nesting_is_flattened():
    deep: dict = {"level": 0}
    node = deep
    for index in range(1, 40):
        node["child"] = {"level": index}
        node = node["child"]
    payload = pc_results.ok("x", "y", tree=deep)
    depth, node = 0, payload["tree"]
    while isinstance(node, dict) and "child" in node:
        depth += 1
        node = node["child"]
    assert depth <= pc_results.MAX_DEPTH


# --------------------------------------------------------------------------
#  16-17. Real results, and voice ambiguity
# --------------------------------------------------------------------------


def test_a_failed_action_is_never_reported_to_the_model_as_success(executor):
    """The real-result contract: not_found must not arrive wrapped as success."""
    result = executor.execute_tool("pc_app_launch", {"name": "AplicacaoQueNaoExiste123"})
    assert result["success"] is False
    assert result["metadata"]["verified"] is False
    assert result["output"]["ok"] is False
    assert result["output"]["status"] == "not_found"


def test_every_failure_result_carries_an_error_code():
    payload = pc_results.fail("not_found", "nada")
    assert payload["ok"] is False and payload["error"] == "not_found"


def test_closing_by_a_loose_title_match_asks_instead_of_guessing(monkeypatch):
    """A destructive verb on a fuzzy target must stop and ask.

    Real transcripts turned "Procura o ficheiro" into "Procuro fechar" -- a
    loose match plus a consequential verb is exactly the pair that must never
    resolve silently.
    """
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": 1, "title": "Discord - geral", "process": "Discord.exe",
         "visible": True, "state": "normal", "focused": False},
    ])
    # A non-destructive verb may take the loose match.
    assert windows.resolve_window(query="discord -", allow_partial=True)["window_id"] == 1
    # The destructive verb may not.
    with pytest.raises(PCControlError) as excinfo:
        windows.resolve_window(query="discord -", allow_partial=False)
    assert excinfo.value.status == "ambiguous"
    assert excinfo.value.details["candidates"][0]["window_id"] == 1


def test_several_matching_windows_are_never_resolved_silently(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": 1, "title": "Relatório - Word", "process": "WINWORD.EXE",
         "visible": True, "state": "normal", "focused": False},
        {"window_id": 2, "title": "Relatório - Excel", "process": "EXCEL.EXE",
         "visible": True, "state": "normal", "focused": False},
    ])
    with pytest.raises(PCControlError) as excinfo:
        windows.resolve_window(query="relatório")
    assert excinfo.value.status == "ambiguous"
    assert len(excinfo.value.details["candidates"]) == 2


def test_an_ambiguous_application_is_never_launched(executor, monkeypatch):
    launched: list = []
    monkeypatch.setattr(applications, "launch", lambda entry: launched.append(entry))
    monkeypatch.setattr(applications, "resolve", lambda q: (None, [
        (applications.AppEntry("App One", "a.lnk", "start_menu_user"), 1.0),
        (applications.AppEntry("App Two", "b.lnk", "start_menu_user"), 1.0),
    ]))
    result = executor.execute_tool("pc_app_launch", {"name": "App"})
    assert result["success"] is False
    assert result["output"]["status"] == "ambiguous"
    assert len(result["output"]["candidates"]) == 2
    assert launched == [], "an ambiguous name was launched anyway"


# --------------------------------------------------------------------------
#  Non-goals stay absent
# --------------------------------------------------------------------------


def test_no_arbitrary_execution_tool_is_exposed_to_the_model():
    """Part of the V1 bar: the model has no general command line anywhere."""
    plugin_loader.load_all_plugins()
    names = {t["function"]["name"] for t in plugin_loader.get_all_tools()}
    for forbidden in ("system_run_powershell", "shell_execute", "powershell_execute",
                      "cmd_execute", "terminal_run", "system_wifi"):
        assert forbidden not in names, f"{forbidden} is exposed to the model"


def test_pc_control_ships_no_permanent_delete_and_no_process_control():
    """The property that survives V2, asserted from behaviour and the AST.

    V1 could assert this by name -- there was simply no file-mutation tool at
    all. V2 ships move, rename, copy and recycle, so a name list is no longer
    the contract. The contract is that REMOVAL MEANS THE RECYCLE BIN and that
    no process can be terminated: no `unlink`, no `rmdir`, no `rmtree`, no
    `terminate`, anywhere in the package.
    """
    forbidden_calls = {"unlink", "rmdir", "rmtree", "remove", "removedirs",
                       "terminate", "kill", "TerminateProcess"}
    # ONE exemption, and it is narrow enough to state exactly: screen.cleanup
    # deletes NANO'S OWN expired captures out of Nano's own data directory. It
    # is not a tool, the model cannot reach it, and it is asserted below to
    # look nowhere else. Every other occurrence anywhere in the package is a
    # failure.
    allowed = {("screen.py", "cleanup", "unlink")}
    for source in _python_sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                called = getattr(node.func, "attr", None) or getattr(node.func, "id", None)
                if called not in forbidden_calls:
                    continue
                assert (source.name, function.name, called) in allowed, (
                    f"{source.name}.{function.name} calls {called}(), which can "
                    f"destroy the user's data permanently or stop a process"
                )

    # The exemption, proved rather than assumed: cleanup only ever enumerates
    # Nano's own screenshot directory, so its unlink cannot reach a user file.
    cleanup_source = ast.parse((PC_PACKAGE / "screen.py").read_text(encoding="utf-8"))
    cleanup = next(node for node in ast.walk(cleanup_source)
                   if isinstance(node, ast.FunctionDef) and node.name == "cleanup")
    globs = [node for node in ast.walk(cleanup)
             if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "glob"]
    assert globs, "screen.cleanup no longer enumerates by glob; re-check its scope"
    for call in globs:
        assert getattr(call.func.value, "id", None) == "SCREENSHOT_DIR", (
            "screen.cleanup enumerates something other than SCREENSHOT_DIR"
        )
        assert call.args and getattr(call.args[0], "value", "").startswith("screenshot-"), (
            "screen.cleanup no longer restricts itself to Nano's own captures"
        )

    plugin_loader.load_all_plugins()
    names = {t["function"]["name"] for t in plugin_loader.get_all_tools()
             if t["function"]["name"].startswith("pc_")}
    for forbidden in ("pc_file_delete", "pc_folder_delete", "pc_file_erase",
                      "pc_process_kill", "pc_process_list", "pc_registry_write",
                      "pc_service_control", "pc_install"):
        assert forbidden not in names


def test_the_pc_tool_surface_is_exactly_the_declared_set():
    """Every PC tool is declared here, and every name here is a real tool.

    Pinning the exact SET rather than a count is what makes this a guard: a
    tool added to plugins/pc_control.py without a line in PC_TOOLS fails, and
    so does a name here that no longer exists.
    """
    plugin_loader.load_all_plugins()
    names = {t["function"]["name"] for t in plugin_loader.get_all_tools()
             if t["function"]["name"].startswith("pc_")}
    assert names == set(PC_TOOLS)
    assert len(PC_TOOLS) == len(set(PC_TOOLS)), "PC_TOOLS lists a name twice"


# --------------------------------------------------------------------------
#  Real Windows behaviour
# --------------------------------------------------------------------------


@WINDOWS_ONLY
def test_app_search_finds_the_calculator_on_this_machine(executor):
    result = executor.execute_tool("pc_app_search", {"query": "Calculadora"})
    assert result["success"] is True
    assert result["output"]["candidates"][0]["name"] == "Calculadora"


@WINDOWS_ONLY
def test_volume_can_be_read_and_restored_exactly(executor):
    """Mutates real audio, so the original level is restored in finally.

    Skipped, not faked, when there is genuinely no default audio playback
    device to read -- a hosted CI runner rather than a real machine. The
    check is the real capability probe itself (the same call the tool makes),
    not an "if CI" shortcut, so this still runs for real on any host that
    actually has audio, hosted or not.
    """
    before = executor.execute_tool("pc_volume_get", {})
    if not before["success"] and str(before.get("error", "")).startswith("audio_unavailable"):
        pytest.skip("no default audio playback device is available on this host")
    assert before["success"] is True
    original_level = before["output"]["level"]
    original_muted = before["output"]["muted"]
    try:
        result = executor.execute_tool("pc_volume_set", {"level": 30})
        assert result["success"] is True
        assert abs(result["output"]["level"] - 30) <= 1

        changed = executor.execute_tool("pc_volume_change", {"delta": 10})
        assert abs(changed["output"]["level"] - 40) <= 2

        # The clamp is not an error: +100 from 40 lands on 100.
        clamped = executor.execute_tool("pc_volume_change", {"delta": 100})
        assert clamped["output"]["level"] == 100
    finally:
        executor.execute_tool("pc_volume_set", {"level": original_level})
        executor.execute_tool("pc_volume_mute" if original_muted else "pc_volume_unmute", {})

    restored = executor.execute_tool("pc_volume_get", {})
    assert abs(restored["output"]["level"] - original_level) <= 1
    assert restored["output"]["muted"] is original_muted


@WINDOWS_ONLY
def test_system_info_is_real_and_carries_no_identifiers(executor):
    result = executor.execute_tool("pc_system_info", {})
    assert result["success"] is True
    payload = result["output"]
    assert payload["ram_total_gb"] > 0
    assert 0 <= payload["ram_percent"] <= 100
    assert payload["cpu_cores_logical"] >= 1
    for forbidden in ("mac_address", "serial", "serial_number", "product_key",
                      "licence", "license", "username", "user", "environment"):
        assert forbidden not in payload


@WINDOWS_ONLY
def test_window_list_returns_real_bounded_windows(executor):
    result = executor.execute_tool("pc_window_list", {})
    assert result["success"] is True
    for window in result["output"]["windows"]:
        assert isinstance(window["window_id"], int)
        assert window["state"] in {"normal", "minimized", "maximized", "unknown"}
        assert len(window["title"]) <= pc_results.MAX_STRING_CHARS


@WINDOWS_ONLY
def test_downloads_folder_opens_by_known_name_and_reports_the_real_path(executor):
    """Regression: a known folder NAME must not be resolved against the repo.

    ToolExecutor rewrites every argument called `path` relative to the
    workspace root, which turned "Downloads" into <repo>/Downloads and failed.
    Known names therefore travel as `folder`; this test is what caught it.
    """
    downloads = files.known_folder("downloads")
    if downloads is None:
        pytest.skip("no Downloads folder on this machine")
    result = executor.execute_tool("pc_folder_open", {"folder": "Downloads"})
    assert result["success"] is True
    assert Path(result["output"]["path"]) == downloads


@WINDOWS_ONLY
def test_an_explicit_folder_path_still_opens_and_keeps_central_validation(executor):
    downloads = files.known_folder("downloads")
    if downloads is None:
        pytest.skip("no Downloads folder on this machine")
    result = executor.execute_tool("pc_folder_open", {"path": str(downloads)})
    assert result["success"] is True
    assert Path(result["output"]["path"]) == downloads


def test_a_known_folder_name_is_never_resolved_against_the_repository():
    """The bug itself, asserted directly on the resolver."""
    from core.execution_scope import resolve_target

    rewritten = resolve_target("Downloads").path
    assert not rewritten.exists(), "test premise broken: <repo>/Downloads now exists"
    resolved = files.known_folder("downloads")
    if resolved is not None:
        assert resolved != rewritten


@WINDOWS_ONLY
def test_the_full_calculator_lifecycle_through_the_executor(executor):
    """launch -> list -> minimize -> restore -> close, all result-backed."""
    launched = executor.execute_tool("pc_app_launch", {"name": "Calculadora"})
    assert launched["success"] is True
    assert launched["output"]["status"] in {"launched", "already_running"}

    window = None
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline and window is None:
        time.sleep(0.5)
        listed = executor.execute_tool("pc_window_list", {})
        window = next((w for w in listed["output"]["windows"]
                       if "calculadora" in w["title"].lower()
                       or "calculator" in w["title"].lower()), None)
    if window is None:
        pytest.skip("the Calculator window did not appear on this host")

    window_id = window["window_id"]
    # The state BEFORE minimising is what restore has to return the window to.
    # Asserting a fixed "normal" here assumed the Calculator was never left
    # maximised by an earlier session, a user, or another test -- and when it
    # was, this test failed while the code was correct.
    state_before = window["state"]
    if state_before == "minimized":
        assert executor.execute_tool(
            "pc_window_restore", {"window_id": window_id})["success"] is True
        state_before = next(
            w["state"] for w in executor.execute_tool("pc_window_list", {})["output"]["windows"]
            if w["window_id"] == window_id)
    try:
        minimized = executor.execute_tool("pc_window_minimize", {"window_id": window_id})
        assert minimized["success"] is True
        assert minimized["output"]["status"] == "minimized"

        restored = executor.execute_tool("pc_window_restore", {"window_id": window_id})
        if state_before == "maximized":
            # Windows restores a maximised window to maximised. `restore`
            # reports state_unchanged because it asked for "normal" and got
            # "maximized" -- an honest result, and the window IS back.
            state_now = next(
                w["state"] for w in executor.execute_tool("pc_window_list", {})["output"]["windows"]
                if w["window_id"] == window_id)
            assert state_now == "maximized", state_now
        else:
            assert restored["success"] is True
            assert restored["output"]["status"] == "normal"
    finally:
        closed = executor.execute_tool("pc_window_close", {"window_id": window_id})
        assert closed["success"] is True
        assert closed["output"]["status"] == "closed"

    time.sleep(1.0)
    remaining = executor.execute_tool("pc_window_list", {})
    assert not any(w["window_id"] == window_id
                   for w in remaining["output"]["windows"]), "the window survived close"
