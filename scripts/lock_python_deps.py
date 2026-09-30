"""Regenerate, or verify, Nano's hashed Python dependency locks.

HUMAN-MAINTAINED INPUT -> GENERATED LOCK -> EVERY INSTALL
---------------------------------------------------------
The requirements*.txt files at the repository root are what people edit:
direct dependencies only, with the ranges Nano is known to work with. Nothing
installs from them directly. This script resolves each one into a lock under
requirements/ -- every transitive package pinned to one exact version, with the
sha256 of every artifact PyPI publishes for that version -- and CI, the packaged
Windows runtime and developer setups all install the locks with
``pip install --require-hashes``.

    requirements-tools.txt    -> requirements/tools.lock     the compiler itself (uv)
    requirements-build.txt    -> requirements/build.lock     pip, setuptools, wheel
    requirements.txt          -> requirements/runtime.lock   what the Windows app ships
    requirements-test.txt     -> requirements/test.lock      the CI test environment
    requirements-optional.txt -> requirements/optional.lock  opt-in capabilities

LAYERED, SO SHARED PACKAGES AGREE
    runtime is resolved with build as a constraint, and test and optional with
    both. A package that appears in two locks therefore has one version in
    both: CI tests the gevent, httpx and groq that ship, rather than whatever
    was newest on the day it ran, and the optional capabilities install on top
    of the runtime without moving any of it.

UNIVERSAL, EXCEPT WHERE IT CANNOT BE
    Every lock keeps its environment markers and is valid on the Windows and
    Linux CI runners alike -- except optional.lock, which is resolved for
    CPython 3.12 on Windows x86-64 only. A universal resolution of that layer
    has no solution: openwakeword requires tflite-runtime on Linux, and
    tflite-runtime publishes no wheel for Python 3.12. The optional
    capabilities are Windows desktop features, and Windows is what it locks.

ANCHORED IN TIME
    Only files uploaded before EXCLUDE_NEWER are candidates. The same inputs
    therefore give the same lock on any machine on any later day: a release
    published tomorrow cannot change it, and neither can a wheel uploaded later
    for a version that is already pinned -- which would otherwise change a hash
    list under an unchanged pin and fail the CI drift check for no reason
    anyone caused.

RESOLVED FROM SCRATCH, EVERY TIME
    The committed locks are never fed back to uv, so a lock is a pure function
    of its manifests, the anchor, EXCLUDE_NEWER_PACKAGE and the uv version --
    and --check can recompute it and compare everything, hashes included. The
    common alternative, regenerating with the old lock as uv's output file,
    cannot: uv keeps the hashes it finds there for every pin it keeps, so a
    hash altered by hand survives regeneration and the check passes. That was
    measured, not assumed. Moving one package is therefore an entry in
    EXCLUDE_NEWER_PACKAGE, and moving everything is moving the anchor.

    Usage, with the pinned compiler installed
    (python -m pip install --require-hashes -r requirements/tools.lock):

        python scripts/lock_python_deps.py            regenerate every lock
        python scripts/lock_python_deps.py --check    exit 1 if any lock differs from a regeneration
"""
from __future__ import annotations

import argparse
import difflib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The resolution anchor: only files uploaded before this instant are
#: candidates. Moving it re-resolves every package to its newest version before
#: the new instant, so it is a deliberate, reviewed refresh, and it stays at
#: least seven days in the past when it moves. A fresh upload is when a broken
#: or malicious release is likeliest to still be live: resolved at the moment
#: this lock was introduced, the optional voice stack would have taken av
#: 19.0.0, a major release published that same day.
EXCLUDE_NEWER = "2026-09-22T00:00:00Z"

#: Per-package cutoffs that override the anchor for one package -- later, for a
#: security fix that cannot wait for a refresh, e.g.
#: {"aiohttp": "2026-10-02T00:00:00Z"}; or earlier, to hold one package back.
#: Each entry appears in the header of every lock, so an exception can never be
#: silent.
EXCLUDE_NEWER_PACKAGE: dict[str, str] = {}

