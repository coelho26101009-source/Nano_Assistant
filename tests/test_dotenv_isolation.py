"""A test run never loads the developer's .env -- and production still does.

WHAT WENT WRONG
---------------
``scripts/benchmark_providers.py`` loads ``ROOT/.env`` at import, so the
benchmark sees the credentials the product sees.
``tests/test_benchmark_prompt_parity.py`` imports it, so every bare ``pytest``
put the developer's real Groq and Mistral keys into ``os.environ`` while it was
still COLLECTING -- before the first test ran, and past ``NANO_SKIP_DOTENV``,
which only core.main consulted. core.main, imported later by some test, then
built its Brain from them. Locally the suite ran against one machine's
credentials; in CI, which has no ``.env``, against none.

HOW THESE TESTS KNOW, WITHOUT THE DEVELOPER'S .env
--------------------------------------------------
The modules that load ``.env`` are copied, unchanged, into a temporary project
next to a synthetic ``.env`` of sentinel values, and run there: under a bare
``pytest`` the way a developer runs it, and as plain Python the way production
runs them. The plain runs are the control: they prove the copied loaders DO
read that file, so its absence under pytest is the isolation and not a broken
setup. Children report booleans only -- never a value -- and get none of this
process's isolation switches, which would make the pytest run pass for the
wrong reason.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core import dotenv_gate, secret_store

ROOT = Path(__file__).resolve().parent.parent

#: The synthetic project .env. Nothing here is, or resembles, a working key.
SENTINELS = {
    "GROQ_API_KEY": "gsk_dotenv_isolation_sentinel_groq",
    "MISTRAL_API_KEY": "dotenv-isolation-sentinel-mistral",
    "NANO_DOTENV_ISOLATION_SENTINEL": "loaded-from-the-project-dotenv",
}
#: What the pytest child's shell exports on purpose; it must still arrive.
EXPLICIT_MISTRAL = "explicitly-exported-fake-mistral"

#: Every variable that would change what a child loads, or where from.
_SCRUBBED = ("NANO_SKIP_DOTENV", "NANO_DATA_DIR", "HELIOS_DATA_DIR", "NANO_APP_ROOT",
             "HELIOS_APP_ROOT", "PYTHONPATH", "NANO_API_KEY", "HELIOS_API_KEY",
             "NANO_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY",
             "NANO_MISTRAL_API_KEY", *SENTINELS)

_PROBE = '''\
"""Import the named modules, then say which sentinels reached os.environ."""
import importlib, json, os, sys
from pathlib import Path

sentinels = json.loads(os.environ["PROBE_SENTINELS"])
for module in sys.argv[2:]:
    importlib.import_module(module)
report = {
    "from_copy": Path(sys.modules["core"].__path__[0]).resolve().parent == Path.cwd().resolve(),
    "loaded": {name: os.environ.get(name) == value for name, value in sentinels.items()},
}
main = sys.modules.get("core.main")
if main is not None:
    from core import secret_store
    report["groq_secret_from_dotenv"] = (
        secret_store.get_secret("groq_api_key") == sentinels["GROQ_API_KEY"])
    report["mistral_secret_from_dotenv"] = (
        secret_store.get_secret("mistral_api_key") == sentinels["MISTRAL_API_KEY"])
    report["brain_groq_enabled"] = bool(main.brain.groq_enabled)
    report["brain_mistral_enabled"] = bool(main.brain.mistral_enabled)
Path(sys.argv[1]).write_text(json.dumps(report), encoding="utf-8")
'''

_PYTEST_PROBE = '''\
"""What a bare pytest run lets through, reported by a test inside it."""
import json, os
from pathlib import Path

SENTINELS = json.loads(os.environ["PROBE_SENTINELS"])


def _loaded():
    return {name: os.environ.get(name) == value for name, value in SENTINELS.items()}


# The import that leaked: this module loads the project .env at import, and
# pytest imports this file while it is still collecting.
from scripts import benchmark_providers  # noqa: E402,F401

AT_COLLECTION = _loaded()


def test_report(monkeypatch, tmp_path):
    import core.main as main
    from core import secret_store

    after_main = _loaded()
    brain_groq = bool(main.brain.groq_enabled)
    explicit = os.environ.get("MISTRAL_API_KEY") == os.environ["PROBE_EXPLICIT_MISTRAL"]
    monkeypatch.setattr(secret_store, "_STORE_PATH", tmp_path / "secrets.dat")
    monkeypatch.setenv("GROQ_API_KEY", "monkeypatched-fake-groq")
    Path(os.environ["PROBE_REPORT"]).write_text(json.dumps({
        "from_copy": Path(main.__file__).resolve().parents[1] == Path.cwd().resolve(),
        "skip_flag": os.environ.get("NANO_SKIP_DOTENV"),
        "at_collection": AT_COLLECTION,
        "after_main": after_main,
        "brain_groq_enabled": brain_groq,
        "explicit_env_arrived": explicit,
        "monkeypatched_secret_arrived":
            secret_store.get_secret("groq_api_key") == "monkeypatched-fake-groq",
    }), encoding="utf-8")
'''


# ---------------------------------------------------------------------------
#  This process
# ---------------------------------------------------------------------------

def test_this_run_has_dotenv_loading_switched_off():
    assert os.environ.get("NANO_SKIP_DOTENV") == "1"
    assert dotenv_gate.dotenv_allowed() is False


@pytest.mark.parametrize("frozen,flag,allowed", [
    (False, None, True), (False, "0", True), (False, "", True),
    (False, "1", False), (True, None, False),
])
def test_the_gate(monkeypatch, frozen, flag, allowed):
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    if flag is None:
        monkeypatch.delenv("NANO_SKIP_DOTENV", raising=False)
    else:
        monkeypatch.setenv("NANO_SKIP_DOTENV", flag)
    assert dotenv_gate.dotenv_allowed() is allowed


def test_a_credential_a_test_sets_itself_still_reaches_the_code(monkeypatch, tmp_path):
    """Only the implicit file is blocked; monkeypatch.setenv works as ever."""
    monkeypatch.setattr(secret_store, "_STORE_PATH", tmp_path / "secrets.dat")
    for name in ("NANO_API_KEY", "HELIOS_API_KEY", "NANO_MISTRAL_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "test-set-fake-groq")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-set-fake-mistral")
    assert secret_store.get_secret("groq_api_key") == "test-set-fake-groq"
    assert secret_store.get_secret("mistral_api_key") == "test-set-fake-mistral"


# ---------------------------------------------------------------------------
#  A synthetic project, run as a developer and as production would run it
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def project(tmp_path_factory):
    """The real loaders, copied as they are, beside a synthetic .env."""
    base = tmp_path_factory.mktemp("dotenv-project")
    for name in ("core", "scripts", "config", "plugins"):
        shutil.copytree(ROOT / name, base / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    (base / "tests").mkdir()
    shutil.copy2(ROOT / "pytest.ini", base / "pytest.ini")
    shutil.copy2(ROOT / "tests" / "conftest.py", base / "tests" / "conftest.py")
    (base / "tests" / "test_probe_dotenv.py").write_text(_PYTEST_PROBE, encoding="utf-8")
    (base / "probe.py").write_text(_PROBE, encoding="utf-8")
    (base / ".env").write_text(
        "".join(f"{name}={value}\n" for name, value in SENTINELS.items()), encoding="utf-8")
    return base


def _child_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """A clean child: none of this run's switches, and a throwaway default profile."""
    env = {k: v for k, v in os.environ.items() if k not in _SCRUBBED}
    local, roaming = tmp_path / "Local", tmp_path / "Roaming"
    local.mkdir(exist_ok=True)
    roaming.mkdir(exist_ok=True)
    # The default profile, so core.main's gate would be open -- but never the
    # real one: every place data_migration looks is inside tmp_path too.
    env.update(LOCALAPPDATA=str(local), APPDATA=str(roaming), XDG_DATA_HOME=str(local),
               PROBE_SENTINELS=json.dumps(SENTINELS), PYTHONIOENCODING="utf-8")
    env.update(extra)
    return env


