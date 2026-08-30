"""Regressions for the public-release security audit.

Three findings were fixed in that audit, and each one had already been "fixed"
once in spirit -- there was a comment, a docstring or a policy entry saying the
dangerous thing was gone while the dangerous thing was still executable. So
every test here asserts against LIVE STATE (the plugin registry, the executor,
the parsed AST) rather than against prose, and the source scans strip comments
first so an explanation of a hazard can never be mistaken for the hazard.
"""
from __future__ import annotations

import ast
import io
import re
from pathlib import Path

import pytest

from core import local_control_plane as lcp
from core.permission_manager import PermissionManager
from core.plugin_loader import get_all_tools, load_all_plugins
from core.tool_execution import ToolExecutor

ROOT = Path(__file__).resolve().parent.parent


def executable_source(path: Path) -> str:
    """The module's code with comments AND docstrings removed.

    `ast.unparse(ast.parse(src))` drops comments but KEEPS docstrings -- they
    are string expression statements, not comments. Every tombstone in this
    repository explains the hazard it replaced, quoting the exact call it
    forbids, so a scan that only strips comments matches the explanation and
    reports the file as still dangerous. That is the same trap that has now
    caught this project four times, one level deeper.

    Stripping docstrings leaves executable code only, which is the thing these
    assertions are actually about.
    """
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef,
                                 ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


@pytest.fixture(scope="module")
def executor() -> ToolExecutor:
    load_all_plugins()
    ex = ToolExecutor(permission_manager=PermissionManager())
    ex.register_plugin_tools()
    return ex


@pytest.fixture(scope="module")
def advertised() -> set[str]:
    load_all_plugins()
    return {(tool.get("function") or {}).get("name") for tool in get_all_tools()}


# ── Finding 1: the context_switcher plugin ───────────────────────────────

def test_context_switcher_exposes_no_tools(advertised, executor):
    """It shelled out to PowerShell, force-killed processes, wrote the registry
    and opened Windows Terminal -- four things Nano states it does not do."""
    for name in ("context_activate_mode", "context_list_modes"):
        assert name not in advertised, f"{name} is advertised to the model again"
        assert name not in executor.registry, f"{name} is executable again"


def test_context_switcher_has_no_executable_process_calls():
    """The tombstone must stay a tombstone. Comments are stripped via the AST,
    so the docstring describing what was removed cannot satisfy this."""
    stripped = executable_source(ROOT / "plugins" / "context_switcher.py")
    for forbidden in ("subprocess", "powershell", "taskkill", "Popen", "webbrowser"):
        assert forbidden not in stripped, f"{forbidden} is live code in context_switcher"


def test_the_mode_yaml_files_can_no_longer_drive_anything(executor):
    """config/modes/*.yaml still exist as data; nothing may execute them."""
    assert (ROOT / "config" / "modes").exists(), "the fixture files moved; update this test"
    handlers = __import__("plugins.context_switcher", fromlist=["TOOL_HANDLERS"]).TOOL_HANDLERS
    assert handlers == {}, "context_switcher registered a handler again"


# ── Finding 2: shell=True primitives in desktop_agent ────────────────────

