"""PC Control V2: the security contract, and the real Windows behaviour.

V2's premise is the same as V1's, applied to a much larger surface: BROAD
CAPABILITY COVERAGE THROUGH NARROW TOOLS. Fifty-six small tools, each with its
own schema, its own risk, its own confirmation rule and its own target -- and
no generic executor anywhere, because a generic executor would make every
refusal beside it decorative.

These tests exist to make that premise falsifiable. They are behavioural: real
handlers run through the real ToolExecutor against the real PermissionManager.
The Windows layer is stubbed only where a test must observe a call it must not
make (process termination, shutdown) or force an outcome the host will not
produce on demand.

Nothing here shuts the machine down, types into a window the test did not
create, or touches a personal file. Everything that mutates is restored.
"""
from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from core import plugin_loader
from core.pc_control import (
    applications,
    clipboard,
    display,
    fileops,
    files,
    geometry,
    keyboard,
    power,
    screen,
    settings,
    web,
    winapi,
    windows,
)
from core.pc_control.results import PCControlError
from core.permission_manager import PermissionManager
from core.policy_engine import AuthorityDecision, PolicyEngine, RiskLevel
from core.tool_execution import ToolExecutor

REPO_ROOT = Path(__file__).resolve().parent.parent
PC_PACKAGE = REPO_ROOT / "core" / "pc_control"
PC_PLUGIN = REPO_ROOT / "plugins" / "pc_control.py"

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")

WINDOWS_ONLY = pytest.mark.skipif(not winapi.IS_WINDOWS,
                                  reason="PC control targets Windows")


def _sources() -> list[Path]:
    return sorted(PC_PACKAGE.glob("*.py")) + [PC_PLUGIN]


@pytest.fixture
def executor():
    """A real executor whose confirmation callback records and says yes."""
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


@pytest.fixture
def sandbox(tmp_path):
    """A directory this test owns, outside the workspace and outside the profile."""
    folder = tmp_path / "nano-v2"
    folder.mkdir()
    return folder


def _tool_schemas() -> dict[str, dict]:
    plugin_loader.load_all_plugins()
    return {t["function"]["name"]: t["function"]
            for t in plugin_loader.get_all_tools()
            if t["function"]["name"].startswith("pc_")}


# ==========================================================================
#  1-6, 31. There is no generic executor, and no way to build one
# ==========================================================================


def test_no_tool_offers_arbitrary_execution_of_anything():
    """Audit items 1-5: no shell, no cmd, no PowerShell, no process runner."""
    plugin_loader.load_all_plugins()
    names = {t["function"]["name"] for t in plugin_loader.get_all_tools()}
    for forbidden in (
        "shell_execute", "shell.execute", "command_execute", "powershell_execute",
        "system_run_powershell", "cmd_execute", "terminal_run", "run_command",
        "arbitrary_process_run", "arbitrary_script_run", "process_start",
        "pc_shell", "pc_run", "pc_execute", "pc_process_kill", "pc_command",
        "computer_action", "keyboard_send", "pc_keyboard_raw_sequence",
        "system_files", "system_brightness", "system_bluetooth", "system_wifi",
    ):
        assert forbidden not in names, f"{forbidden} is exposed to the model"


def test_no_pc_tool_takes_a_free_form_execution_argument():
    """The escape hatch a large tool surface invites, closed by schema.

    A tool called `pc_window_snap` that happens to accept `{"command": "..."}`
    would be a shell with a misleading name. Every PC tool's parameter names
    are checked against the vocabulary of arbitrary execution.
    """
    forbidden = {"command", "cmd", "script", "code", "shell", "powershell",
                 "exec", "execute", "args", "argv", "payload", "expression",
                 "sequence", "scancode", "scan_codes", "keys", "macro", "uri",
                 "executable"}
    for name, function in _tool_schemas().items():
        properties = set((function.get("parameters") or {}).get("properties") or {})
        overlap = properties & forbidden
        assert not overlap, f"{name} accepts {sorted(overlap)}"


def test_no_pc_module_imports_subprocess_or_reaches_a_shell():
    """Audit items 1-3, asserted from the AST across the WHOLE V2 package.

    system.py keeps its one fixed-argv nvidia-smi call for the GPU name; that
    is the only subprocess user, and there is no shell=True and no os.system /
    os.popen anywhere, in V1 modules or V2 ones.
    """
    subprocess_users = []
    for source in _sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)) and "subprocess" in ast.dump(node):
                subprocess_users.append(source.name)
            if isinstance(node, ast.keyword) and node.arg == "shell":
                assert getattr(node.value, "value", None) is not True, \
                    f"{source.name} passes shell=True"
            if isinstance(node, ast.Attribute) and node.attr in {"system", "popen"}:
                assert getattr(node.value, "id", "") != "os", \
                    f"{source.name} calls os.{node.attr}"
    assert sorted(set(subprocess_users)) == ["system.py"], subprocess_users


def test_a_model_supplied_executable_path_never_launches(executor):
    """Audit item 6. The catalogue is the only source of launchable targets."""
    for attempt in (r"C:\Windows\System32\cmd.exe",
                    r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                    "cmd.exe & calc.exe",
                    "../../../Windows/System32/cmd.exe",
                    r"\\evil-share\payload.exe"):
        for tool in ("pc_app_launch", "pc_app_switch"):
            result = executor.execute_tool(tool, {"name": attempt})
            assert result["success"] is False, f"{tool}({attempt!r}) succeeded"
            assert result["output"]["status"] in {"not_found", "ambiguous"}


def test_the_application_catalogue_never_offers_a_command_interpreter():
    """Discovery widened in V2; the shells it discovered are filtered back out.

    App Paths registers `powershell.exe` and the Store aliases include `wt.exe`.
    Surfacing either as an application Nano can launch would be the beginning of
    an argument that a command line exists.
    """
    for name in ("cmd", "powershell", "pwsh", "wt", "conhost", "wscript",
                 "cscript", "mshta", "regedit"):
        assert applications._looks_like_an_application(name) is False, name


def test_an_unknown_pc_operation_fails_closed(executor):
    """Audit item 31.

    Both refusals fail closed; they differ in what they tell the caller.
    A name Nano simply does not have is `unknown_tool`. A name that stands for
    a capability Nano has DECLARED it will never have is
    `unsupported_capability`, so the answer can say why instead of implying a
    typo -- or, worse, implying an approval would fix it.
    """
    for name in ("pc_window_teleport", "pc_frobnicate"):
        result = executor.execute_tool(name, {})
        assert result["success"] is False
        assert result["status"] == "unknown_tool"

    for name in ("pc_run_anything", "pc_shell_execute", "pc_run_command"):
        result = executor.execute_tool(name, {})
        assert result["success"] is False
        assert result["status"] == "unsupported_capability"


def test_pc_control_cannot_reach_the_secret_store():
    """Nano never types a credential it looked up, because it cannot look one up.

    Structural, not behavioural: no module in the package imports the secret
    store, and no tool name suggests one.
    """
    for source in _sources():
        text = source.read_text(encoding="utf-8")
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert "secret_store" not in ast.dump(node), \
                    f"{source.name} imports the secret store"
    for name in _tool_schemas():
        assert "secret" not in name and "credential" not in name and "password" not in name


# ==========================================================================
#  7. URL schemes
# ==========================================================================


@pytest.mark.parametrize("url", [
    "file:///C:/Windows/win.ini",
    "javascript:alert(document.cookie)",
    "data:text/html;base64,PHNjcmlwdD4=",
    "vbscript:msgbox(1)",
    "shell:startup",
    "ms-settings:privacy",
    "ms-appinstaller://x",
    "search-ms:query=password",
    "view-source:https://example.com",
    "ftp://example.com/x",
    "smb://server/share",
])
def test_dangerous_url_schemes_are_refused(url):
    with pytest.raises(PCControlError) as excinfo:
        web.normalise_url(url)
    assert excinfo.value.status == "blocked"


def test_a_url_carrying_credentials_is_refused():
    with pytest.raises(PCControlError) as excinfo:
        web.normalise_url("https://user:hunter2@example.com/")
    assert excinfo.value.status == "blocked"


def test_a_bare_host_is_upgraded_to_https_never_http():
    assert web.normalise_url("github.com") == "https://github.com"
    assert web.normalise_url("www.example.com/path?q=1").startswith("https://")


def test_a_search_query_is_encoded_and_cannot_alter_the_url():
    url, engine, query = web.build_search_url('monitores 240 Hz & q=x#frag/../etc', "duckduckgo")
    assert engine == "duckduckgo"
    assert url.startswith("https://duckduckgo.com/?q=")
    # Everything after the single q= is percent-encoded: no second parameter, no
    # fragment, no path traversal survives into the address.
    tail = url.split("?q=", 1)[1]
    for character in ("&", "#", "/", " "):
        assert character not in tail, f"{character!r} survived encoding"


def test_a_search_engine_is_an_enum_not_a_template():
    with pytest.raises(PCControlError) as excinfo:
        web.build_search_url("x", "https://evil.example/?q={query}")
    assert excinfo.value.status == "invalid_input"


