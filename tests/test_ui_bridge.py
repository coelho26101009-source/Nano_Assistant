"""Regression tests for the Python -> UI callback bridge.

The failure these guard against was silent and total: eel discovered ZERO
JavaScript callbacks, so every `eel.on_xxx()` call from Python raised
AttributeError. Streaming chat never rendered, wake events never reached the
UI, and permission dialogs could not be shown (the request was auto-denied).

Two independent causes were found, and both are covered here:

1. The registration calls lived inside the Next.js bundle, where the production
   minifier rewrote them and destroyed the token eel scans for.
2. After moving them to a static file, a *documentation comment* in that file
   mentioned the registration token. Eel's parser scans from the first textual
   occurrence, so the mention made it misparse and return zero functions for
   the whole file.

Cause 2 is the nasty one: the file looked correct, the calls were real, and the
result was still zero. These tests assert the observable outcome (eel actually
registers the callbacks) rather than the shape of the source.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FRONTEND = REPO_ROOT / "frontend"
BRIDGE_SOURCE = FRONTEND / "public" / "nano_bridge.js"
BUILD_OUTPUT = FRONTEND / "out"

# Every callback Python calls on the UI. Kept explicit: adding an eel.on_xxx()
# call in main.py without a matching stub is exactly the bug this file catches.
REQUIRED_CALLBACKS = {
    "on_stream_start",
    "on_stream_status",
    "on_stream_chunk",
    "on_stream_end",
    "on_confirm_request",
    "on_wake_detected",
    "on_voice_exchange",
}


def test_bridge_source_exists():
    assert BRIDGE_SOURCE.exists(), (
        "frontend/public/nano_bridge.js is missing. Without it eel registers no "
        "callbacks and the UI goes silent."
    )


def test_bridge_registers_every_callback_python_calls():
    source = BRIDGE_SOURCE.read_text(encoding="utf-8")
    registered = set(re.findall(r"eel\.expose\(\s*\w+\s*,\s*[\"'](\w+)[\"']", source))
    missing = REQUIRED_CALLBACKS - registered
    assert not missing, f"nano_bridge.js does not register: {sorted(missing)}"


def test_bridge_comments_never_mention_the_registration_token():
    """A mention inside a comment silently zeroes the whole file's parse."""
    source = BRIDGE_SOURCE.read_text(encoding="utf-8")
    comments = re.findall(r"/\*.*?\*/|//[^\n]*", source, flags=re.S)
    offenders = [c for c in comments if "eel.expose(" in c]
    assert not offenders, (
        "A comment in nano_bridge.js contains the literal registration token. "
        "Eel's parser scans from the first textual occurrence, so this makes it "
        "misparse and register ZERO callbacks."
    )


@pytest.mark.skipif(not (BUILD_OUTPUT / "index.html").exists(), reason="frontend not built")
def test_bridge_is_served_verbatim_after_build():
    """The file must reach frontend/out unminified for eel to scan it."""
    built = BUILD_OUTPUT / "nano_bridge.js"
    assert built.exists(), "nano_bridge.js was not copied into frontend/out"
    assert "eel.expose(" in built.read_text(encoding="utf-8"), (
        "the built nano_bridge.js lost its registration calls"
    )


@pytest.mark.skipif(not (BUILD_OUTPUT / "index.html").exists(), reason="frontend not built")
def test_eel_actually_discovers_the_callbacks_in_the_built_frontend():
    """The real assertion: what eel ends up with after scanning the build.

    This is the check that would have caught the original bug. Everything above
    can pass while this still returns an empty set.
    """
    import eel

    eel.init(str(BUILD_OUTPUT))
    discovered = set(eel._js_functions)

    assert discovered, (
        "eel discovered NO JavaScript callbacks in frontend/out. Python cannot "
        "reach the UI: streaming, wake events and permission dialogs are all dead."
    )
    missing = REQUIRED_CALLBACKS - discovered
    assert not missing, f"eel did not discover: {sorted(missing)}"

    # And the proxies Python actually calls must exist as attributes.
    for name in sorted(REQUIRED_CALLBACKS):
        assert hasattr(eel, name), f"eel.{name} is missing; calling it would raise AttributeError"


def test_app_shell_loads_the_bridge_after_eel():
    """Order matters: nano_bridge.js needs the eel global to already exist."""
    app_source = (FRONTEND / "pages" / "_app.tsx").read_text(encoding="utf-8")
    assert "/eel.js" in app_source
    assert "/nano_bridge.js" in app_source
    assert app_source.index("/eel.js") < app_source.index("/nano_bridge.js"), (
        "nano_bridge.js must be loaded after eel.js"
    )


def test_every_eel_callback_used_by_main_has_a_bridge_stub():
    """Catch a new eel.on_xxx() in main.py that nobody registered in the UI."""
    main_source = (REPO_ROOT / "core" / "main.py").read_text(encoding="utf-8")
    used = set(re.findall(r"\beel\.(on_\w+)\s*\(", main_source))
    bridge_source = BRIDGE_SOURCE.read_text(encoding="utf-8")
    registered = set(re.findall(r"eel\.expose\(\s*\w+\s*,\s*[\"'](\w+)[\"']", bridge_source))

    missing = used - registered
    assert not missing, (
        f"core/main.py calls {sorted(missing)} but nano_bridge.js does not register "
        "them; those calls would raise AttributeError at runtime."
    )