def test_no_live_shell_true_anywhere_in_core_or_plugins():
    """`shell=True` on a model-influenced string is the exact primitive that
    produced this project's last three security incidents."""
    offenders = []
    for path in [*(ROOT / "core").rglob("*.py"), *(ROOT / "plugins").rglob("*.py")]:
        if "__pycache__" in str(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords or []:
                if (keyword.arg == "shell"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value is True):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert offenders == [], f"live shell=True call sites: {offenders}"


def test_desktop_agent_no_longer_spawns_processes():
    stripped = executable_source(ROOT / "core" / "desktop_agent.py")
    assert "subprocess" not in stripped, "desktop_agent imports subprocess again"
    for gone in ("launch_process", "kill_process"):
        assert f"def {gone}" not in stripped, f"{gone} was reintroduced"


def test_the_only_subprocess_in_the_executor_is_the_allow_listed_test_runner():
    """`project.run_tests` is legitimate and stays; it picks argv from a closed
    allow-list with shell=False. Nothing else in the executor may spawn."""
    from core import tool_execution

    stripped = executable_source(ROOT / "core" / "tool_execution.py")
    assert stripped.count("subprocess.run") == 1, (
        "a second subprocess call site appeared in the tool executor")
    assert "shell=False" in stripped
    assert set(tool_execution._ALLOWED_TEST_RUNNERS) == {"pytest", "unittest"}


# ── Finding 3: the unauthenticated local control plane ───────────────────

@pytest.mark.parametrize("origin,expected,why", [
    ("http://localhost:{p}", True, "the real UI served from localhost"),
    ("http://127.0.0.1:{p}", True, "the real UI served from 127.0.0.1"),
    ("https://evil.example", False, "an arbitrary website"),
    ("http://evil.example:{p}", False, "attacker host on our port"),
    ("http://localhost:{p}.evil.com", False, "prefix-match attack"),
    ("http://localhost:1", False, "loopback but the wrong port"),
    ("file://localhost:{p}", False, "a non-http scheme"),
    ("", False, "an empty Origin"),
    (None, False, "no Origin header at all"),
])
def test_origin_guard_accepts_only_our_own_page(origin, expected, why):
    port = 51234
    value = origin.format(p=port) if isinstance(origin, str) else origin
    assert lcp.is_origin_allowed(value, port) is expected, why


def test_the_guard_rejects_a_forged_websocket_upgrade():
    """The whole approval surface -- confirm_action, resolve_permission,
    set_emergency_stop -- rides this socket. eel does no Origin check at all,
    so a web page could otherwise approve its own permission requests."""
    import bottle

    port = 51234
    app = bottle.Bottle()
    guard = lcp.install_origin_guard(port, app)

    def attempt(path: str, origin: str | None) -> str:
        env = {
            "PATH_INFO": path, "REQUEST_METHOD": "GET",
            "wsgi.input": io.BytesIO(), "SERVER_NAME": "localhost",
            "SERVER_PORT": str(port), "wsgi.url_scheme": "http",
        }
        if origin is not None:
            env["HTTP_ORIGIN"] = origin
        bottle.request.bind(env)
        try:
            guard()
            return "allowed"
        except bottle.HTTPError as exc:
            return f"blocked:{exc.status_code}"

    assert attempt("/eel", "https://evil.example") == "blocked:403"
    assert attempt("/eel", None) == "blocked:403"
    assert attempt("/eel", f"http://localhost:{port}") == "allowed"
    # Static assets are deliberately unguarded: serving index.html to a curious
    # local process discloses nothing, and guarding it risks breaking the UI.
    assert attempt("/index.html", "https://evil.example") == "allowed"


def test_the_guard_is_actually_installed_before_the_server_starts():
    """A guard that exists but is never wired protects nothing."""
    stripped = executable_source(ROOT / "core" / "main.py")
    assert "local_control_plane.install_origin_guard" in stripped
    # ...and it must run BEFORE eel begins accepting connections.
    install_at = stripped.index("local_control_plane.install_origin_guard")
    start_at = stripped.index("eel.start")
    assert install_at < start_at, "the origin guard is installed after eel.start"


def test_the_control_plane_never_binds_beyond_loopback():
    """eel defaults to localhost; an explicit host= or all_interfaces=True
    would put the whole RPC surface on the network."""
    stripped = executable_source(ROOT / "core" / "main.py")
    assert "all_interfaces" not in stripped
    assert "0.0.0.0" not in stripped


# ── Electron: the Content Security Policy ────────────────────────────────

def _electron_main() -> str:
    return (ROOT / "electron" / "main.js").read_text(encoding="utf-8")


def _strip_js_comments(source: str) -> str:
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"(?m)//.*$", "", source)


def test_the_main_window_has_a_content_security_policy():
    code = _strip_js_comments(_electron_main())
    assert "contentSecurityPolicy" in code
    assert "applyContentSecurityPolicy(mainWindow.webContents.session" in code


@pytest.mark.parametrize("directive", [
    "default-src 'none'",
    "script-src 'self'",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
    "frame-ancestors 'none'",
])
def test_the_policy_contains_its_load_bearing_directives(directive):
    assert directive in _strip_js_comments(_electron_main()), directive


def test_the_policy_never_allows_unsafe_script_execution():
    """'unsafe-inline' is tolerated for STYLE only -- React's style={{...}}
    prop makes it unavoidable, and inline style cannot execute anything.
    Neither keyword may ever appear in script-src."""
    code = _strip_js_comments(_electron_main())
    match = re.search(r'"script-src[^"]*"', code)
    assert match, "script-src disappeared from the policy"
    assert "unsafe-inline" not in match.group(0)
    assert "unsafe-eval" not in match.group(0)


def test_electron_keeps_its_process_isolation_guarantees():
    """Behavioural coverage lives in electron/test/security.test.js; this is
    the cheap cross-check that the flags did not quietly flip."""
    code = _strip_js_comments(_electron_main())
    for flag in ("contextIsolation: true", "nodeIntegration: false",
                 "sandbox: true", "webviewTag: false", "webSecurity: true"):
        assert flag in code, f"{flag} was changed or removed"
    assert "setPermissionRequestHandler" in code