def test_a_url_is_bounded_and_rejects_control_characters():
    with pytest.raises(PCControlError):
        web.normalise_url("https://example.com/" + "a" * web.MAX_URL_LENGTH)
    with pytest.raises(PCControlError):
        web.normalise_url("https://example.com/\r\nHost: evil")


# ==========================================================================
#  8. Windows Settings is an enum allow-list
# ==========================================================================


def test_windows_settings_only_accepts_allowlisted_sections():
    for attempt in ("ms-settings:privacy", "ms-settings:signinoptions",
                    "windowsdefender:", "../display", "recovery",
                    "ms-settings:display", "signinoptions", "backup"):
        with pytest.raises(PCControlError) as excinfo:
            settings.resolve_section(attempt)
        assert excinfo.value.status == "invalid_input", attempt


def test_every_settings_uri_is_a_constant_not_built_from_input():
    """The URI is looked up, never composed, so nothing can be injected."""
    for section, (uri, label) in settings.SECTIONS.items():
        assert uri.startswith("ms-settings:"), section
        assert "{" not in uri and "%" not in uri, section
        assert label
    resolved = settings.resolve_section("Sound")
    assert resolved == ("sound", "ms-settings:sound", "Som")


def test_the_settings_tool_schema_pins_the_enum():
    schema = _tool_schemas()["pc_settings_open"]["parameters"]["properties"]["section"]
    assert set(schema["enum"]) == set(settings.SECTIONS)


# ==========================================================================
#  9-10. Keyboard is an allow-list; scan codes do not exist
# ==========================================================================


@pytest.mark.parametrize("attempt", [
    "f4", "win+r", "ctrl+alt+delete", "ctrl+shift+escape", "alt+f4",
    "0x41", "VK_RETURN", "", None, 13, "enter enter",
])
def test_a_key_outside_the_allowlist_is_refused(attempt):
    with pytest.raises(PCControlError) as excinfo:
        keyboard.validate_key(attempt)
    assert excinfo.value.status == "invalid_input"


@pytest.mark.parametrize("attempt", [
    "ctrl+r", "win+r", "alt+f4", "ctrl+shift+n", "ctrl+alt+del",
    "Ctrl+C;Ctrl+V", "", None, 42,
])
def test_a_hotkey_outside_the_allowlist_is_refused(attempt):
    with pytest.raises(PCControlError) as excinfo:
        keyboard.validate_hotkey(attempt)
    assert excinfo.value.status == "invalid_input"