#: The compiler every lock is generated and checked with. It must equal the pin
#: in requirements-tools.txt; tests/test_python_dependency_locks.py holds them
#: together.
UV_VERSION = "0.12.17"

PYTHON_VERSION = "3.12"
INDEX_URL = "https://pypi.org/simple"
WINDOWS_X64 = "x86_64-pc-windows-msvc"
LOCK_DIR = "requirements"


@dataclass(frozen=True)
class Layer:
    name: str
    source: str                           # the human-maintained manifest
    lock: str                             # the lock generated from it
    platform: str | None = None           # None means universal
    constraints: tuple[str, ...] = ()     # earlier locks this one must agree with


TOOLS = Layer("tools", "requirements-tools.txt", "requirements/tools.lock")
BUILD = Layer("build", "requirements-build.txt", "requirements/build.lock")
RUNTIME = Layer("runtime", "requirements.txt", "requirements/runtime.lock",
                constraints=(BUILD.lock,))
TEST = Layer("test", "requirements-test.txt", "requirements/test.lock",
             constraints=(BUILD.lock, RUNTIME.lock))
OPTIONAL = Layer("optional", "requirements-optional.txt", "requirements/optional.lock",
                 platform=WINDOWS_X64, constraints=(BUILD.lock, RUNTIME.lock))

#: Resolution order: every layer comes after the locks it is constrained by.
LAYERS = (TOOLS, BUILD, RUNTIME, TEST, OPTIONAL)


def compile_args(layer: Layer) -> list[str]:
    """Every uv argument that decides the content of ``layer``'s lock."""
    args = ["pip", "compile"]
    args += ["--python-platform", layer.platform] if layer.platform else ["--universal"]
    args += ["--python-version", PYTHON_VERSION, "--exclude-newer", EXCLUDE_NEWER]
    for package, cutoff in sorted(EXCLUDE_NEWER_PACKAGE.items()):
        args += ["--exclude-newer-package", f"{package}={cutoff}"]
    args += ["--generate-hashes", "--index-url", INDEX_URL]
    for constraint in layer.constraints:
        args += ["-c", constraint]
    return [*args, "-o", layer.lock, layer.source]


def header_command(layer: Layer) -> str:
    """The command a lock's header names: this script, and exactly what it ran."""
    return f"python scripts/lock_python_deps.py -> uv {UV_VERSION} {' '.join(compile_args(layer))}"


def _uv_environment() -> dict[str, str]:
    """The caller's environment without any UV_* setting that could steer a resolution.

    uv reads an index URL, a cutoff date, constraints, a header and a dozen
    other inputs from UV_* variables, so one left over in somebody's shell would
    produce a lock nobody else can reproduce. The cache location cannot change a
    result, so it is the one that survives.
    """
    return {key: value for key, value in os.environ.items()
            if not key.upper().startswith("UV_") or key.upper() == "UV_CACHE_DIR"}


def find_uv() -> list[str] | None:
    """The pinned uv -- installed for this interpreter, or on PATH -- or None."""
    candidates = [[sys.executable, "-m", "uv"]]
    on_path = shutil.which("uv")
    if on_path:
        candidates.append([on_path])
    seen = []
    for command in candidates:
        try:
            probe = subprocess.run([*command, "--version"], capture_output=True, text=True,
                                   env=_uv_environment(), check=False)
        except OSError:
            continue
        words = probe.stdout.split()
        if probe.returncode != 0 or len(words) < 2:
            continue
        if words[1] == UV_VERSION:
            return command
        seen.append(f"uv {words[1]} ({' '.join(command)})")
    found = f" Found {', '.join(seen)} instead." if seen else ""
    print(f"lock_python_deps: these locks are generated with uv {UV_VERSION}.{found}\n"
          "Install the pinned compiler with:\n"
          "    python -m pip install --require-hashes -r requirements/tools.lock",
          file=sys.stderr)
    return None


