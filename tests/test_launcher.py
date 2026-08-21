"""Regression tests for the Windows launcher.

The bug these exist to prevent: NANO.bat opened a console window that closed
again within a second, with no error visible, so Nano could not be started at
all. The cause was encoding. cmd.exe parses .bat files using the OEM codepage
(850/437 on this machine), not UTF-8. The launcher had been written as UTF-8
containing box-drawing characters and accented Portuguese, so cmd decoded the
bytes as mojibake, the parser desynchronised, and the script died on a syntax
error before it could report anything.

The failure was invisible to the previous test pass because that pass ran
`python core\\main.py` directly and never executed the .bat at all. These tests
check the launcher file itself.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "NANO.bat"


def test_launcher_exists():
    assert LAUNCHER.exists(), "NANO.bat is the only supported entry point and is missing"


def test_launcher_is_pure_ascii():
    """The bug itself: a single non-ASCII byte can corrupt cmd.exe parsing."""
    raw = LAUNCHER.read_bytes()
    try:
        raw.decode("ascii")
    except UnicodeDecodeError as exc:
        context = raw[max(0, exc.start - 40):exc.start + 10]
        pytest.fail(
            f"NANO.bat contains a non-ASCII byte at offset {exc.start}. "
            f"cmd.exe reads .bat in the OEM codepage, so this corrupts parsing "
            f"and the window closes instantly. Context: {context!r}"
        )


def test_launcher_uses_crlf_line_endings():
    """LF-only batch files misbehave around goto labels and blocks."""
    raw = LAUNCHER.read_bytes()
    lone_lf = len(re.findall(rb"(?<!\r)\n", raw))
    assert lone_lf == 0, f"NANO.bat has {lone_lf} LF-only line endings; batch needs CRLF"


def test_launcher_has_no_utf8_bom():
    """A BOM is echoed as stray characters and breaks the first command."""
    assert not LAUNCHER.read_bytes().startswith(b"\xef\xbb\xbf")


def test_launcher_never_exits_without_pausing():
    """Every failure path must hold the window open so the user can read it."""
    text = LAUNCHER.read_text(encoding="ascii")
    assert "pause" in text.lower(), "NANO.bat must pause so errors stay readable"

    # Every 'exit /b' should be preceded by a pause somewhere; the simplest
    # robust check is that the failure label exists and pauses.
    assert ":fail" in text, "expected a :fail label that pauses before exiting"
    fail_block = text.split(":fail", 1)[1]
    assert "pause" in fail_block.lower(), "the :fail path must pause"


def _executable_lines(path: Path) -> str:
    """Launcher text with REM comments and :: remarks stripped.

    Checking raw text made a test fail on its own explanatory comment, which is
    the same trap that made the eel bridge silently register nothing.
    """
    lines = []
    for line in path.read_text(encoding="ascii").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("rem ") or stripped.startswith("::"):
            continue
        lines.append(line)
    return "\n".join(lines).lower()


def test_launcher_does_not_open_a_browser():
    """Only core/main.py may open the UI, or two tabs appear again."""
    text = _executable_lines(LAUNCHER)
    for token in ("start http", "explorer http", "chrome.exe", "msedge"):
        assert token not in text, f"NANO.bat must not open a browser itself (found {token!r})"


def test_launcher_does_not_start_ollama_itself():
    """Ollama startup belongs to core.ollama_service, which reuses a running one."""
    text = _executable_lines(LAUNCHER)
    for token in ("ollama serve", "ollama.exe", "ollama app"):
        assert token not in text, (
            f"NANO.bat must not launch Ollama itself (found {token!r}); "
            "core/main.py does it and avoids duplicate servers"
        )


def test_no_competing_public_launchers_in_root():
    """NANO.bat must be the single obvious entry point."""
    root_bats = {path.name for path in REPO_ROOT.glob("*.bat")}
    assert root_bats == {"NANO.bat"}, (
        f"expected only NANO.bat in the project root, found: {sorted(root_bats)}"
    )


@pytest.mark.parametrize("script", sorted((REPO_ROOT / "scripts").rglob("*.bat")))
def test_shipped_batch_scripts_are_ascii(script: Path):
    """Same encoding rule for any other batch file we ship."""
    try:
        script.read_bytes().decode("ascii")
    except UnicodeDecodeError as exc:
        pytest.fail(f"{script.relative_to(REPO_ROOT)} is not ASCII at offset {exc.start}")


def test_logs_are_written_outside_the_repository_root():
    """Runtime logs must not reappear as untracked clutter in the root."""
    from core.logger import LOG_PATH

    assert LOG_PATH.parent.name == "logs", "logs belong in the gitignored logs/ directory"
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "logs/" in gitignore


# ------------------------------------------------- cmd.exe block parsing

def _uncommented_lines() -> list[tuple[int, str]]:
    """Numbered launcher lines with REM/:: comments removed."""
    out = []
    for number, line in enumerate(LAUNCHER.read_text(encoding="ascii").splitlines(), 1):
        stripped = line.strip()
        if stripped.upper().startswith("REM") or stripped.startswith("::"):
            continue
        out.append((number, stripped))
    return out


def test_no_echo_inside_a_block_has_unescaped_parentheses():
    """An unescaped ( or ) in an echo ends the enclosing IF block early.

    This was real: `echo BUILDING (first run, this takes a minute)` closed the
    `if not exist frontend\out\index.html` block on its own line, so the npm
    build that followed ran on EVERY launch instead of only the first, adding
    about a minute to every startup. Escaped as ^( and ^) it behaves.
    """
    depth = 0
    offenders = []
    for number, line in _uncommented_lines():
        if depth > 0 and line.lower().startswith("echo"):
            argument = line[4:]
            if re.search(r"(?<!\^)[()]", argument):
                offenders.append(f"line {number}: {line}")
        if re.search(r"\(\s*$", line) and not line.lower().startswith("echo"):
            depth += 1
        if line == ")" or line.startswith(") else"):
            depth = max(0, depth - 1)

    assert not offenders, (
        "these echo lines sit inside a block with unescaped parentheses, "
        "which ends the block early: " + "; ".join(offenders)
    )


def test_parentheses_are_balanced():
    """A stray paren is a syntax error that closes the window instantly."""
    code = "\n".join(line for _, line in _uncommented_lines())
    code = re.sub(r"\^[()]", "", code)          # escaped parens are literals
    code = re.sub(r'"[^"\n]*"', '""', code)     # quoted strings are literals
    assert code.count("(") == code.count(")"), "unbalanced parentheses in NANO.bat"


def test_captured_command_output_uses_call_so_quoting_survives():
    """`for /f ('"%VAR%" -c ...')` silently captures nothing.

    cmd strips the outer quote pair of the whole in-clause, so the command
    never runs and the launcher printed `Python ........ OK ()` followed by a
    bare `ECHO is off.`. Prefixing with `call` keeps the quotes intact, which
    also lets an interpreter path containing spaces work.
    """
    for number, line in _uncommented_lines():
        match = re.search(r"for\s+/f[^(]*\(\s*'(.*)'\s*\)", line, re.I)
        if not match:
            continue
        command = match.group(1).strip()
        if command.startswith('"'):
            pytest.fail(
                f"line {number} captures a quoted command without `call`, which "
                f"returns nothing: {line}"
            )


def test_the_launcher_only_builds_the_frontend_when_it_is_missing():
    """A rebuild on every launch is a minute of dead time per start."""
    code = LAUNCHER.read_text(encoding="ascii")
    build = re.search(r"npm run build", code)
    assert build, "the launcher can no longer build the frontend at all"
    guard = code[:build.start()]
    assert "if not exist" in guard and "index.html" in guard, (
        "npm run build is not guarded by a check for an existing build"
    )