def test_there_is_no_scan_code_or_key_sequence_entry_point():
    """Audit item 10, asserted structurally rather than by trying inputs.

    Nothing in the keyboard layer accepts a caller-chosen scan code: text goes
    in as Unicode characters, and a chord's virtual-key code comes from a table
    in this repository. `press_chord` is the only function taking a raw code and
    it is not reachable from a tool argument -- every caller resolves the code
    through `validate_key` / `validate_hotkey` / `validate_media_action` first.
    """
    tree = ast.parse((PC_PACKAGE / "keyboard.py").read_text(encoding="utf-8"))
    public = {node.name for node in ast.walk(tree)
              if isinstance(node, ast.FunctionDef) and not node.name.startswith("_")}
    assert "press_chord" not in public, "a raw-chord entry point is exported"

    plugin_source = PC_PLUGIN.read_text(encoding="utf-8")
    plugin_tree = ast.parse(plugin_source)
    for node in ast.walk(plugin_tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "press_chord":
            raise AssertionError("the tool layer calls press_chord directly")


def test_every_allowlisted_key_and_hotkey_is_a_real_windows_code():
    for name, code in keyboard.KEY_ALLOWLIST.items():
        assert isinstance(code, int) and 0 < code < 0xFF, name
    for name, (code, modifiers, requires_target, label) in keyboard.HOTKEY_ALLOWLIST.items():
        assert isinstance(code, int) and 0 < code < 0xFF, name
        assert all(isinstance(m, int) for m in modifiers), name
        assert isinstance(requires_target, bool) and label


def test_typed_text_is_bounded_and_refuses_control_characters():
    assert keyboard.validate_text("olá\nmundo\t!") == "olá\nmundo\t!"
    for attempt in ("x" * (keyboard.MAX_TEXT_CHARS + 1), "a\x00b", "a\x1bb", "", 42, None):
        with pytest.raises(PCControlError):
            keyboard.validate_text(attempt)


def test_the_destructive_key_list_agrees_across_the_two_layers():
    """PermissionManager duplicates this set rather than importing the PC layer.

    The duplication is deliberate -- the security core must not depend on the
    Windows layer -- so this is what stops the two copies drifting apart and
    silently downgrading Delete to the navigation-key risk level.
    """
    from core.permission_manager import _DESTRUCTIVE_KEYS

    assert _DESTRUCTIVE_KEYS == keyboard.DESTRUCTIVE_KEYS
    assert keyboard.DESTRUCTIVE_KEYS <= set(keyboard.KEY_ALLOWLIST)


def test_a_destructive_key_resolves_to_a_confirmed_capability():
    manager = PermissionManager()
    assert manager.resolve_tool_capability(
        "pc_input_press_key", {"key": "delete"}) == "pc.input.key_destructive"
    assert manager.resolve_tool_capability(
        "pc_input_press_key", {"key": "Backspace"}) == "pc.input.key_destructive"
    assert manager.resolve_tool_capability(
        "pc_input_press_key", {"key": "right"}) == "pc.input.key"
    assert manager.is_approval_gated("pc.input.key_destructive") is True
    assert manager.is_approval_gated("pc.input.key") is False


# ==========================================================================
#  The composition that would be a shell: typing into a console
# ==========================================================================


@pytest.mark.parametrize("process", [
    "cmd.exe", "powershell.exe", "pwsh.exe", "WindowsTerminal.exe",
    "conhost.exe", "bash.exe", "python.exe", "wt.exe",
])
def test_a_console_window_is_recognised_as_a_terminal(process):
    assert windows.is_terminal_window(
        {"window_id": 0, "process": process, "title": "x"}) is True


def test_typing_into_a_terminal_is_refused_not_gated(executor, monkeypatch):
    """LAUNCHING a terminal is allowed; TYPING into one is not.

    Without this, two individually-reasonable actions compose into arbitrary
    command execution with nothing between them but the user reading a dialog.
    """
    monkeypatch.setattr(windows, "resolve_window", lambda **kw: {
        "window_id": 4242, "title": "Windows PowerShell", "process": "powershell.exe",
        "visible": True, "state": "normal", "focused": False})
    typed: list = []
    monkeypatch.setattr(keyboard, "type_text", lambda text: typed.append(text))

    for tool, args in (("pc_input_type_text", {"window_id": 4242, "text": "Remove-Item -Recurse C:\\"}),
                       ("pc_input_press_key", {"window_id": 4242, "key": "enter"}),
                       ("pc_input_hotkey", {"window_id": 4242, "hotkey": "paste"}),
                       ("pc_pointer_scroll", {"window_id": 4242, "direction": "down"})):
        result = executor.execute_tool(tool, args)
        assert result["success"] is False, tool
        assert result["output"]["status"] == "blocked", tool
    assert typed == [], "text was sent to a console window"


def test_typing_into_nanos_own_window_is_refused(executor, monkeypatch):
    monkeypatch.setattr(windows, "resolve_window", lambda **kw: {
        "window_id": 77, "title": "Nano", "process": "electron.exe",
        "visible": True, "state": "normal", "focused": True})
    monkeypatch.setattr(windows, "is_nano_window", lambda window: True)
    typed: list = []
    monkeypatch.setattr(keyboard, "type_text", lambda text: typed.append(text))

    result = executor.execute_tool("pc_input_type_text", {"window_id": 77, "text": "olá"})
    assert result["success"] is False
    assert result["output"]["status"] == "blocked"
    assert typed == []


def test_nothing_is_typed_when_windows_refuses_the_focus_change(executor, monkeypatch):
    """The dangerous case: an unverified target must stop the action, not proceed."""
    monkeypatch.setattr(windows, "resolve_window", lambda **kw: {
        "window_id": 5, "title": "Bloco de Notas", "process": "notepad.exe",
        "visible": True, "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "is_terminal_window", lambda window: False)
    monkeypatch.setattr(windows, "is_nano_window", lambda window: False)
    monkeypatch.setattr(keyboard, "focus_and_verify", lambda hwnd: {
        "focused": False, "title": "Bloco de Notas", "detail": "recusado"})
    typed: list = []
    monkeypatch.setattr(keyboard, "type_text", lambda text: typed.append(text))

    result = executor.execute_tool("pc_input_type_text", {"window_id": 5, "text": "olá"})
    assert result["success"] is False
    assert result["output"]["status"] == "focus_refused"
    assert typed == [], "text was sent at an unverified target"


def test_an_input_target_must_be_named_and_matched_strictly(executor):
    """A loose title match is not good enough to send keystrokes at."""
    result = executor.execute_tool("pc_input_type_text", {"text": "olá"})
    assert result["success"] is False
    assert result["output"]["status"] in {"invalid_input", "not_found", "ambiguous"}


# ==========================================================================
#  11-14, 33. Files: protected, bounded, inert, and never deleted
# ==========================================================================


@pytest.mark.parametrize("location", [
    r"C:\Windows\System32\drivers\etc\hosts",
    r"C:\Program Files\x.txt",
    r"C:\ProgramData\x.txt",
    "~/.ssh/id_rsa",
    "~/.aws/credentials",
    "~/AppData/Local/Google/Chrome/User Data/Default/Login Data",
    "~/AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup/x.txt",
    "C:\\",
])
def test_protected_locations_are_refused_for_every_file_mutation(executor, location):
    """Audit item 11, across the whole V2 file surface."""
    target = str(Path(location).expanduser())
    for tool, args in (
        ("pc_file_recycle", {"path": target}),
        ("pc_folder_recycle", {"path": target}),
        ("pc_file_rename", {"source": target, "new_name": "x.txt"}),
        ("pc_file_move", {"source": target, "destination": str(Path.home() / "x.txt")}),
        ("pc_file_copy", {"source": target, "destination": str(Path.home() / "x.txt")}),
        ("pc_folder_create", {"path": target, "name": "nano"}),
    ):
        result = executor.execute_tool(tool, args)
        assert result["success"] is False, f"{tool} accepted {target}"
        status = result.get("status")
        output_status = (result.get("output") or {}).get("status")
        assert "permission_denied" in {status} or output_status in {
            "protected_path", "not_found", "invalid_input", "blocked"
        }, f"{tool}({target}) -> {status}/{output_status}"


def test_nanos_own_directories_are_protected():
    from core.app_paths import DATA_DIR, ROOT

    assert files.is_protected(Path(ROOT) / "core" / "main.py") is True
    assert files.is_protected(Path(DATA_DIR) / "permission_policies.json") is True


@pytest.mark.parametrize("attempt", [
    "../escape.txt", "..\\escape.txt", "sub/dir.txt", "sub\\dir.txt",
    "C:\\absolute.txt", "..", ".", "con", "nul.txt", "aux",
    "trailing.", "report.txt. ", 'quote".txt', "pipe|.txt", "star*.txt",
    "colon:.txt", "question?.txt", "less<.txt", "more>.txt", "",
])
def test_a_created_name_can_never_become_a_path(attempt):
    """Audit item 12. `safe_name` is where a NAME is stopped from being a PATH."""
    with pytest.raises(PCControlError) as excinfo:
        fileops.safe_name(attempt)
    assert excinfo.value.status == "invalid_input", attempt


def test_a_trailing_dot_is_refused_because_windows_would_erase_it():
    """The specific reason the trailing-dot rule exists, stated as a test.

    Windows drops a trailing dot, so "report.txt." IS "report.txt". Accepting
    the first spelling would let a "new" file land silently on an existing one,
    straight past the conflict check. Surrounding whitespace is a different
    thing and is simply trimmed, the way every file dialog trims it -- so the
    name that gets created is still exactly the one the confirmation showed.
    """
    with pytest.raises(PCControlError):
        fileops.safe_name("report.txt.")
    assert fileops.safe_name("  relatorio.txt  ") == "relatorio.txt"


@pytest.mark.parametrize("extension", [
    ".bat", ".cmd", ".ps1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".exe",
    ".com", ".msi", ".scr", ".reg", ".hta", ".lnk", ".html", ".svg", ".py",
])
def test_creating_an_executable_or_scriptable_file_is_refused(executor, sandbox, extension):
    """Audit item 13. File creation must never be a shell bypass."""
    result = executor.execute_tool("pc_file_create_text", {
        "path": str(sandbox), "name": f"payload{extension}", "content": "echo hi"})
    assert result["success"] is False, extension
    assert result["output"]["status"] == "blocked", extension
    assert not list(sandbox.iterdir()), "a file was created anyway"


def test_copying_or_renaming_into_an_executable_extension_is_refused(executor, sandbox):
    source = sandbox / "nota.txt"
    source.write_text("olá", encoding="utf-8")
    for tool, args in (
        ("pc_file_copy", {"source": str(source), "destination": str(sandbox / "x.bat")}),
        ("pc_file_move", {"source": str(source), "destination": str(sandbox / "x.ps1")}),
        ("pc_file_rename", {"source": str(source), "new_name": "x.exe"}),
    ):
        result = executor.execute_tool(tool, args)
        assert result["success"] is False, tool
        assert result["output"]["status"] == "blocked", tool
    assert source.exists(), "the source was disturbed by a refused operation"


def test_a_text_file_may_only_use_an_inert_extension():
    for good in ("nota.txt", "dados.csv", "notas.md", "config.yaml"):
        assert fileops.validate_text_filename(good) == good
    # No extension at all becomes .txt rather than being refused.
    assert fileops.validate_text_filename("nota") == "nota.txt"
    for bad in ("run.bat", "a.ps1", "page.html", "icon.svg", "app.exe"):
        with pytest.raises(PCControlError):
            fileops.validate_text_filename(bad)


def test_nothing_is_ever_overwritten(executor, sandbox):
    existing = sandbox / "nota.txt"
    existing.write_text("original", encoding="utf-8")
    other = sandbox / "outra.txt"
    other.write_text("outra", encoding="utf-8")

    for tool, args in (
        ("pc_file_create_text", {"path": str(sandbox), "name": "nota.txt", "content": "novo"}),
        ("pc_file_copy", {"source": str(other), "destination": str(existing)}),
        ("pc_file_move", {"source": str(other), "destination": str(existing)}),
        ("pc_file_rename", {"source": str(other), "new_name": "nota.txt"}),
    ):
        result = executor.execute_tool(tool, args)
        assert result["success"] is False, tool
        assert result["output"]["status"] == "conflict", tool
    assert existing.read_text(encoding="utf-8") == "original"


def test_created_file_content_is_bounded(executor, sandbox):
    result = executor.execute_tool("pc_file_create_text", {
        "path": str(sandbox), "name": "grande.txt",
        "content": "x" * (fileops.MAX_TEXT_BYTES + 1)})
    assert result["success"] is False
    assert result["output"]["status"] == "invalid_input"


def test_recycle_uses_the_recycle_bin_and_never_unlink(monkeypatch, sandbox):
    """Audit item 33, proved by observing WHICH Windows call is made."""
    victim = sandbox / "descartavel.txt"
    victim.write_text("adeus", encoding="utf-8")
    recycled: list[str] = []

    def fake_recycle(path):
        recycled.append(path)
        Path(path).unlink()          # what the shell would have done
        return 0, False

    monkeypatch.setattr(winapi, "shell_recycle", fake_recycle)
    monkeypatch.setattr(winapi, "recycle_bin_items",
                        lambda root: 10 if not recycled else 11)
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)

    result = fileops.recycle_file(str(victim))
    assert recycled == [str(victim)], "SHFileOperationW was not the mechanism"
    assert result["recycled"] is True
    assert result["bin_items_after"] > result["bin_items_before"]


def test_a_removal_that_did_not_reach_the_bin_is_reported_honestly(monkeypatch, sandbox):
    """The bin count is the verification, and it is allowed to say no."""
    victim = sandbox / "descartavel.txt"
    victim.write_text("adeus", encoding="utf-8")
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "shell_recycle",
                        lambda path: (Path(path).unlink(), (0, False))[1])
    monkeypatch.setattr(winapi, "recycle_bin_items", lambda root: 10)

    result = fileops.recycle_file(str(victim))
    assert result["recycled"] is False, "an unverified removal was reported as recycled"


def test_a_cancelled_recycle_reports_refused_and_removes_nothing(monkeypatch, sandbox):
    victim = sandbox / "fica.txt"
    victim.write_text("fico", encoding="utf-8")
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "shell_recycle", lambda path: (0, True))
    monkeypatch.setattr(winapi, "recycle_bin_items", lambda root: 10)

    with pytest.raises(PCControlError) as excinfo:
        fileops.recycle_file(str(victim))
    assert excinfo.value.status == "refused"
    assert victim.exists()


def test_path_traversal_is_refused_by_the_central_validator(executor, sandbox):
    """Audit item 12, at the executor rather than in a handler."""
    for attempt in (r"..\..\Windows\System32\config\SAM",
                    "../../../../etc/passwd",
                    r"\\?\C:\Windows\notepad.exe",
                    r"\\server\share\x.txt"):
        result = executor.execute_tool("pc_file_recycle", {"path": attempt})
        assert result["success"] is False, attempt
        assert result["status"] in {"invalid_input", "permission_denied"}, attempt


def test_opening_an_executable_is_still_refused(executor, sandbox):
    """Audit item 14, unchanged from V1 and re-asserted for the V2 surface."""
    for extension in (".exe", ".bat", ".ps1", ".vbs", ".msi", ".lnk", ".reg"):
        target = sandbox / f"payload{extension}"
        target.write_text("echo hi", encoding="utf-8")
        result = executor.execute_tool("pc_file_open", {"path": str(target)})
        assert result["success"] is False, extension
        assert result["output"]["status"] == "executable_refused", extension


# ==========================================================================
#  15-17, 34. Confirmation gates
# ==========================================================================