def _mirror(workdir: Path) -> None:
    """Copy every manifest -- and nothing else -- into ``workdir`` at the same paths.

    uv writes the paths it was given into each lock -- in every "# via -r ..."
    and "# via -c ..." line -- so resolution runs from a directory shaped
    exactly like the repository, and a lock regenerated anywhere is identical.
    The committed locks stay out of it on purpose (see the module docstring).
    And because nothing is written back until every layer has resolved, a
    failure halfway through never leaves some locks updated and some not.
    """
    (workdir / LOCK_DIR).mkdir(parents=True, exist_ok=True)
    for layer in LAYERS:
        shutil.copyfile(ROOT / layer.source, workdir / layer.source)


def _resolve(uv: list[str], workdir: Path) -> bool:
    for layer in LAYERS:
        command = [*uv, *compile_args(layer), "--quiet", "--no-config",
                   "--python", sys.executable,
                   "--custom-compile-command", header_command(layer)]
        print(f"  resolving {layer.name:<9}{layer.source} -> {layer.lock}", flush=True)
        if subprocess.run(command, cwd=workdir, env=_uv_environment(), check=False).returncode:
            print(f"lock_python_deps: resolving {layer.source} failed; no lock was written.",
                  file=sys.stderr)
            return False
    return True


def _lines(path: Path) -> list[str]:
    # Compared line by line, so a CRLF checkout of an LF lock is not "drift".
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _report_drift(stale: list[tuple[Layer, list[str], list[str]]], orphans: list[str]) -> int:
    if not stale and not orphans:
        print(f"All {len(LAYERS)} Python locks match their manifests "
              f"(anchor {EXCLUDE_NEWER}, uv {UV_VERSION}).")
        return 0
    for layer, committed, fresh in stale:
        state = "is missing" if not committed else "is stale"
        print(f"\n{layer.lock} {state}: regenerating it from {layer.source} gives a different file.")
        diff = list(difflib.unified_diff(committed, fresh, f"{layer.lock} (committed)",
                                         f"{layer.lock} (regenerated)", lineterm="", n=1))
        for line in diff[:80]:
            print("  " + line)
        if len(diff) > 80:
            print(f"  ... {len(diff) - 80} more lines of difference")
    for orphan in orphans:
        print(f"\n{orphan} is not produced by any layer of scripts/lock_python_deps.py.")
    print("\nRegenerate with `python scripts/lock_python_deps.py` and commit each manifest "
          "together with its lock.")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="regenerate in a scratch directory and fail if any committed lock differs")
    args = parser.parse_args(argv)

    uv = find_uv()
    if uv is None:
        return 2
    with tempfile.TemporaryDirectory(prefix="nano-locks-") as scratch:
        workdir = Path(scratch)
        _mirror(workdir)
        if not _resolve(uv, workdir):
            return 2
        stale = []
        for layer in LAYERS:
            committed, fresh = _lines(ROOT / layer.lock), _lines(workdir / layer.lock)
            if committed != fresh:
                stale.append((layer, committed, fresh))
        produced = {layer.lock for layer in LAYERS}
        orphans = sorted(path.relative_to(ROOT).as_posix()
                         for path in (ROOT / LOCK_DIR).glob("*.lock")
                         if path.relative_to(ROOT).as_posix() not in produced)
        if args.check:
            return _report_drift(stale, orphans)
        (ROOT / LOCK_DIR).mkdir(exist_ok=True)
        for layer, _committed, _fresh in stale:
            shutil.copyfile(workdir / layer.lock, ROOT / layer.lock)
    changed = {layer.name for layer, _committed, _fresh in stale}
    for layer in LAYERS:
        print(f"  {layer.lock:<28}{'updated' if layer.name in changed else 'unchanged'}")
    for orphan in orphans:
        print(f"  {orphan:<28}not produced by any layer -- delete it if it is obsolete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