def _probe(project: Path, tmp_path: Path, *modules: str, **extra: str) -> dict:
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, "probe.py", str(report), *modules], cwd=str(project),
        env=_child_env(tmp_path, **extra), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=180)
    assert result.returncode == 0, result.stderr[-3000:]
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["from_copy"], "the probe imported a Nano other than the synthetic project"
    return seen


def test_a_bare_pytest_run_never_loads_the_project_dotenv(project, tmp_path):
    report = tmp_path / "pytest-report.json"
    env = _child_env(tmp_path, PROBE_REPORT=str(report),
                     PROBE_EXPLICIT_MISTRAL=EXPLICIT_MISTRAL, MISTRAL_API_KEY=EXPLICIT_MISTRAL)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q",
         "tests/test_probe_dotenv.py"],
        cwd=str(project), env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=300)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-2000:]
    seen = json.loads(report.read_text(encoding="utf-8"))
    assert seen["from_copy"], "the pytest run imported a Nano other than the synthetic project"
    nothing = {name: False for name in SENTINELS}
    assert seen["at_collection"] == nothing, "collection loaded the project .env"
    assert seen["after_main"] == nothing, "importing core.main loaded the project .env"
    assert seen["brain_groq_enabled"] is False, "the Brain was built from the project .env"
    assert seen["skip_flag"] == "1"
    # What the run was GIVEN still arrives: the shell's export, and a test's own.
    assert seen["explicit_env_arrived"] is True
    assert seen["monkeypatched_secret_arrived"] is True


def test_outside_pytest_the_benchmark_still_loads_the_project_dotenv(project, tmp_path):
    """The control: the copied loader reads that file, and production is unchanged."""
    loaded = _probe(project, tmp_path, "scripts.benchmark_providers")["loaded"]
    assert loaded == {name: True for name in SENTINELS}


def test_outside_pytest_nano_skip_dotenv_still_switches_the_benchmark_off(project, tmp_path):
    loaded = _probe(project, tmp_path, "scripts.benchmark_providers",
                    NANO_SKIP_DOTENV="1")["loaded"]
    assert loaded == {name: False for name in SENTINELS}


def test_outside_pytest_the_application_still_loads_the_project_dotenv(project, tmp_path):
    """core.main on its default profile: the keys load and configure the Brain."""
    seen = _probe(project, tmp_path, "core.main")
    assert seen["loaded"] == {name: True for name in SENTINELS}
    assert seen["groq_secret_from_dotenv"] and seen["mistral_secret_from_dotenv"]
    assert seen["brain_groq_enabled"] and seen["brain_mistral_enabled"]


@pytest.mark.parametrize("boundary", ["skip_flag", "explicit_profile"])
def test_outside_pytest_the_application_keeps_its_isolation_boundaries(project, tmp_path,
                                                                       boundary):
    extra = ({"NANO_SKIP_DOTENV": "1"} if boundary == "skip_flag"
             else {"NANO_DATA_DIR": str(tmp_path / "explicit-profile")})
    seen = _probe(project, tmp_path, "core.main", **extra)
    assert seen["loaded"] == {name: False for name in SENTINELS}
    assert not seen["brain_groq_enabled"] and not seen["brain_mistral_enabled"]
