"""Shared pytest configuration.

AN ISOLATED NANO PROFILE, ALWAYS
--------------------------------
core/main.py opens its world at IMPORT time: the conversation database, the
task queue, the secret store, user_settings.json, permission policies and the
log all live under ``core.app_paths.DATA_DIR``, which is computed once, when
``core.app_paths`` is first imported, from ``NANO_DATA_DIR``. Without it that
is the developer's real profile (%LOCALAPPDATA%\\NanoAssistant): a bare
``pytest`` migrated, reindexed and wrote into the real database and settings
of whoever ran it.

So this file points ``NANO_DATA_DIR`` at a fresh temporary profile as it is
imported -- which pytest does before it collects, and therefore before it
imports, a single test module. The rules:

* Default: a new directory under ``%TEMP%/nano-pytest-profiles``, one per
  session, removed when the session ends (best effort: Windows keeps files
  that SQLite still holds open, and those go at the next session's pruning).
* An explicit ``NANO_DATA_DIR`` or ``HELIOS_DATA_DIR`` that is NOT the real
  profile is respected: a CI job or a developer that chose a scratch profile
  keeps it.
* One that IS the real profile is overridden. The real profile is never a
  test fixture; there is deliberately no switch to make it one.
* A test that needs a particular directory still sets it itself --
  ``monkeypatch.setenv`` for its own process, an explicit ``env=`` for a
  subprocess -- and child processes inherit the isolated profile otherwise,
  so a spawned backend is isolated too.
* If ``core.app_paths`` was somehow imported before this ran, the session
  stops instead of running against whatever it resolved.

The header of every run names the profile in use.

NO DEVELOPER .env, EVER
-----------------------
Loading the repository ``.env`` is production's way of finding credentials,
and nothing a test needs. It is switched off here, at import, by setting
``NANO_SKIP_DOTENV=1`` -- the switch every loader consults (core.dotenv_gate)
-- before pytest has imported anything that could load it. Setting it in a
fixture would be too late: ``scripts/benchmark_providers.py`` loads the file at
IMPORT, and a test module imports it while pytest is still collecting. That is
exactly how a bare ``pytest`` used to put the developer's real Groq and Mistral
keys into ``os.environ`` before the first test ran, so timing and routing tests
ran against one machine's configuration while CI, which has no ``.env``, ran
another.

* It is unconditional. There is no switch to let a test run read ``.env``.
* It blocks only the implicit file. A test that needs a credential sets a
  fake one itself -- ``monkeypatch.setenv`` for its own process, ``env=`` for a
  subprocess -- and that keeps working, as does anything the invoking shell
  exported on purpose.
* Child processes inherit it, so a spawned backend does not load it either.

ORDER INDEPENDENCE
------------------
core/main.py builds its world at import time -- `brain`, `memory`,
`memory_stack`, `tool_executor`, `permission_manager` and a dozen more are
module-level singletons, and every test in this suite shares the one instance.
A test that leaves one of them altered does not fail; it makes some LATER,
unrelated test fail, and which test that is depends on collection order. That
failure mode is invisible in a suite that only ever runs in one order, which is
what this suite did.

So the order is made changeable, and reproducible:

    pytest --shuffle                 shuffle with a fresh random seed
    pytest --shuffle-seed=12345      shuffle exactly the way seed 12345 does
    pytest                           source order, unchanged

The seed is always printed, and printed again in the failure header, so a
shuffled failure can be replayed exactly rather than described as "it sometimes
fails". Shuffling is OFF by default: a suite that reorders itself on every run
turns one real defect into an intermittent one, and CI pins a seed instead (see
.github/workflows/ci.yml) so the order it exercises is a different one from the
source order but is the same on every run.

This is deliberately eleven lines of hook rather than a dependency on
pytest-randomly. That plugin also reseeds `random` and `numpy` before every
test, which changes what the tests under it actually do -- it would be adding a
second, uncontrolled variable to the very experiment being run here -- and it
would have to be installed in CI to keep the suite reproducible there.
"""
from __future__ import annotations

import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path


# --------------------------------------------------------------------------
#  The isolated profile -- runs at IMPORT, before any test module is imported
# --------------------------------------------------------------------------

_PROFILE_PARENT = Path(tempfile.gettempdir()) / "nano-pytest-profiles"
#: Leftovers older than this belong to no running session and are pruned.
_STALE_PROFILE_SECONDS = 24 * 3600