CONFIRMED_TOOLS = {
    "pc_window_close": {"window_id": 1},
    "pc_window_batch_close": {"app": "notepad"},
    "pc_screenshot_capture": {},
    "pc_clipboard_read": {},
    "pc_clipboard_write": {"text": "x"},
    "pc_clipboard_clear": {},
    "pc_input_type_text": {"window_id": 1, "text": "x"},
    "pc_folder_create": {"folder": "Desktop", "name": "x"},
    "pc_file_create_text": {"folder": "Desktop", "name": "x", "content": "y"},
    "pc_file_copy": {"source": "a", "destination": "b"},
    "pc_file_move": {"source": "a", "destination": "b"},
    "pc_file_rename": {"source": "a", "new_name": "b"},
    "pc_file_recycle": {"path": "a"},
    "pc_folder_recycle": {"path": "a"},
    "pc_session_lock": {},
    "pc_power_sleep": {},
    "pc_power_restart": {},
    "pc_power_shutdown": {},
    "pc_session_logoff": {},
}

UNCONFIRMED_TOOLS = (
    "pc_app_search", "pc_app_list_running", "pc_window_list", "pc_volume_get",
    "pc_system_info", "pc_network_status", "pc_storage_info", "pc_display_info",
    "pc_file_search", "pc_app_launch", "pc_window_focus", "pc_window_snap",
    "pc_volume_set", "pc_web_open_url", "pc_settings_open", "pc_media_control",
)


@pytest.mark.parametrize("tool", sorted(CONFIRMED_TOOLS))
def test_every_sensitive_tool_requires_confirmation(executor, tool):
    """Audit items 15-17 and 34, one assertion per gated capability."""
    entry = executor.registry[tool]
    capability = entry["capabilities"][0]
    evaluation = executor.permission_manager.policy_engine.evaluate(capability)
    assert evaluation.requires_confirmation is True, f"{tool} ({capability})"
    assert evaluation.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}, f"{tool} ({capability})"


@pytest.mark.parametrize("tool", UNCONFIRMED_TOOLS)
def test_ordinary_tools_do_not_raise_a_dialog(executor, tool):
    """The other half of the contract: a prompt for everything is a prompt for nothing."""
    capability = executor.registry[tool]["capabilities"][0]
    evaluation = executor.permission_manager.policy_engine.evaluate(
        capability, target="window:1", scope="current_workspace")
    assert evaluation.requires_confirmation is False, f"{tool} ({capability})"


def test_a_power_action_never_runs_before_confirmation(refusing_executor, monkeypatch):
    """Audit item 34. The OS call is stubbed, and asserted never to happen."""
    called: list[str] = []
    monkeypatch.setattr(winapi, "exit_windows", lambda flags: called.append(flags) or True)
    monkeypatch.setattr(winapi, "lock_workstation", lambda: called.append("lock") or True)
    monkeypatch.setattr(winapi, "suspend_system", lambda: called.append("sleep") or True)

    for tool in ("pc_session_lock", "pc_power_sleep", "pc_power_restart",
                 "pc_power_shutdown", "pc_session_logoff"):
        result = refusing_executor.execute_tool(tool, {})
        assert result["success"] is False, tool
        assert result["status"] == "permission_denied", tool
    assert called == [], f"a power action reached Windows without approval: {called}"


def test_a_confirmed_power_action_reaches_exactly_the_right_windows_call(executor, monkeypatch):
    """And it is never FORCED: no EWX_FORCE, so an application can still veto."""
    flags: list[int] = []
    monkeypatch.setattr(winapi, "exit_windows", lambda value: flags.append(value) or True)
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)

    assert executor.execute_tool("pc_power_restart", {})["success"] is True
    assert executor.execute_tool("pc_power_shutdown", {})["success"] is True
    assert executor.execute_tool("pc_session_logoff", {})["success"] is True
    assert flags == [winapi.EWX_REBOOT,
                     winapi.EWX_SHUTDOWN | winapi.EWX_POWEROFF,
                     winapi.EWX_LOGOFF]
    for value in flags:
        assert not value & 0x00000004, "EWX_FORCE was set; unsaved work could be lost"


def test_power_actions_can_never_be_covered_by_a_task_grant():
    """No broad task grant may silently allow a future shutdown."""
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    for capability in ("pc.power.restart", "pc.power.shutdown", "pc.session.logoff",
                       "pc.file.recycle", "pc.folder.recycle"):
        assert manager.is_critical_capability(capability), capability
        request = manager.request_permission(capability, {"target": "power:x"},
                                             task_id="task-1")
        outcome = manager.resolve_permission(request, "allow_for_task")
        assert outcome["ok"] is False, capability
        assert outcome["error"] == "critical_requires_explicit_confirmation"


def test_a_refused_confirmation_stops_a_batch_close(refusing_executor, monkeypatch):
    closed: list[int] = []
    monkeypatch.setattr(windows, "resolve_group", lambda app, **kw: [
        {"window_id": 1, "title": "A", "process": "notepad.exe", "state": "normal"},
        {"window_id": 2, "title": "B", "process": "notepad.exe", "state": "normal"}])
    monkeypatch.setattr(windows, "close", lambda hwnd: closed.append(hwnd) or {"closed": True})

    result = refusing_executor.execute_tool("pc_window_batch_close", {"app": "notepad"})
    assert result["success"] is False
    assert result["status"] == "permission_denied"
    assert closed == []


def test_a_batch_close_confirmation_describes_the_whole_target_set(executor, monkeypatch):
    """"Fecha tudo do Discord" is a different decision at one window and at nine."""
    monkeypatch.setattr(windows, "resolve_group", lambda app, **kw: [
        {"window_id": 1, "title": "#geral - Discord", "process": "discord.exe", "state": "normal"},
        {"window_id": 2, "title": "#admin - Discord", "process": "discord.exe", "state": "normal"}])
    monkeypatch.setattr(windows, "close", lambda hwnd: {"closed": True, "title": "x"})

    result = executor.execute_tool("pc_window_batch_close", {"app": "discord"})
    assert result["success"] is True
    assert result["output"]["count"] == 2
    assert len(result["output"]["titles"]) == 2
    # And the grant is bound to the SET, not to "any window".
    capability, args = executor.asked[-1]
    assert capability == "pc.window.batch_close"
    assert args["target"] == "windows:discord"
    assert ":any" not in args["target"]


# ==========================================================================
#  18-19. Target binding and ALLOW_ONCE
# ==========================================================================


def test_a_grant_for_one_target_never_authorises_another():
    """Audit item 18, across every V2 shape of target."""
    manager = PermissionManager(confirmation_callback=lambda *_: True)
    pairs = [
        ("pc.window.close", "window:100", "window:200"),
        ("pc.file.recycle", r"recycle:C:\a.txt", r"recycle:C:\b.txt"),
        ("pc.file.move", "file:a -> b", "file:a -> c"),
        ("pc.clipboard.write", "clipboard:write:#aaaa", "clipboard:write:#bbbb"),
        ("pc.input.type", "input:type:window:1:#aaaa", "input:type:window:2:#aaaa"),
        ("pc.power.shutdown", "power:shutdown", "power:restart"),
    ]
    for capability, approved, other in pairs:
        request = manager.request_permission(capability, {"_pc_target": approved})
        manager.resolve_permission(request, "allow_once")
        assert manager._has_execution_grant(capability, {"_pc_target": approved}) is True
        assert manager._has_execution_grant(capability, {"_pc_target": other}) is False, \
            f"{capability}: a grant for {approved} covered {other}"


def test_moving_a_file_binds_the_grant_to_source_and_destination():
    """A grant on the source alone would let one approval reach a new destination."""
    from core.tool_execution import _pc_control_target

    first = _pc_control_target("pc_file_move", {"source": "a.txt", "destination": "B"})
    second = _pc_control_target("pc_file_move", {"source": "a.txt", "destination": "C"})
    assert first != second
    assert "a.txt" in first and "B" in first


