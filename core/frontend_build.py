"""Decide whether the built frontend is still current.

`NANO.bat` used to rebuild only when ``frontend/out/index.html`` was missing.
That kept startup fast but meant any edit to a component or stylesheet was
silently ignored: the launcher happily served a stale bundle, and the UI you
tested was not the UI you had just written.

Rebuilding unconditionally is not the answer either -- `npm run build` costs
about a minute, on every single launch.

So the launcher asks this module instead. It records a stamp describing the
newest frontend source at build time; if any watched source is newer than the
stamp, the build is stale and must be redone. Otherwise startup skips npm
entirely.

Used from the launcher as::

    python -m core.frontend_build check     # exit 0 = current, 1 = stale
    python -m core.frontend_build stamp     # record the current sources
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BUILD_DIR = FRONTEND / "out"
INDEX = BUILD_DIR / "index.html"
STAMP = BUILD_DIR / ".nano-build-stamp.json"

# Directories whose contents affect the built output.
WATCHED_DIRS = ("pages", "components", "lib", "styles", "public", "hooks")

# Individual files that change the build.
WATCHED_FILES = (
    "package.json", "package-lock.json", "next.config.js", "next.config.mjs",
    "next.config.ts", "tsconfig.json", "postcss.config.js", "tailwind.config.js",
    "tailwind.config.ts",
)

# Never walked: huge, generated, or the build output itself.
IGNORED_DIRS = {"node_modules", "out", ".next", ".git", "__pycache__"}

# Source extensions that matter. A stray .log in components/ must not force a
# rebuild, and neither must an editor swap file.
SOURCE_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss",
                   ".json", ".html", ".svg", ".png", ".jpg", ".webp", ".ico", ".woff2"}


def _iter_sources(frontend: Path | None = None):
    base = frontend or FRONTEND
    for name in WATCHED_FILES:
        path = base / name
        if path.is_file():
            yield path
    for name in WATCHED_DIRS:
        directory = base / name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if any(part in IGNORED_DIRS for part in path.parts):
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES:
                yield path


def newest_source(frontend: Path | None = None) -> float:
    """Modification time of the most recently changed frontend source."""
    newest = 0.0
    for path in _iter_sources(frontend):
        try:
            newest = max(newest, path.stat().st_mtime)
        except OSError:
            continue
    return newest


def read_stamp(stamp: Path | None = None) -> float:
    path = stamp or STAMP
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return float(data.get("newest_source", 0.0))
    except Exception:
        return 0.0


def write_stamp(stamp: Path | None = None, frontend: Path | None = None) -> float:
    """Record the current newest-source time next to the build output."""
    path = stamp or STAMP
    newest = newest_source(frontend)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"newest_source": newest}), encoding="utf-8")
    except OSError:
        pass
    return newest


def is_stale(frontend: Path | None = None, *, index: Path | None = None,
             stamp: Path | None = None) -> bool:
    """True when the frontend must be rebuilt before it is served."""
    index_path = index or INDEX
    if not index_path.is_file():
        return True                      # never built
    recorded = read_stamp(stamp)
    if recorded <= 0.0:
        # Built by an older launcher that left no stamp. Compare against the
        # build output itself so an existing good build is not thrown away.
        try:
            recorded = index_path.stat().st_mtime
        except OSError:
            return True
    # A one second slack absorbs filesystem timestamp granularity.
    return newest_source(frontend) > (recorded + 1.0)


def _main(argv: list[str]) -> int:
    command = (argv[1] if len(argv) > 1 else "check").strip().lower()
    if command == "stamp":
        write_stamp()
        return 0
    # "check": exit 0 when the build is current, 1 when it is stale, so the
    # launcher can branch on ERRORLEVEL without parsing any output.
    return 1 if is_stale() else 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