def _real_profile() -> Path:
    """core.app_paths.default_data_root(), without importing core.app_paths."""
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.getenv("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return (Path(base) / "NanoAssistant").expanduser().resolve()


def _prune_stale_profiles() -> None:
    try:
        entries = list(_PROFILE_PARENT.iterdir())
    except OSError:
        return
    cutoff = time.time() - _STALE_PROFILE_SECONDS
    for entry in entries:
        try:
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
        except OSError:
            continue


def _isolate_nano_profile() -> tuple[Path, bool]:
    """Point Nano at a profile that is not the real one. Returns (dir, created)."""
    if "core.app_paths" in sys.modules:
        raise RuntimeError(
            "core.app_paths was imported before tests/conftest.py could isolate the "
            "Nano profile; refusing to run against whatever DATA_DIR it resolved")
    explicit = os.getenv("NANO_DATA_DIR") or os.getenv("HELIOS_DATA_DIR")
    if explicit and Path(explicit).expanduser().resolve() != _real_profile():
        os.environ["NANO_DATA_DIR"] = str(Path(explicit).expanduser().resolve())
        return Path(os.environ["NANO_DATA_DIR"]), False
    _PROFILE_PARENT.mkdir(parents=True, exist_ok=True)
    _prune_stale_profiles()
    profile = Path(tempfile.mkdtemp(prefix="session-", dir=_PROFILE_PARENT)).resolve()
    os.environ["NANO_DATA_DIR"] = str(profile)
    return profile, True


def _disable_dotenv_loading() -> None:
    """Keep the developer's ``.env`` out of this process and its children.

    Checked against the same import boundary as the profile: every module that
    can load ``.env`` imports ``core.app_paths``, so the guard in
    ``_isolate_nano_profile`` also proves none of them ran before this did.
    """
    os.environ["NANO_SKIP_DOTENV"] = "1"


_disable_dotenv_loading()
NANO_TEST_PROFILE, _PROFILE_CREATED = _isolate_nano_profile()


def pytest_unconfigure(config):
    if _PROFILE_CREATED:
        shutil.rmtree(NANO_TEST_PROFILE, ignore_errors=True)


def pytest_addoption(parser):
    group = parser.getgroup("order")
    group.addoption("--shuffle", action="store_true", default=False,
                    help="shuffle test order to expose order dependencies")
    group.addoption("--shuffle-seed", action="store", type=int, default=None,
                    help="shuffle with this exact seed (implies --shuffle)")


def pytest_collection_modifyitems(session, config, items):
    seed = config.getoption("shuffle_seed")
    if seed is None and not config.getoption("shuffle"):
        return
    if seed is None:
        seed = random.randrange(1 << 31)
    random.Random(seed).shuffle(items)
    config._nano_shuffle_seed = seed


def pytest_report_header(config):
    lines = [f"nano data isolated in {NANO_TEST_PROFILE}"
             + ("" if _PROFILE_CREATED else " (explicit NANO_DATA_DIR)")]
    seed = getattr(config, "_nano_shuffle_seed", None)
    if seed is not None:
        lines.append(f"test order shuffled with --shuffle-seed={seed}")
    return lines


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Say the seed again at the END, where a failing run is actually read."""
    seed = getattr(config, "_nano_shuffle_seed", None)
    if seed is not None:
        terminalreporter.write_line(
            f"test order was shuffled; reproduce with --shuffle-seed={seed}")


# --------------------------------------------------------------------------
#  The real-Chromium tests, addressable as a set
# --------------------------------------------------------------------------
#: Fixtures that spawn Electron and drive the shipped bundle in real Chromium.
#: A test is a Chromium test if it asks for one of these -- which is a fact
#: about the test, not a label someone has to remember to keep in sync.
_CHROMIUM_FIXTURES = {"chat_report", "graph_report", "drive_report", "render_report"}


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "chromium: drives the shipped bundle in real Chromium via an Electron "
        "harness; needs electron/node_modules and a built frontend/out")


def pytest_itemcollected(item):
    """Mark the Chromium tests by the fixtures they request.

    Applied here rather than written onto ~51 test functions by hand, so the
    marker cannot drift away from the thing it describes: ask for the harness
    and you are a Chromium test, in every module, forever.
    """
    if _CHROMIUM_FIXTURES.intersection(getattr(item, "fixturenames", ())):
        item.add_marker("chromium")