def test_a_model_supplied_pc_target_is_discarded(executor, monkeypatch):
    """The authoritative target must not be forgeable from tool arguments."""
    monkeypatch.setattr(windows, "resolve_window", lambda **kw: {
        "window_id": 900, "title": "Alvo", "process": "notepad.exe",
        "visible": True, "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "close", lambda hwnd: {"closed": True, "title": "Alvo"})

    executor.execute_tool("pc_window_close",
                          {"window_id": 900, "_pc_target": "window:inofensivo"})
    capability, args = executor.asked[-1]
    assert args["_pc_target"] == "window:900", "the model rewrote its own permission target"


def test_allow_once_authorises_exactly_one_execution(monkeypatch):
    """Audit item 19."""
    plugin_loader.load_all_plugins()
    manager = PermissionManager()
    tool_executor = ToolExecutor(manager)
    tool_executor.register_plugin_tools()

    from core.tool_execution import _digest

    text = "uma vez"
    captured: list[str] = []
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "clipboard_write_text",
                        lambda value: captured.append(value) or True)
    monkeypatch.setattr(winapi, "clipboard_read_text", lambda limit: text)

    # The grant is keyed on the content DIGEST, which is how the same target
    # string is produced by the executor when the real call arrives.
    request = manager.request_permission(
        "pc.clipboard.write", {"_pc_target": f"clipboard:write:#{_digest(text)}"})
    assert manager.resolve_permission(request, "allow_once")["ok"] is True

    first = tool_executor.execute_tool("pc_clipboard_write", {"text": text})
    assert first["success"] is True, first
    assert captured == [text]

    # There is NO confirmation callback on this manager, so a second execution
    # can only succeed by re-using the consumed grant. It must not.
    second = tool_executor.execute_tool("pc_clipboard_write", {"text": text})
    assert second["success"] is False, "the one-shot grant authorised a second execution"
    assert second["status"] == "permission_denied"
    assert captured == [text], "the clipboard was written twice from one approval"


# ==========================================================================
#  20. Provider failover cannot duplicate a V2 action
# ==========================================================================


CONSEQUENTIAL_CALLS = [
    ("pc_window_snap", {"window_id": 12, "position": "left"}),
    ("pc_window_move", {"window_id": 12, "x": 100, "y": 100}),
    ("pc_input_type_text", {"window_id": 12, "text": "olá"}),
    ("pc_file_move", {"source": "a.txt", "destination": "b"}),
    ("pc_file_recycle", {"path": "a.txt"}),
    ("pc_clipboard_write", {"text": "copiado"}),
    ("pc_screenshot_capture", {"mode": "desktop"}),
    ("pc_window_close", {"window_id": 12}),
    ("pc_power_shutdown", {}),
]


@pytest.mark.parametrize("name,args", CONSEQUENTIAL_CALLS,
                         ids=[call[0] for call in CONSEQUENTIAL_CALLS])
def test_a_provider_failover_never_repeats_a_v2_action(monkeypatch, name, args):
    """Audit item 20, for every consequential V2 capability.

    Groq issues the call, it runs, Groq dies, and the local model re-issues the
    identical call from the transcript it was handed. The machine must be acted
    on once.
    """
    from test_provider_fallback import (
        RATE_LIMIT_HEADERS, FakeGroqError, RecordingExecutor, _Chunk,
        build_brain, collect, local_text, local_tool_call, run,
    )

    executor = RecordingExecutor(result={"success": True, "status": "completed",
                                         "output": {"ok": True, "status": "done"},
                                         "metadata": {}})
    executor.registry[name] = {}
    schema = [{"type": "function", "function": {"name": name, "description": "x",
                                                "parameters": {"type": "object"}}}]
    brain = build_brain(
        monkeypatch, mode="AUTO", tools=schema, executor=executor,
        groq_script=[[_Chunk(tool_calls=[(name, json.dumps(args))])],
                     FakeGroqError(429, RATE_LIMIT_HEADERS)],
        ollama_script=[local_tool_call(name, args), local_text("Feito.")])

    run(collect(brain, "faz isso"))
    assert executor.executions == [(name, args)], (
        f"{name} ran {len(executor.executions)} times: {executor.executions}")


def test_the_ledger_treats_two_spellings_of_one_path_as_one_call(monkeypatch):
    """Audit item 20 with V2's wider argument shapes -- see PART V.

    `(tool_name, arguments)` was enough for V1's window ids. A path can be
    written several ways for the same file, and two spellings would be two
    ledger keys, which is exactly the hole the ledger closes.
    """
    from test_provider_fallback import RecordingExecutor, _Chunk, build_brain, run

    executor = RecordingExecutor(result={"success": True, "status": "completed",
                                         "output": {"ok": True}, "metadata": {}})
    executor.registry["pc_file_recycle"] = {}
    brain = build_brain(monkeypatch, mode="AUTO", executor=executor,
                        groq_script=[[_Chunk("x")]])

    run(brain._run_tool({"function": {"name": "pc_file_recycle",
                                      "arguments": '{"path": "C:/Users/x/a.txt"}'}}))
    run(brain._run_tool({"function": {"name": "pc_file_recycle",
                                      "arguments": '{"path": "C:\\\\Users\\\\X\\\\a.txt"}'}}))
    assert len(executor.executions) == 1, "one file was recycled twice"


def test_the_ledger_still_separates_genuinely_different_calls(monkeypatch):
    """The normalisation must not merge two calls that do different things."""
    from test_provider_fallback import RecordingExecutor, _Chunk, build_brain, run

    executor = RecordingExecutor(result={"success": True, "status": "completed",
                                         "output": {"ok": True}, "metadata": {}})
    executor.registry["pc_window_snap"] = {}
    brain = build_brain(monkeypatch, mode="AUTO", executor=executor,
                        groq_script=[[_Chunk("x")]])
    for position in ("left", "right"):
        run(brain._run_tool({"function": {
            "name": "pc_window_snap",
            "arguments": json.dumps({"window_id": 1, "position": position})}}))
    assert len(executor.executions) == 2


# ==========================================================================
#  21-22, 35. Ambiguity fails closed
# ==========================================================================


def test_a_batch_action_never_matches_a_loose_title(monkeypatch):
    """Audit item 22. "Todas as janelas do Discord" means the Discord PROCESS.

    A browser tab or a chat message can put the word "discord" in any caption,
    so a substring match would close windows that merely mention it.
    """
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": 1, "title": "Discord — #geral", "process": "brave.exe", "state": "normal"},
        {"window_id": 2, "title": "notas sobre discord.txt", "process": "notepad.exe",
         "state": "normal"},
    ])
    with pytest.raises(PCControlError) as excinfo:
        windows.resolve_group("discord")
    assert excinfo.value.status == "not_found"


def test_a_batch_action_is_bounded(monkeypatch):
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": index, "title": f"w{index}", "process": "chrome.exe", "state": "normal"}
        for index in range(windows.MAX_BATCH_WINDOWS + 5)])
    with pytest.raises(PCControlError) as excinfo:
        windows.resolve_group("chrome")
    assert excinfo.value.status == "invalid_input"


def test_an_ambiguous_application_is_never_launched_or_switched_to(executor, monkeypatch):
    """Audit item 21."""
    launched: list = []
    monkeypatch.setattr(applications, "resolve", lambda query: (None, [
        (applications.AppEntry("Nano Alpha", "a.lnk", "builtin"), 1.0),
        (applications.AppEntry("Nano Beta", "b.lnk", "builtin"), 1.0)]))
    monkeypatch.setattr(applications, "launch", lambda entry: launched.append(entry))

    for tool in ("pc_app_launch", "pc_app_switch"):
        result = executor.execute_tool(tool, {"name": "Nano"})
        assert result["success"] is False, tool
        assert result["output"]["status"] == "ambiguous", tool
        assert len(result["output"]["candidates"]) == 2
    assert launched == []


def test_switching_never_silently_launches_something(executor, monkeypatch):
    """"Muda para o Discord" when Discord is closed is a question, not a licence."""
    launched: list = []
    monkeypatch.setattr(applications, "launch", lambda entry: launched.append(entry))
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [])

    result = executor.execute_tool("pc_app_switch", {"name": "Calculadora"})
    assert result["success"] is False
    assert result["output"]["status"] == "not_found"
    assert launched == []


def test_a_loose_match_plus_a_consequential_verb_asks(monkeypatch):
    """Audit item 35, the voice-ambiguity rule, on the V2 surface too."""
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": 1, "title": "relatorio final.docx - Word", "process": "winword.exe",
         "state": "normal"},
    ])
    # A read-only action may match loosely...
    assert windows.resolve_window(query="relatorio", allow_partial=True)["window_id"] == 1
    # ...a consequential one may not.
    for consequential in (lambda: windows.resolve_window(query="relatorio", allow_partial=False),
                          lambda: windows.resolve_input_target(query="relatorio")):
        with pytest.raises(PCControlError) as excinfo:
            consequential()
        assert excinfo.value.status == "ambiguous"


# ==========================================================================
#  23-26. Malformed input, NaN, and screen geometry
# ==========================================================================


@pytest.mark.parametrize("tool,args", [
    ("pc_window_snap", {"window_id": 1}),
    ("pc_window_snap", {"window_id": 1, "position": "diagonal"}),
    ("pc_window_move", {"window_id": 1}),
    ("pc_window_move_monitor", {"window_id": 1, "monitor": 99}),
    ("pc_window_set_topmost", {"window_id": 1}),
    ("pc_window_batch_state", {"app": "x", "state": "explode"}),
    ("pc_media_control", {"action": "rewind"}),
    ("pc_display_set_brightness", {}),
    ("pc_clipboard_write", {}),
    ("pc_input_press_key", {"window_id": 1}),
    ("pc_input_hotkey", {"window_id": 1, "hotkey": "ctrl+alt+del"}),
    ("pc_pointer_scroll", {"window_id": 1, "direction": "diagonally"}),
    ("pc_file_create_text", {"name": "x", "content": "y"}),
    ("pc_settings_open", {"section": "recovery"}),
    ("pc_web_open_url", {}),
    ("pc_web_search", {"query": ""}),
    ("pc_app_switch", {}),
])
def test_malformed_arguments_fail_closed(executor, tool, args):
    """Audit item 23, for every V2 tool that takes an argument."""
    result = executor.execute_tool(tool, args)
    assert result["success"] is False, f"{tool}({args}) succeeded"
    status = (result.get("output") or {}).get("status") or result.get("status")
    assert status in {"invalid_input", "not_found", "ambiguous", "unsupported",
                      "unsupported_platform", "blocked", "permission_denied",
                      "state_unchanged"}, f"{tool} -> {status}"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "abc", None, True, [], {}])
