"""The built UI must run under the desktop window's Content Security Policy.

Measured on 2026-11-18: after the Next.js 16 upgrade, `next build` switched to
Turbopack, which writes an inline bootstrap `<script>` into index.html. The
Electron window enforces `script-src 'self'`, so that script was blocked, the
app never started and the desktop window stayed black -- while the same page
in an ordinary browser (no CSP) worked. The build is pinned to webpack, which
emits only same-origin `<script src>` tags.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def test_the_frontend_build_is_pinned_to_webpack():
    scripts = json.loads((FRONTEND / "package.json").read_text(encoding="utf-8"))["scripts"]
    assert "--webpack" in scripts["build"], (
        "next build without --webpack uses Turbopack, whose inline bootstrap "
        "script is blocked by the desktop CSP (black window)"
    )


def _executable_inline_scripts(html: str) -> list[str]:
    found = []
    for attrs, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, flags=re.S | re.I):
        if re.search(r"\bsrc\s*=", attrs, re.I):
            continue
        type_match = re.search(r"\btype\s*=\s*[\"']?([^\"'\s>]+)", attrs, re.I)
        if type_match and type_match.group(1).lower() not in ("text/javascript", "module"):
            continue  # application/json and friends are data, never executed
        if body.strip():
            found.append(body.strip()[:120])
    return found


def test_the_detector_catches_the_turbopack_bootstrap():
    html = ('<script id="__NEXT_DATA__" type="application/json">{}</script>'
            '<script src="/a.js" defer=""></script>'
            '<script>(globalThis["TURBOPACK"] || []).push({})</script>')
    assert len(_executable_inline_scripts(html)) == 1


def test_the_built_page_has_no_inline_script():
    index = FRONTEND / "out" / "index.html"
    if not index.exists():
        pytest.skip("frontend/out is not built on this host")
    inline = _executable_inline_scripts(index.read_text(encoding="utf-8"))
    assert inline == [], f"inline scripts the desktop CSP will block: {inline}"


def test_a_build_the_csp_would_block_is_always_rebuilt(tmp_path):
    """Copied or unzipped files keep old mtimes, so a stale Turbopack build
    looked "current" to the timestamp check and kept the window black."""
    from core import frontend_build

    frontend = tmp_path / "frontend"
    out = frontend / "out"
    out.mkdir(parents=True)
    (frontend / "package.json").write_text("{}", encoding="utf-8")
    index = out / "index.html"
    stamp = out / ".nano-build-stamp.json"

    index.write_text('<script src="/a.js"></script>'
                     '<script id="__NEXT_DATA__" type="application/json">{}</script>',
                     encoding="utf-8")
    frontend_build.write_stamp(stamp, frontend)
    assert not frontend_build.is_stale(frontend, index=index, stamp=stamp)

    index.write_text('<script>(globalThis["TURBOPACK"] || []).push({})</script>', encoding="utf-8")
    assert frontend_build.is_stale(frontend, index=index, stamp=stamp)
