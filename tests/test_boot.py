"""Boot regression tests.

The application once failed to start because ``stop_voice`` was decorated with
``@eel.expose`` twice and eel asserts that each exposed name is unique. Nothing
in the suite imported the entry point, so 78 tests passed against an
unbootable application. These tests import it for real.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN_PY = REPO_ROOT / "core" / "main.py"


def test_core_main_imports_in_a_clean_interpreter():
    """The exact check the Windows build workflow runs."""
    result = subprocess.run(
        [sys.executable, "-c", "import core.main; print('CORE MAIN IMPORT OK')"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, f"import core.main failed:\n{result.stdout}\n{result.stderr}"
    assert "CORE MAIN IMPORT OK" in result.stdout


def test_no_duplicate_eel_exposed_names():
    """Two @eel.expose functions sharing a name make eel assert at import."""
    tree = ast.parse(MAIN_PY.read_text(encoding="utf-8"))
    exposed: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            target = decorator.func if isinstance(decorator, ast.Call) else decorator
            if isinstance(target, ast.Attribute) and target.attr == "expose":
                exposed.append(node.name)

    duplicates = {name for name in exposed if exposed.count(name) > 1}
    assert not duplicates, f"duplicated @eel.expose names: {sorted(duplicates)}"
    assert "stop_voice" in exposed


def test_importing_plugins_starts_no_background_threads():
    """Importing a plugin must not leave a daemon thread running."""
    code = (
        "import threading;"
        "from core.plugin_loader import load_all_plugins;"
        "from core.app_paths import PLUGINS_DIR;"
        "load_all_plugins(PLUGINS_DIR);"
        "names=[t.name for t in threading.enumerate()];"
        "print('THREADS=' + ','.join(names))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, result.stderr
    threads = result.stdout.strip().rsplit("THREADS=", 1)[-1]
    assert "reminders" not in threads
    assert "system-monitor" not in threads


def test_main_exposes_the_emergency_stop_controls():
    """The kill switch must remain reachable from the bridge."""
    source = MAIN_PY.read_text(encoding="utf-8")
    for name in ("set_emergency_stop", "get_emergency_stop_state", "get_provider_health"):
        assert f"def {name}(" in source, f"{name} is no longer exposed"


def test_voice_module_imports_io():
    """AudioInputProvider.capture uses io.BytesIO; the import was missing."""
    tree = ast.parse((REPO_ROOT / "core" / "voice.py").read_text(encoding="utf-8-sig"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
    assert "io" in imported


def test_no_python2_subprocess_attribute_remains():
    """subprocess.mswindows was removed in Python 3 and always raised.

    Checked against the parsed tree rather than the raw text, so a comment
    explaining the old bug does not count as the bug.
    """
    assert not hasattr(subprocess, "mswindows")

    tree = ast.parse((REPO_ROOT / "core" / "tool_execution.py").read_text(encoding="utf-8"))
    offenders = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and node.attr == "mswindows"
        and isinstance(node.value, ast.Name)
        and node.value.id == "subprocess"
    ]
    assert not offenders, "subprocess.mswindows is still referenced in code"