def test_nan_and_infinity_are_rejected_in_every_numeric_control(bad):
    """Audit item 24. Coercing NaN to a bound is an arbitrary change, not a fix."""
    from core.pc_control import audio

    for call in (lambda: geometry.coordinate(bad, "x"),
                 lambda: display._percent(bad, "level"),
                 lambda: audio.parse_level(bad)):
        with pytest.raises(PCControlError) as excinfo:
            call()
        assert excinfo.value.status == "invalid_input", bad


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"),
                                 "abc", True, [], {}])
def test_a_malformed_delta_is_rejected_but_an_absent_one_is_the_default(bad):
    """A DELTA is optional: "baixa o brilho" with no number means ten points.

    That documented default is the one place None is not an error, which is why
    it is asserted here rather than folded into the test above -- doing that
    would have been asserting the wrong contract, and "fixing" it by dropping
    None from the list would have quietly stopped testing the default at all.
    """
    from core.pc_control import audio

    for call in (lambda: display._delta(bad), lambda: audio.parse_delta(bad)):
        with pytest.raises(PCControlError) as excinfo:
            call()
        assert excinfo.value.status == "invalid_input", bad

    assert display._delta(None) == display.DEFAULT_STEP
    assert audio.parse_delta(None) == audio.DEFAULT_STEP


def test_window_coordinates_are_bounded_to_real_monitors():
    """Audit item 25."""
    for extreme in (10 ** 9, -(10 ** 9), geometry.COORDINATE_LIMIT + 1):
        with pytest.raises(PCControlError) as excinfo:
            geometry.coordinate(extreme, "x")
        assert excinfo.value.status == "invalid_input"


@WINDOWS_ONLY
def test_a_window_can_never_be_moved_entirely_off_screen():
    """Audit item 26. A window with no visible titlebar cannot be dragged back."""
    monitors = geometry.monitors()
    union_right = max(m["work_area"][2] for m in monitors)
    union_bottom = max(m["work_area"][3] for m in monitors)

    for x, y in ((30000, 30000), (-30000, -30000), (union_right + 5000, 0),
                 (0, union_bottom + 5000)):
        plan = geometry.clamp_geometry(x, y, 800, 600)
        assert plan["clamped"] is True, (x, y)
        # The applied rectangle overlaps some real work area.
        assert geometry._intersects_any(plan["x"], plan["y"], plan["width"],
                                        plan["height"], monitors), (x, y)


@WINDOWS_ONLY
def test_a_window_is_never_shrunk_below_a_usable_size():
    plan = geometry.clamp_geometry(100, 100, 1, 1)
    assert plan["width"] >= geometry.MIN_WINDOW_WIDTH
    assert plan["height"] >= geometry.MIN_WINDOW_HEIGHT
    assert plan["clamped"] is True


@WINDOWS_ONLY
def test_every_snap_position_lands_inside_the_work_area():
    """Snapping uses the WORK area, so a snapped window is not under the taskbar."""
    monitor = geometry.monitors()[0]
    left, top, right, bottom = monitor["work_area"]
    for mode in geometry.SNAP_MODES:
        x, y, width, height = geometry.snap_rect(mode, monitor)
        assert x >= left and y >= top, mode
        assert x + width <= right and y + height <= bottom, mode
        assert width > 0 and height > 0, mode


def test_an_unknown_snap_position_is_refused():
    monitor = {"work_area": (0, 0, 1920, 1032)}
    with pytest.raises(PCControlError) as excinfo:
        geometry.snap_rect("middle", monitor)
    assert excinfo.value.status == "invalid_input"


def test_an_unknown_monitor_number_is_refused_with_the_real_count():
    with pytest.raises(PCControlError) as excinfo:
        geometry.resolve_monitor(99)
    assert excinfo.value.status == "not_found"
    assert "monitors" in excinfo.value.details


# ==========================================================================
#  27-29. Privacy: clipboard, screenshots, OCR
# ==========================================================================


def test_clipboard_content_never_reaches_a_log_or_a_permission_target(executor, monkeypatch):
    """Audit item 27."""
    secret = "palavra-passe-do-banco-12345"
    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "clipboard_write_text", lambda text: True)
    monkeypatch.setattr(winapi, "clipboard_read_text", lambda limit: secret)

    assert executor.execute_tool("pc_clipboard_write", {"text": secret})["success"] is True
    assert executor.execute_tool("pc_clipboard_read", {})["success"] is True

    audit = json.dumps(executor.permission_manager.get_audit_log(), ensure_ascii=False)
    assert secret not in audit, "clipboard content reached the audit log"
    policy_audit = json.dumps(
        executor.permission_manager.policy_engine.get_audit_events(), ensure_ascii=False)
    assert secret not in policy_audit, "clipboard content reached the policy audit"
    for capability, args in executor.asked:
        assert secret not in str(args.get("_pc_target") or "")
        assert secret not in str(args.get("target") or "")


