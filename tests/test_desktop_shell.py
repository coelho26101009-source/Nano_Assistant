"""The desktop shell, and the places where it has to agree with Python.

Two halves of one application now share several facts -- where the data lives,
which port means "a Nano is already running", which operations the control
channel accepts. A comment claiming they match is worthless; these tests
execute both sides and compare the answers.

They also run the Electron shell's own Node suite and the real Chromium render
measurement, so `pytest` remains the single command that verifies everything.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import eel as eel_module
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ELECTRON_DIR = REPO_ROOT / "electron"
MAIN_JS = ELECTRON_DIR / "main.js"
ASSETS = ELECTRON_DIR / "assets"
DESKTOP_LAUNCHER = REPO_ROOT / "NANO_DESKTOP.bat"

ELECTRON_BIN = ELECTRON_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
NODE = shutil.which("node")


def _child_env() -> dict:
    """A clean environment for spawning Electron.

    ELECTRON_RUN_AS_NODE is exported by editors that are themselves Electron
    apps (VS Code sets it for its extension host). Inheriting it makes the
    electron binary run as plain Node, `require('electron')` returns the npm
    shim instead of the real API, and the app dies on startup with a confusing
    "cannot read property of undefined". Strip it here, and NANO_DESKTOP.bat
    strips it for the same reason.
    """
    env = dict(os.environ)
    env.pop("ELECTRON_RUN_AS_NODE", None)
    return env


@pytest.fixture(scope="module")
def main_module():
    import core.main as module
    return module


# ==========================================================================
#  The Electron shell's own suite
# ==========================================================================

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_electron_shell_test_suite_passes():
    """Runs electron/test/run.js so the whole project is one command."""
    result = subprocess.run(
        [NODE, str(ELECTRON_DIR / "test" / "run.js")],
        cwd=str(ELECTRON_DIR), capture_output=True, text=True, timeout=180,
        env=_child_env(),
    )
    assert result.returncode == 0, (
        "the Electron shell tests failed:\n"
        f"{result.stdout[-6000:]}\n{result.stderr[-2000:]}"
    )
    assert "0 failed" in result.stdout


# ==========================================================================
#  Where the data lives  (Part 18: exactly one canonical location)
# ==========================================================================

@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_electron_and_python_resolve_the_same_data_directory():
    """The bug this closes: Electron used to pass its own %APPDATA% folder.

    Launching Nano from the desktop app then presented an empty profile --
    no saved Groq key, no settings, no history -- because the two halves were
    reading different directories. Comparing the two implementations here is
    the only way to know they still agree.
    """
    from core.app_paths import data_root

    script = (
        "const { canonicalDataDir } = require('./lib/paths');"
        "process.stdout.write(canonicalDataDir(process.env));"
    )
    result = subprocess.run(
        [NODE, "-e", script], cwd=str(ELECTRON_DIR),
        capture_output=True, text=True, timeout=60, env=_child_env(),
    )
    assert result.returncode == 0, result.stderr

    from_electron = Path(result.stdout.strip()).resolve()
    from_python = Path(data_root()).resolve()
    assert str(from_electron).lower() == str(from_python).lower(), (
        "Electron and Python disagree about where Nano's data lives:\n"
        f"  electron: {from_electron}\n  python:   {from_python}"
    )


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_environment_the_shell_gives_python_names_the_canonical_directory():
    """Built by the real backendEnv(), then compared with Python's own answer.

    Electron used to point NANO_DATA_DIR at app.getPath('userData'), which is a
    different folder entirely. Asserting on the source text would pass the day
    someone reintroduced that through a variable, so this executes the function.
    """
    from core.app_paths import data_root

    # main.js requires the real electron module, so it is loaded through the
    # shell's own test stub -- the same one the Node suite uses.
    script = (
        "const { stubElectron } = require('./test/harness');"
        "const stub = stubElectron();"
        "const { backendEnv } = require('./main.js');"
        "stub.restore();"
        "process.stdout.write(backendEnv().NANO_DATA_DIR);"
    )
    result = subprocess.run(
        [NODE, "-e", script], cwd=str(ELECTRON_DIR),
        capture_output=True, text=True, timeout=60, env=_child_env(),
    )
    assert result.returncode == 0, result.stderr

    handed_to_python = Path(result.stdout.strip()).resolve()
    assert str(handed_to_python).lower() == str(Path(data_root()).resolve()).lower(), (
        "the desktop shell would start the backend against a different data "
        "directory: shell=" + str(handed_to_python) + " python=" + str(data_root())
    )
    assert "userData" not in str(handed_to_python)


# ==========================================================================
#  Single instance  (Part 5: the two guards must mean the same thing)
# ==========================================================================

def test_both_halves_agree_on_the_single_instance_port(main_module):
    source = MAIN_JS.read_text(encoding="utf-8")
    match = re.search(r"PY_INSTANCE_LOCK_PORT\s*=\s*(\d+)", source)
    assert match, "electron/main.js no longer names Python's instance-lock port"
    assert int(match.group(1)) == main_module.INSTANCE_LOCK_PORT, (
        "Electron probes a different port than Python binds, so it would fail to "
        "notice a backend already running and would start a second microphone owner."
    )


def test_python_still_refuses_to_become_a_second_backend(main_module):
    """The guard that actually prevents two microphone owners."""
    import socket

    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    holder.listen(1)
    port = holder.getsockname()[1]
    try:
        assert main_module.acquire_single_instance(port) is False
    finally:
        holder.close()


# ==========================================================================
#  Electron mode never opens a browser  (Part 1)
# ==========================================================================

@pytest.mark.parametrize("mode", ["electron", "ELECTRON", "Electron"])
def test_electron_mode_never_opens_a_browser_tab(main_module, mode):
    assert main_module.should_open_browser(mode) is False


@pytest.mark.parametrize("mode", ["default", "browser", "", "chrome"])
def test_other_modes_still_open_the_browser(main_module, mode):
    assert main_module.should_open_browser(mode) is True


def test_eel_is_never_asked_to_launch_a_browser_itself():
    """eel.start(mode=...) is the second, easily forgotten browser launcher.

    Parsed from the AST, not searched for in the text. core/main.py carries a
    docstring explaining the duplicate-tab bug, and that docstring quotes the
    very call being guarded against -- a textual search matches the explanation
    and reports a failure that is not there.
    """
    import ast

    tree = ast.parse((REPO_ROOT / "core" / "main.py").read_text(encoding="utf-8"))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "start"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "eel"
    ]
    assert calls, "core/main.py no longer calls eel.start"
    for call in calls:
        mode = next((kw.value for kw in call.keywords if kw.arg == "mode"), None)
        assert isinstance(mode, ast.Constant) and mode.value is None, (
            "eel.start must always be called with mode=None; any other value makes "
            "eel launch a browser of its own, which is the duplicate-tab bug."
        )


# ==========================================================================
#  The control channel's vocabulary  (Part 7)
# ==========================================================================

#: Exactly what the desktop shell may ask of the backend.
EXPECTED_OPERATIONS = {
    "ping", "voice_status", "start_voice_turn", "cancel_voice_turn",
    "data_location", "report_shortcut", "shutdown",
}


def test_the_control_channel_offers_exactly_these_operations(main_module):
    assert set(main_module.DESKTOP_OPERATIONS) == EXPECTED_OPERATIONS, (
        "the desktop control surface changed; every addition is a new way into "
        "the running backend and needs to be a deliberate decision"
    )


def test_no_control_operation_sounds_like_execution(main_module):
    forbidden = re.compile(
        r"exec|spawn|shell|command|eval|run_|_run|file|read|write|path|"
        r"secret|key|token|permission|approve|allow",
        re.I,
    )
    for name in main_module.DESKTOP_OPERATIONS:
        assert not forbidden.search(name), (
            f"'{name}' is not a Nano control operation; the channel must not grow "
            "an execution, filesystem, credential or permission path"
        )


def test_every_declared_operation_is_actually_callable(main_module):
    for name, handler in main_module.DESKTOP_OPERATIONS.items():
        assert callable(handler), f"{name} is not callable"


def test_the_control_channel_never_returns_a_secret(main_module):
    """ping, voice_status and data_location are the read-only ones.

    The check is for secret VALUES, not for the word "secret": data_location
    legitimately names the file secrets.dat when reporting which of Nano's data
    files exist, and naming a file is not disclosing its contents.
    """
    from core.data_migration import MIGRATABLE_FILES

    # Credential shapes: a Groq key, and any long opaque token-like run.
    key_shapes = re.compile(r"gsk_[A-Za-z0-9]{8,}|sk-[A-Za-z0-9]{8,}|[A-Za-z0-9_-]{40,}")

    for name in ("ping", "voice_status", "data_location"):
        payload = json.dumps(main_module.DESKTOP_OPERATIONS[name]({}), default=str)
        # Data-file names are expected output; remove them before looking.
        for filename in MIGRATABLE_FILES:
            payload = payload.replace(filename, "")
        assert not key_shapes.search(payload), name + " returned something credential-shaped"
        for word in ("api_key", "apikey", "password", "bearer", "authorization"):
            assert word not in payload.lower(), name + " leaked " + word


def test_an_unknown_operation_is_refused_by_a_real_bridge(main_module):
    """End to end through the real dispatcher with the real handler table."""
    import io

    from core.desktop_bridge import DesktopBridge, decode

    writer = io.StringIO()
    bridge = DesktopBridge(main_module.DESKTOP_OPERATIONS,
                           reader=io.StringIO(), writer=writer)
    response = bridge.handle({"id": "1", "op": "execute_tool",
                              "args": {"name": "shell", "cmd": "whoami"}})
    assert response["ok"] is False
    assert response["error"] == "unknown_operation"


# ==========================================================================
#  The hotkey starts a voice turn, and only that  (Parts 6 and 8)
# ==========================================================================

@pytest.fixture
def captured_turns(main_module, monkeypatch):
    """Replace the voice turn with a recorder, so no microphone is opened."""
    calls: list[dict] = []

    async def fake_turn(source="ui", **kwargs):
        calls.append({"source": source, **kwargs})
        return {"ok": True, "source": source, "response": "ok"}

    monkeypatch.setattr(main_module.voice_runtime, "run_voice_turn", fake_turn)
    monkeypatch.setattr(main_module.voice_runtime, "turn_status",
                        lambda: {"active": False, "source": None, "phase": "IDLE",
                                 "elapsed_seconds": None})
    return calls


def _settle(main_module, calls, expected, timeout=5.0):
    """Wait for the dispatched coroutines to actually run on the shared loop."""
    import time

    deadline = time.time() + timeout
    while len(calls) < expected and time.time() < deadline:
        time.sleep(0.02)
    return calls


def test_the_hotkey_starts_exactly_one_voice_turn(main_module, captured_turns):
    reply = main_module.DESKTOP_OPERATIONS["start_voice_turn"]({"source": "hotkey"})
    assert reply["accepted"] is True

    _settle(main_module, captured_turns, 1)
    assert len(captured_turns) == 1, "one activation must produce one turn"
    assert captured_turns[0]["source"] == "hotkey"


def test_the_hotkey_acknowledges_immediately_instead_of_waiting(main_module, captured_turns):
    """Blocking here would freeze the whole eel bridge for the length of a turn."""
    import time

    start = time.time()
    main_module.DESKTOP_OPERATIONS["start_voice_turn"]({"source": "hotkey"})
    assert time.time() - start < 1.0


def test_an_unknown_source_is_normalised_to_the_hotkey(main_module, captured_turns):
    main_module.DESKTOP_OPERATIONS["start_voice_turn"]({"source": "../../etc/passwd"})
    _settle(main_module, captured_turns, 1)
    assert captured_turns[0]["source"] == "hotkey", (
        "the source is a label, and only the three known labels are accepted"
    )


def test_a_second_activation_during_a_turn_is_refused(main_module, monkeypatch):
    """The honest busy answer, with no second turn dispatched."""
    dispatched: list[str] = []

    async def fake_turn(source="ui", **_kwargs):
        dispatched.append(source)
        return {"ok": True}

    monkeypatch.setattr(main_module.voice_runtime, "run_voice_turn", fake_turn)
    monkeypatch.setattr(main_module.voice_runtime, "turn_status",
                        lambda: {"active": True, "source": "hotkey",
                                 "phase": "COMMAND_LISTENING", "elapsed_seconds": 1.2})

    reply = main_module.DESKTOP_OPERATIONS["start_voice_turn"]({"source": "hotkey"})

    assert reply["busy"] is True
    assert reply["accepted"] is False
    assert reply["active_source"] == "hotkey"
    assert dispatched == [], "a busy Nano must not dispatch a second turn"


def test_the_hotkey_uses_the_one_authoritative_voice_turn(main_module):
    """Not a second pipeline: the same coroutine the wake phrase and UI use."""
    from core.voice import VoiceRuntime

    assert "hotkey" in VoiceRuntime.TURN_SOURCES
    assert set(VoiceRuntime.TURN_SOURCES) == {"wake_phrase", "hotkey", "ui"}


def test_cancelling_a_turn_leaves_voice_available(main_module, monkeypatch):
    """A cancel must never disable the hotkey for the rest of the session."""
    stopped = []
    monkeypatch.setattr(main_module.voice, "stop_playback", lambda: stopped.append("playback"))
    monkeypatch.setattr(main_module.voice, "shutdown",
                        lambda: pytest.fail("cancel must not shut the voice stack down"))

    reply = main_module.DESKTOP_OPERATIONS["cancel_voice_turn"]({})

    assert stopped == ["playback"]
    assert reply["voiceStillAvailable"] is True


# ==========================================================================
#  Voice phase events reach the overlay  (Part 10)
# ==========================================================================

def test_voice_phase_events_are_pushed_to_the_desktop_shell(main_module, monkeypatch):
    """The overlay is driven by real phases, so they must actually be emitted."""
    emitted: list[tuple[str, dict]] = []

    class FakeBridge:
        running = True
        operations = ()

        def emit(self, event, payload):
            emitted.append((event, payload))

    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", FakeBridge())
    monkeypatch.setattr(main_module, "_notify_ui", lambda *_args: None)

    main_module._emit_voice_phase("COMMAND_LISTENING", "A ouvir comando…")
    main_module._emit_voice_phase("IDLE", "")

    assert emitted == [
        ("voice_phase", {"phase": "COMMAND_LISTENING", "detail": "A ouvir comando…"}),
        ("voice_phase", {"phase": "IDLE", "detail": ""}),
    ]


def test_a_missing_eel_renderer_callback_does_not_block_the_desktop_bridge(main_module, monkeypatch):
    """Regression: a frontend build that exposes nothing must not also drop
    the desktop-shell event.

    Before this was fixed, `_emit_voice_phase` built its eel call as
    `eel.on_voice_phase(...)` directly in the argument list of `_notify_ui`.
    `eel.<name>` only exists once `eel.init()` has scanned a built frontend
    and found a matching `eel.expose(...)` call in it -- exactly the state of
    a clean checkout that has never run `npm run build`. On such a host the
    attribute genuinely does not exist, the AttributeError happened before
    `_notify_ui` was even entered, and `_emit_voice_phase` never reached its
    `_desktop_emit(...)` call at all: the Electron overlay silently lost every
    phase update. This does not monkeypatch `_notify_ui` away, so it exercises
    the real lookup inside it against a real eel module with the attribute
    genuinely absent.
    """
    monkeypatch.delattr(eel_module, "on_voice_phase", raising=False)

    emitted: list[tuple[str, dict]] = []

    class FakeBridge:
        running = True
        operations = ()

        def emit(self, event, payload):
            emitted.append((event, payload))

    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", FakeBridge())

    main_module._emit_voice_phase("COMMAND_LISTENING", "A ouvir comando…")

    assert emitted == [
        ("voice_phase", {"phase": "COMMAND_LISTENING", "detail": "A ouvir comando…"}),
    ]


def test_a_missing_desktop_shell_is_not_an_error(main_module, monkeypatch):
    """Browser mode has no shell; emitting must be a silent no-op."""
    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", None)
    main_module._desktop_emit("voice_phase", {"phase": "IDLE"})


def test_the_overlay_states_cover_every_phase_the_runtime_emits():
    """Both halves must know the same phase vocabulary.

    The overlay maps phases to what the user sees. A phase the runtime emits
    but the overlay has never heard of would leave the panel stuck on the
    previous state -- so the two lists are compared rather than assumed.
    """
    runtime_phases = set(
        re.findall(r'self\._phase\(\s*"([A-Z_]+)"',
                   (REPO_ROOT / "core" / "voice.py").read_text(encoding="utf-8"))
    )
    overlay_source = (ELECTRON_DIR / "lib" / "overlay-state.js").read_text(encoding="utf-8")
    known = set(re.findall(r"^\s{2}([A-Z_]+):", overlay_source, re.M))

    missing = runtime_phases - known
    assert not missing, f"the overlay has no state for these real phases: {sorted(missing)}"


# ==========================================================================
#  Honest shortcut status  (Part 6)
# ==========================================================================

def test_the_shortcut_is_unknown_until_the_shell_reports_it(main_module, monkeypatch):
    monkeypatch.setattr(main_module, "_DESKTOP_BRIDGE", None)
    monkeypatch.setitem(main_module.DESKTOP_STATE, "shortcutRegistered", None)
    monkeypatch.setitem(main_module.DESKTOP_STATE, "present", False)

    status = main_module.get_desktop_status()
    assert status["channel"] is False
    assert status["shortcutRegistered"] is None, (
        "an unregistered shortcut must never be reported as working"
    )


def test_a_shortcut_conflict_is_recorded_verbatim(main_module):
    main_module.DESKTOP_OPERATIONS["report_shortcut"]({
        "shortcut": "Ctrl + Shift + Space",
        "registered": False,
        "error": "Outra aplicação já usa este atalho.",
        "overlay": True,
        "autoLaunch": False,
        "version": "8.1.0",
    })
    status = main_module.get_desktop_status()
    assert status["shortcutRegistered"] is False
    assert status["shortcutError"] == "Outra aplicação já usa este atalho."
    assert status["shortcut"] == "Ctrl + Shift + Space"


# ==========================================================================
#  Wake phrase is experimental and off  (Part 9)
# ==========================================================================

def test_the_wake_phrase_ships_disabled():
    """The global hotkey is the primary activation; the detector is opt-in."""
    import yaml

    config = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert config["voice"]["wake_phrase_enabled"] is False, (
        "wake_phrase_enabled must default to false: it holds the microphone open "
        "and runs Whisper continuously for an activation that is no longer primary"
    )


def test_the_wake_phrase_implementation_is_kept_not_deleted():
    assert (REPO_ROOT / "core" / "wake_phrase.py").exists()
    assert "wake_phrase_enabled" in __import__("core.user_settings", fromlist=["x"]).ALLOWED_KEYS


def test_bare_nano_activation_stays_disabled():
    import yaml

    config = yaml.safe_load((REPO_ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert config["voice"]["wake_phrase_allow_nano_only"] is False
    assert config["voice"]["wake_word"]["enabled"] is False, "legacy ONNX wake word stays off"


# ==========================================================================
#  A UI request must never be the first to load a native extension
# ==========================================================================
#
# The freeze this guards against was reproduced live: opening Settings called
# get_audio_devices -> audio_feedback.output_device_report -> `import pygame`,
# pygame imported numpy, and the main thread parked inside numpy's C extension
# loader. eel serves its entire bridge from one cooperative gevent hub, so that
# did not just make Settings slow -- every call in the process stopped, for
# good. It had been hidden by the wake-phrase detector importing numpy at
# startup; turning that off by default removed the accident covering it up.


def test_prewarm_loads_the_audio_backends_itself():
    """After prewarm, the heavy modules are already in sys.modules."""
    from core import audio_feedback

    result = audio_feedback.prewarm()
    assert isinstance(result, dict)
    assert result.get("pygame") or result.get("pygame_error"), (
        "prewarm must report what happened, not stay silent"
    )
    if result.get("pygame"):
        assert "pygame" in sys.modules
        # numpy is the expensive one, and pygame is what drags it in.
        assert "numpy" in sys.modules


def test_prewarm_never_raises_even_with_no_audio_backend(monkeypatch):
    """A machine with no working audio must still start Nano."""
    import builtins

    from core import audio_feedback

    real_import = builtins.__import__

    def refuse(name, *args, **kwargs):
        if name in ("pygame", "pyaudio"):
            raise ImportError(f"no {name} on this machine")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    result = audio_feedback.prewarm()
    assert result["pygame"] is False
    assert result["pyaudio"] is False
    assert "pygame_error" in result and "pyaudio_error" in result


def test_startup_prewarms_audio_before_eel_starts_serving():
    """Order is the whole point, so it is checked on the AST, not the text."""
    import ast

    tree = ast.parse((REPO_ROOT / "core" / "main.py").read_text(encoding="utf-8"))
    main_fn = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )

    prewarm_line = None
    eel_start_line = None
    for node in ast.walk(main_fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if (isinstance(func, ast.Attribute) and func.attr == "prewarm"
                and isinstance(func.value, ast.Name) and func.value.id == "audio_feedback"):
            prewarm_line = node.lineno
        if (isinstance(func, ast.Attribute) and func.attr == "start"
                and isinstance(func.value, ast.Name) and func.value.id == "eel"):
            eel_start_line = node.lineno

    assert prewarm_line is not None, (
        "main() must call audio_feedback.prewarm(): without it the first "
        "Settings request imports numpy on the eel hub and freezes the whole UI"
    )
    assert eel_start_line is not None
    assert prewarm_line < eel_start_line, (
        "audio_feedback.prewarm() must run BEFORE eel.start(); after it, the "
        "first request can still be the one that pays for the import"
    )


def test_the_settings_payload_still_reports_audio_devices(main_module):
    """The endpoint that triggered the freeze must still do its job."""
    devices = main_module.get_audio_devices()
    assert set(devices) >= {"inputs", "outputs"}
    assert isinstance(devices["inputs"], list)
    assert isinstance(devices["outputs"], list)


# ==========================================================================
#  Assets and launcher
# ==========================================================================

def test_the_application_icon_exists_and_is_a_real_multi_size_icon():
    icon = ASSETS / "icon.ico"
    assert icon.exists(), "electron/assets/icon.ico is missing; run scripts/build_app_icon.ps1"

    raw = icon.read_bytes()
    assert raw[:4] == b"\x00\x00\x01\x00", "not an ICO file"
    count = int.from_bytes(raw[4:6], "little")
    assert count >= 4, f"an application icon needs several sizes, found {count}"

    sizes = {raw[6 + 16 * i] or 256 for i in range(count)}
    assert 256 in sizes, "electron-builder requires a 256x256 frame"
    assert 16 in sizes, "the tray and the taskbar need a 16x16 frame"


def test_the_tray_icon_exists():
    assert (ASSETS / "tray.png").exists()


def test_the_desktop_launcher_is_pure_ascii():
    """cmd.exe reads .bat in the OEM codepage; one UTF-8 byte corrupts parsing."""
    raw = DESKTOP_LAUNCHER.read_bytes()
    try:
        raw.decode("ascii")
    except UnicodeDecodeError as exc:
        context = raw[max(0, exc.start - 40):exc.start + 10]
        pytest.fail(f"NANO_DESKTOP.bat has a non-ASCII byte at {exc.start}: {context!r}")


def test_the_desktop_launcher_uses_crlf_and_has_no_bom():
    raw = DESKTOP_LAUNCHER.read_bytes()
    assert len(re.findall(rb"(?<!\r)\n", raw)) == 0, "batch files need CRLF"
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_the_desktop_launcher_pauses_on_every_failure():
    text = DESKTOP_LAUNCHER.read_text(encoding="ascii")
    assert ":fail" in text and "pause" in text.lower()


def test_the_desktop_launcher_clears_electron_run_as_node():
    """Inheriting it makes the electron binary run as plain Node and die.

    Editors that are themselves Electron apps export ELECTRON_RUN_AS_NODE for
    their child processes; launching Nano from such a terminal would otherwise
    fail with an unexplained "cannot read property 'whenReady' of undefined".
    """
    text = DESKTOP_LAUNCHER.read_text(encoding="ascii")
    assert re.search(r'set\s+"?ELECTRON_RUN_AS_NODE=\s*"?', text), (
        "NANO_DESKTOP.bat must clear ELECTRON_RUN_AS_NODE before starting Electron"
    )


def test_the_browser_launcher_still_exists():
    """NANO.bat remains the development/fallback path and must not be removed."""
    assert (REPO_ROOT / "NANO.bat").exists()


# ==========================================================================
#  Real layout measurement in real Chromium  (Parts 13 and 23)
# ==========================================================================

@pytest.mark.skipif(not ELECTRON_BIN.exists(), reason="the Electron binary is not installed")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "out" / "index.html").exists(),
    reason="frontend/out is not built",
)
def test_the_ui_has_no_horizontal_overflow_at_any_desktop_size():
    """Renders the real production bundle at 1920, 1600, 1366 and 1280 wide.

    The user's report was that the right-hand side felt cut off until the
    browser was zoomed to ~80%. Reading the CSS could not reproduce it, which
    is why this measures the rendered document instead.
    """
    result = subprocess.run(
        [str(ELECTRON_BIN), str(ELECTRON_DIR / "test" / "render-check.js")],
        cwd=str(ELECTRON_DIR), capture_output=True, text=True, timeout=300,
        env=_child_env(),
    )
    assert "{" in result.stdout, f"the render check produced no report:\n{result.stderr[-3000:]}"
    report = json.loads(result.stdout[result.stdout.index("{"):])
    assert report["ok"], report.get("error")

    for row in report["desktop"]:
        assert row["hasApp"], f"{row['viewport']}: the app shell did not render"
        assert row["devicePixelRatio"] == 1 or row["devicePixelRatio"] > 0
        assert not row["horizontalOverflow"], (
            f"{row['viewport']}: the page scrolls horizontally "
            f"({row['docScrollWidth']} > {row['docClientWidth']}). "
            f"Offenders: {row['offenders']}"
        )
        assert not row["offenders"], (
            f"{row['viewport']}: elements stick out past the viewport: {row['offenders']}"
        )
        assert row["stageWidth"] >= 600, (
            f"{row['viewport']}: the stage collapsed to "
            f"{row['stageWidth']}px — the conversation rail is taking priority"
        )
        # A wide stage holding a hairline-thin reading column would still be a
        # squeezed layout, so the column inside it is measured too.
        assert row["readingColumnWidth"] >= 520, (
            f"{row['viewport']}: the reading column is only "
            f"{row['readingColumnWidth']}px wide"
        )
        assert row["composerVisible"], f"{row['viewport']}: the composer fell below the fold"

        # A scrolling flex column whose children can shrink does not scroll when
        # it runs out of room -- it squeezes them, and any child with
        # overflow:hidden loses its last line. That is what cut "Nenhuma tarefa
        # em execucao" in half in the inspector, and it is very likely why the
        # app only looked right at 80% zoom: zooming out made everything fit.
        assert not row["shrinkable"], (
            f"{row['viewport']}: these children of a scrolling flex column can be "
            f"squeezed instead of scrolled: {row['shrinkable']}"
        )
        assert not row["clipped"], (
            f"{row['viewport']}: content is cut off inside these boxes: {row['clipped']}"
        )

    # DRAG REGIONS, measured rather than read off the stylesheet.
    #
    # The redesign made the whole shell draggable so the new exterior margin
    # moves the window like a title bar would. That is only safe if the panels
    # and every control opt back out: a control inside a drag region is not
    # clickable at all, and the failure looks like a button that silently does
    # nothing. Verified by hand at the time (top bar and margin drag the window,
    # the panels do not, and the navigation still switches sections) -- this is
    # what keeps it true.
    for row in report["desktop"]:
        regions = row["dragRegions"]
        where = row["viewport"]
        assert regions["shell"] == "drag", (
            f"{where}: the shell is not a drag region, so the frameless window "
            f"cannot be moved (got {regions['shell']!r})"
        )
        assert regions["app"] == "no-drag", (
            f"{where}: the panels did not opt out of the drag region, so nothing "
            f"inside them is clickable (got {regions['app']!r})"
        )
        for control in ("topnavItem", "statusPill", "windowControl", "railToggle"):
            value = regions[control]
            if value is None:
                continue          # not rendered at this width; nothing to check
            assert value == "no-drag", (
                f"{where}: .{control} is inside the drag region and cannot be "
                f"clicked (got {value!r})"
            )

    # The chat is not the only screen. Ferramentas, PC, Memoria and Definicoes
    # are the densest layouts in the app -- cards, tab strips, meters, tables --
    # and measuring only the conversation left every one of them unchecked.
    assert report["sections"], "the section sweep produced no measurements"
    for row in report["sections"]:
        where = f"{row['viewport']} / {row['section']}"
        assert not row["horizontalOverflow"], (
            f"{where}: the page scrolls horizontally. Offenders: {row['offenders']}"
        )
        assert not row["offenders"], f"{where}: elements stick out past the viewport: {row['offenders']}"
        assert not row["clipped"], f"{where}: content is cut off inside these boxes: {row['clipped']}"
        assert not row["shrinkable"], (
            f"{where}: these children of a scrolling flex column can be squeezed "
            f"instead of scrolled: {row['shrinkable']}"
        )


@pytest.mark.skipif(not ELECTRON_BIN.exists(), reason="the Electron binary is not installed")
@pytest.mark.skipif(
    not (REPO_ROOT / "frontend" / "out" / "index.html").exists(),
    reason="frontend/out is not built",
)
def test_the_same_bundle_still_works_with_no_desktop_shell():
    """Capability detection, checked by actually removing the capability.

    The identical production bundle is loaded with no preload, so
    `window.nanoApp` does not exist. It must render the app and simply omit the
    native title bar -- not throw, and not leave a dead window.
    """
    result = subprocess.run(
        [str(ELECTRON_BIN), str(ELECTRON_DIR / "test" / "render-check.js")],
        cwd=str(ELECTRON_DIR), capture_output=True, text=True, timeout=300,
        env=_child_env(),
    )
    report = json.loads(result.stdout[result.stdout.index("{"):])
    browser = report["browser"]

    assert browser is not None and browser["hasApp"], "the browser fallback did not render"
    # The caption merged into the top bar, so the bar itself renders in both.
    # What must never appear without the desktop shell is the caption cluster.
    assert browser["hasTopBar"] is True, "the top bar did not render in the browser fallback"
    assert browser["hasWindowControls"] is False, (
        "the native window controls must not appear without the desktop shell: "
        "their buttons would do nothing"
    )
    assert not browser["horizontalOverflow"]

    # The eel bridge is genuinely absent in this harness, so its own complaint
    # is expected. Anything else is a real failure of the fallback.
    unexpected = [e for e in browser["errors"] if "eel.js did not load" not in e]
    assert not unexpected, f"the browser fallback logged errors: {unexpected}"