def test_the_clipboard_module_never_hands_content_to_its_logger():
    """No history, no cache, no background watcher -- and nothing logged."""
    tree = ast.parse((PC_PACKAGE / "clipboard.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = getattr(node.func.value, "id", "")
            if owner == "logger":
                rendered = ast.dump(node)
                for forbidden in ("content", "text", "payload", "value", "confirmed"):
                    assert f"id='{forbidden}'" not in rendered, ast.unparse(node)


def test_clipboard_reads_and_writes_are_bounded():
    assert clipboard.MAX_READ_CHARS <= 8000
    assert clipboard.MAX_WRITE_CHARS <= 8000
    with pytest.raises(PCControlError):
        clipboard.write_text("x" * (clipboard.MAX_WRITE_CHARS + 1))


def test_a_screenshot_result_never_contains_image_data(executor):
    """Audit item 28: the image cannot be uploaded because it never leaves disk."""
    result = executor.execute_tool("pc_screenshot_capture", {"mode": "desktop"})
    if not result["success"]:
        pytest.skip(f"capture unavailable on this host: {result.get('output')}")
    payload = result["output"]
    try:
        assert set(payload) >= {"path", "width", "height", "size_bytes"}
        for key, value in payload.items():
            assert "base64" not in str(key).lower()
            if isinstance(value, str):
                assert len(value) < 1000, f"{key} looks like embedded image data"
        assert Path(payload["path"]).is_file()
    finally:
        Path(payload["path"]).unlink(missing_ok=True)


def test_nothing_in_pc_control_uploads_anything():
    """Audit items 28-29: no HTTP client anywhere in the package."""
    for source in _sources():
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                dumped = ast.dump(node)
                for network in ("httpx", "requests", "urllib.request", "aiohttp",
                                "socket", "http.client", "ftplib", "smtplib"):
                    assert network not in dumped, f"{source.name} imports {network}"


def test_ocr_is_not_claimed_anywhere():
    """Audit item 29 and PART S: Nano must not imply it can see the screen.

    OCR is deferred (see docs/architecture/PC_CONTROL.md). Until it exists, no
    tool may be named or described as if it read the screen, because "I can see
    what is on your screen" is a claim a screenshot path does not support.
    """
    for name, function in _tool_schemas().items():
        assert "ocr" not in name.lower()
        description = str(function.get("description") or "").lower()
        for claim in ("ler o ecrã", "lê o ecrã", "vê o ecrã", "consigo ver"):
            assert claim not in description, f"{name}: {description}"


# ==========================================================================
#  30. Result bounds
# ==========================================================================


def test_every_v2_result_stays_inside_the_declared_bounds(executor):
    from core.pc_control.results import MAX_RESULT_BYTES

    for tool in ("pc_window_list", "pc_app_list_running", "pc_display_info",
                 "pc_storage_info", "pc_network_status", "pc_system_info"):
        result = executor.execute_tool(tool, {})
        if not result["success"]:
            continue
        encoded = json.dumps(result["output"], ensure_ascii=False, default=str)
        assert len(encoded.encode("utf-8")) <= MAX_RESULT_BYTES, tool


def test_the_running_application_list_is_grouped_not_a_process_table(monkeypatch):
    """A process table is hundreds of entries and discloses everything installed."""
    monkeypatch.setattr(windows, "list_windows", lambda **kw: [
        {"window_id": index, "title": f"janela {index}",
         "process": f"app{index % 3}.exe", "state": "normal", "focused": False}
        for index in range(50)])
    running = applications.running_applications()
    assert len(running) == 3
    assert all(len(entry["titles"]) <= 3 for entry in running)


def test_a_permission_target_is_bounded():
    from core.tool_execution import MAX_TARGET_CHARS, _pc_control_target

    target = _pc_control_target("pc_file_move", {"source": "a" * 5000,
                                                 "destination": "b" * 5000})
    assert len(target) <= MAX_TARGET_CHARS


# ==========================================================================
#  32. No process termination, anywhere
# ==========================================================================


def test_no_v2_module_can_terminate_a_process(monkeypatch):
    """Audit item 32, behaviourally: psutil's killers are made to explode."""
    import psutil

    def forbidden(*_args, **_kwargs):
        raise AssertionError("PC Control attempted to terminate a process")

    monkeypatch.setattr(psutil.Process, "terminate", forbidden, raising=False)
    monkeypatch.setattr(psutil.Process, "kill", forbidden, raising=False)

    monkeypatch.setattr(winapi, "IS_WINDOWS", True)
    monkeypatch.setattr(winapi, "post_close", lambda hwnd: True)
    monkeypatch.setattr(winapi, "is_window", lambda hwnd: False)
    monkeypatch.setattr(winapi, "window_title", lambda hwnd: "Alvo")
    assert windows.close(1)["closed"] is True

    monkeypatch.setattr(winapi, "is_window", lambda hwnd: True)
    outcome = windows.close(1)
    assert outcome["closed"] is False
    assert "não força" in outcome["detail"]


# ==========================================================================
#  Real Windows behaviour
# ==========================================================================


@WINDOWS_ONLY
def test_the_catalogue_finds_packaged_and_registered_applications():
    """V2's whole reason for widening discovery: V1 could not find Spotify."""
    catalogue = applications.build_catalogue(force=True)
    sources = {entry.source for entry in catalogue}
    assert "start_menu_user" in sources or "start_menu_system" in sources
    assert sources & {"registered_app", "store_app"}, (
        "neither App Paths nor the Store aliases contributed anything")
    assert len(catalogue) > 20


@WINDOWS_ONLY
def test_monitor_enumeration_is_real_and_ordered():
    monitors = geometry.monitors()
    assert monitors
    assert [m["number"] for m in monitors] == list(range(1, len(monitors) + 1))
    assert sum(1 for m in monitors if m["primary"]) >= 1
    for monitor in monitors:
        assert monitor["width"] > 0 and monitor["height"] > 0
        left, top, right, bottom = monitor["work_area"]
        assert right > left and bottom > top
        # The work area is inside the monitor, and usually smaller (taskbar).
        assert (right - left) <= monitor["width"]
        assert (bottom - top) <= monitor["height"]


@WINDOWS_ONLY
def test_display_info_reports_brightness_support_honestly(executor):
    result = executor.execute_tool("pc_display_info", {})
    assert result["success"] is True
    for monitor in result["output"]["monitors"]:
        assert isinstance(monitor["brightness_supported"], bool)
        if monitor["brightness_supported"]:
            assert 0 <= monitor["brightness_percent"] <= 100
        else:
            assert "brightness_percent" not in monitor


@WINDOWS_ONLY
def test_brightness_can_be_set_and_is_restored(executor):
    """Mutates the real panel, so the original level is restored in finally."""
    info = executor.execute_tool("pc_display_info", {})
    monitors = [m for m in info["output"]["monitors"] if m["brightness_supported"]]
    if not monitors:
        pytest.skip("no monitor on this host exposes software brightness")

    original = monitors[0]["brightness_percent"]
    try:
        result = executor.execute_tool("pc_display_set_brightness", {"level": 40})
        assert result["success"] is True
        assert abs(result["output"]["level"] - 40) <= 5
        assert result["output"]["verified"] is True

        changed = executor.execute_tool("pc_display_change_brightness", {"delta": 20})
        assert abs(changed["output"]["level"] - 60) <= 6
    finally:
        executor.execute_tool("pc_display_set_brightness", {"level": original})

    restored = executor.execute_tool("pc_display_info", {})
    assert abs(restored["output"]["monitors"][0]["brightness_percent"] - original) <= 5


@WINDOWS_ONLY
def test_an_unsupported_brightness_request_says_so_rather_than_faking_it(monkeypatch):
    monkeypatch.setattr(winapi, "monitor_brightness", lambda handle: None)
    written: list = []
    monkeypatch.setattr(winapi, "set_monitor_brightness",
                        lambda handle, value: written.append(value) or True)
    with pytest.raises(PCControlError) as excinfo:
        display.set_brightness(50)
    assert excinfo.value.status == "unsupported"
    assert written == [], "brightness was written to a monitor that cannot report it"


@WINDOWS_ONLY
def test_the_clipboard_round_trips_and_is_restored(executor):
    before = executor.execute_tool("pc_clipboard_read", {})
    original = (before.get("output") or {}).get("text") if before["success"] else None
    try:
        written = executor.execute_tool("pc_clipboard_write",
                                        {"text": "nano-v2 acentuação ✓"})
        assert written["success"] is True
        assert written["output"]["verified"] is True

        read = executor.execute_tool("pc_clipboard_read", {})
        assert read["success"] is True
        assert read["output"]["text"] == "nano-v2 acentuação ✓"
    finally:
        if original:
            executor.execute_tool("pc_clipboard_write", {"text": original})
        else:
            executor.execute_tool("pc_clipboard_clear", {})


@WINDOWS_ONLY
def test_the_full_file_lifecycle_in_a_dedicated_directory(executor, sandbox):
    """create -> rename -> copy -> move -> recycle, every step result-backed.

    In a directory this test made, under the system temp folder. No personal
    file is touched at any point.
    """
    created = executor.execute_tool("pc_file_create_text", {
        "path": str(sandbox), "name": "nota", "content": "Nano PC Control V2"})
    assert created["success"] is True
    assert created["output"]["name"] == "nota.txt"
    assert (sandbox / "nota.txt").read_text(encoding="utf-8") == "Nano PC Control V2"

    renamed = executor.execute_tool("pc_file_rename", {
        "source": str(sandbox / "nota.txt"), "new_name": "relatorio"})
    assert renamed["success"] is True
    assert renamed["output"]["name"] == "relatorio.txt"

    copied = executor.execute_tool("pc_file_copy", {
        "source": str(sandbox / "relatorio.txt"),
        "destination": str(sandbox / "copia.txt")})
    assert copied["success"] is True
    assert (sandbox / "copia.txt").is_file()

    moved = executor.execute_tool("pc_file_move", {
        "source": str(sandbox / "copia.txt"),
        "destination": str(sandbox / "movido.txt")})
    assert moved["success"] is True
    assert not (sandbox / "copia.txt").exists()
    assert (sandbox / "movido.txt").is_file()

    recycled = executor.execute_tool("pc_file_recycle",
                                     {"path": str(sandbox / "movido.txt")})
    assert recycled["success"] is True
    assert recycled["output"]["status"] in {"recycled", "removed_not_recycled"}
    assert not (sandbox / "movido.txt").exists()
    if recycled["output"]["status"] == "recycled":
        assert recycled["output"]["bin_items_after"] > recycled["output"]["bin_items_before"]

    folder = executor.execute_tool("pc_folder_create",
                                   {"path": str(sandbox), "name": "Nano Teste"})
    assert folder["success"] is True
    assert (sandbox / "Nano Teste").is_dir()
    removed = executor.execute_tool("pc_folder_recycle",
                                    {"path": str(sandbox / "Nano Teste")})
    assert removed["success"] is True
    assert not (sandbox / "Nano Teste").exists()


@WINDOWS_ONLY
def test_network_and_storage_report_no_identifying_information(executor):
    network = executor.execute_tool("pc_network_status", {})
    storage = executor.execute_tool("pc_storage_info", {})
    assert network["success"] is True and storage["success"] is True

    blob = json.dumps([network["output"], storage["output"]], ensure_ascii=False).lower()
    for forbidden in ("mac", "ipv4", "ipv6", "gateway", "ssid", "serial",
                      "product_key", "dns", "subnet"):
        assert f'"{forbidden}"' not in blob, forbidden
    # And no value that looks like an address.
    import re

    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}\b", blob), "an IP address leaked"
    assert not re.search(r"\b([0-9a-f]{2}[:-]){5}[0-9a-f]{2}\b", blob), "a MAC address leaked"


@WINDOWS_ONLY
def test_a_real_window_can_be_snapped_moved_and_restored(executor):
    """Uses the Calculator, opened and closed by this test."""
    launched = executor.execute_tool("pc_app_launch", {"name": "Calculadora"})
    assert launched["success"] is True

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
    original = geometry.current_rect(window_id)
    monitor = geometry.monitors()[0]
    try:
        snapped = executor.execute_tool("pc_window_snap",
                                        {"window_id": window_id, "position": "left"})
        assert snapped["success"] is True, snapped["output"]
        rect = snapped["output"]["geometry"]
        assert rect["x"] <= monitor["work_area"][0] + 40
        assert rect["width"] <= monitor["work_width"] // 2 + 40

        centered = executor.execute_tool("pc_window_center", {"window_id": window_id})
        assert centered["success"] is True
        middle = centered["output"]["geometry"]
        assert middle["x"] > rect["x"]

        moved = executor.execute_tool("pc_window_move",
                                      {"window_id": window_id, "x": 120, "y": 90})
        assert moved["success"] is True
        assert abs(moved["output"]["geometry"]["x"] - 120) <= 20

        topmost = executor.execute_tool("pc_window_set_topmost",
                                        {"window_id": window_id, "topmost": True})
        assert topmost["success"] is True
        assert topmost["output"]["topmost"] is True
        executor.execute_tool("pc_window_set_topmost",
                              {"window_id": window_id, "topmost": False})
    finally:
        geometry.apply_geometry(window_id, original["x"], original["y"],
                                original["width"], original["height"])
        closed = executor.execute_tool("pc_window_close", {"window_id": window_id})
        assert closed["success"] is True


@WINDOWS_ONLY
def test_capturing_a_named_window_produces_a_real_bounded_image(executor):
    listed = executor.execute_tool("pc_window_list", {})
    windows_found = listed["output"]["windows"]
    if not windows_found:
        pytest.skip("no visible window on this host")

    target = next((w for w in windows_found if w["state"] != "minimized"), None)
    if target is None:
        pytest.skip("every window is minimised")

    result = executor.execute_tool("pc_screenshot_capture",
                                   {"mode": "window", "window_id": target["window_id"]})
    if not result["success"]:
        pytest.skip(f"window capture unavailable: {result['output'].get('message')}")
    payload = result["output"]
    try:
        assert payload["width"] > 0 and payload["height"] > 0
        assert payload["width"] <= screen.MAX_DIMENSION
        assert payload["height"] <= screen.MAX_DIMENSION
        assert payload["method"] in {"print_window", "screen_region"}
        assert Path(payload["path"]).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    finally:
        Path(payload["path"]).unlink(missing_ok=True)


# ==========================================================================
#  The approval card: never "Permitir ação?"
# ==========================================================================


def test_every_confirmed_capability_has_a_human_action_and_scope(executor):
    """A capability the person has never heard of is not a description.

    Every capability that raises a dialog must have a headline and a scope line
    written for a human. A new gated tool with no entry here is a card that
    reads "PC WINDOW CLOSE" -- legible, but not the sentence somebody should be
    approving.
    """
    from core import confirmation

    for tool in sorted(CONFIRMED_TOOLS):
        capability = executor.registry[tool]["capabilities"][0]
        assert capability in confirmation.ACTION_LABELS, f"{tool} ({capability})"
        assert capability in confirmation.SCOPE_LABELS, f"{tool} ({capability})"


@pytest.mark.parametrize("tool,args", sorted(CONFIRMED_TOOLS.items()))
def test_the_approval_card_names_the_action_and_the_target(executor, tool, args):
    """Audit: the dialog shows ACTION, TARGET and SCOPE, for every gated tool."""
    from core import confirmation
    from core.tool_execution import _pc_control_target

    capability = executor.registry[tool]["capabilities"][0]
    target = _pc_control_target(tool, args)
    card = confirmation.describe(capability, {**args, "_pc_target": target})

    assert card["action"] and card["action"] != "AÇÃO DESCONHECIDA", tool
    assert card["scope"] and card["scope"] != "Esta execução", tool
    assert card["target"] and card["target"] != "—", tool
    # The raw machine identity must not be what the person is asked to judge.
    assert not card["action"].startswith("pc."), tool
    for prefix in ("window:", "recycle:", "clipboard:", "power:", "session:",
                   "input:type:", "windows:"):
        assert not card["target"].startswith(prefix), f"{tool}: {card['target']}"


def test_the_card_never_degrades_to_a_bare_permission_question():
    """Even an unlabelled capability gets a sentence naming what it is."""
    from core import confirmation

    card = confirmation.describe("pc.future.capability", {"_pc_target": "thing:1"})
    assert card["action"] == "FUTURE CAPABILITY"
    assert "Permitir" not in confirmation.message("pc.future.capability", {})


def test_the_card_previews_the_size_of_a_batch_close(monkeypatch):
    """"Fecha tudo do Discord" is a different decision at one window than at nine."""
    from core import confirmation

    monkeypatch.setattr(windows, "resolve_group", lambda app, **kw: [
        {"window_id": 1, "title": "#geral - Discord", "process": "discord.exe"},
        {"window_id": 2, "title": "#admin - Discord", "process": "discord.exe"},
        {"window_id": 3, "title": "DM - Discord", "process": "discord.exe"}])

    card = confirmation.describe("pc.window.batch_close",
                                 {"app": "discord", "_pc_target": "windows:discord"})
    assert card["preview"]["count"] == 3
    assert len(card["preview"]["items"]) == 3
    assert "3" in card["preview"]["note"]


def test_the_card_previews_what_is_inside_a_folder_being_recycled(sandbox):
    from core import confirmation

    (sandbox / "a.txt").write_text("a", encoding="utf-8")
    (sandbox / "b.txt").write_text("b", encoding="utf-8")
    card = confirmation.describe("pc.folder.recycle", {"path": str(sandbox),
                                                       "_pc_target": f"recycle:{sandbox}"})
    assert card["preview"]["count"] == 2


def test_a_preview_that_fails_never_stops_the_question(monkeypatch):
    """Failing to DESCRIBE an action must never become failing to ASK about it."""
    from core import confirmation

    def explode(*_args, **_kwargs):
        raise RuntimeError("windows went away")

    monkeypatch.setattr(windows, "resolve_group", explode)
    card = confirmation.describe("pc.window.batch_close", {"app": "discord"})
    assert card["action"] == "FECHAR TODAS AS JANELAS"
    assert card["preview"] == {}


def test_typed_text_appears_on_the_card_but_never_in_the_target():
    """The person must see what will be typed; the audit trail must not hold it."""
    from core.tool_execution import _digest, _pc_control_target

    secret = "a minha frase privada"
    target = _pc_control_target("pc_input_type_text", {"window_id": 3, "text": secret})
    assert secret not in target
    assert _digest(secret) in target


# ==========================================================================
#  The shape of an injected keystroke, and what its result may claim
# ==========================================================================


@WINDOWS_ONLY
def test_a_virtual_key_event_carries_its_hardware_scan_code():
    """An event with wScan = 0 is delivered, accepted, and then ignored.

    Applications frequently match accelerators on the SCAN CODE rather than the
    virtual key. SendInput reports success either way, so the failure is silent
    -- which is how this shipped unnoticed until the keys were sent at a real
    window and nothing happened.
    """
    event = winapi._key_event(ord("A"), up=False)
    assert event.ki.wVk == ord("A")
    assert event.ki.wScan == winapi.ctypes.windll.user32.MapVirtualKeyW(
        ord("A"), winapi.MAPVK_VK_TO_VSC)
    assert event.ki.wScan != 0, "the scan code was not filled in"


@WINDOWS_ONLY
@pytest.mark.parametrize("key", ["up", "down", "left", "right", "home", "end",
                                 "page_up", "page_down", "delete"])
def test_extended_keys_are_flagged_as_extended(key):
    """Without the flag these arrive as their NUMPAD twins -- a different key."""
    _name, code = keyboard.validate_key(key)
    event = winapi._key_event(code, up=False)
    assert event.ki.dwFlags & winapi.KEYEVENTF_EXTENDEDKEY, (
        f"{key} is an extended key and was not flagged")


@WINDOWS_ONLY
def test_an_ordinary_key_is_not_flagged_as_extended():
    """The guard above must not pass by flagging everything."""
    for key in ("enter", "escape", "tab", "space", "backspace"):
        _name, code = keyboard.validate_key(key)
        event = winapi._key_event(code, up=False)
        assert not (event.ki.dwFlags & winapi.KEYEVENTF_EXTENDEDKEY), key


@WINDOWS_ONLY
def test_typed_text_carries_the_character_not_a_key():
    """Unicode injection is why accented Portuguese types correctly on any layout."""
    event = winapi._key_event(0, up=False, unicode_char=ord("ç"))
    assert event.ki.dwFlags & winapi.KEYEVENTF_UNICODE
    assert event.ki.wVk == 0
    assert event.ki.wScan == ord("ç")


def test_a_keystroke_result_claims_only_that_it_was_sent(executor, monkeypatch):
    """SendInput succeeding is not the application acting, and the two differ.

    Measured on this machine: Win+D takes effect, while Ctrl+C aimed at Windows
    11 Notepad does not -- and `SendInput` reports success for both, because in
    both the event really was injected. Nano cannot tell them apart, so it says
    the true thing and says the limit out loud.
    """
    monkeypatch.setattr(windows, "resolve_window", lambda **kw: {
        "window_id": 11, "title": "Bloco de Notas", "process": "notepad.exe",
        "visible": True, "state": "normal", "focused": False})
    monkeypatch.setattr(windows, "is_terminal_window", lambda window: False)
    monkeypatch.setattr(windows, "is_nano_window", lambda window: False)
    monkeypatch.setattr(keyboard, "focus_and_verify",
                        lambda hwnd: {"focused": True, "title": "Bloco de Notas"})
    monkeypatch.setattr(keyboard, "press_key",
                        lambda key: {"key": key, "sent": True})
    monkeypatch.setattr(keyboard, "press_hotkey",
                        lambda name: {"hotkey": name, "label": "Ctrl+C", "sent": True})

    for tool, args in (("pc_input_press_key", {"window_id": 11, "key": "enter"}),
                       ("pc_input_hotkey", {"window_id": 11, "hotkey": "copy"})):
        result = executor.execute_tool(tool, args)
        assert result["success"] is True, tool
        assert result["output"]["status"] == "sent", tool
        assert result["output"]["confirmed"] is False, tool
        message = result["output"]["message"].lower()
        assert "enviei" in message, tool
        assert "não consigo confirmar" in message, tool


def test_media_control_makes_the_same_limited_claim(executor, monkeypatch):
    monkeypatch.setattr(keyboard, "press_media",
                        lambda action: {"action": "play_pause", "label": "x", "sent": True})
    result = executor.execute_tool("pc_media_control", {"action": "play_pause"})
    assert result["output"]["status"] == "sent"
    assert result["output"]["confirmed"] is False
