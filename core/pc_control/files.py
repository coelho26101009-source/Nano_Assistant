"""Opening folders, searching for files, and opening documents.

THE LINE THIS MODULE DEFENDS: `file.open` opens a DOCUMENT. It is not a way to
run a program.

Windows makes that distinction easy to lose, because ShellExecuteExW("open")
on `payload.bat` runs the batch file. So the extension is checked against a
deny-list of executable and script types BEFORE the shell is involved, and
those are refused outright in V1 -- not gated, not confirmable, refused. There
is no legitimate V1 request that needs them: launching a real application is
`app.launch`, which resolves against a catalogue of installed software and
never takes a path from the model.

File search is bounded on every axis that can grow: roots, depth, results,
wall-clock time. It returns metadata only, never contents, so a search cannot
become an exfiltration primitive for files the model was not allowed to read.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from core.pc_control import winapi
from core.pc_control.results import MAX_FILE_RESULTS, PCControlError, clamp_text

logger = logging.getLogger("nano.pc_control.files")

#: Extensions Windows would EXECUTE rather than display. Refused by file.open.
#: `.js`, `.jse`, `.wsf` and friends are included because Windows Script Host
#: runs them on a double-click exactly like a batch file.
EXECUTABLE_EXTENSIONS = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js",
    ".jse", ".wsf", ".wsh", ".msi", ".msp", ".scr", ".reg", ".hta", ".cpl",
    ".jar", ".lnk", ".pif", ".gadget", ".application", ".msc", ".inf",
})

#: Known-folder names the user can say out loud, mapped to the environment
#: rather than to a hard-coded username.
_KNOWN_FOLDERS: dict[str, tuple[str, ...]] = {
    "downloads": ("Downloads",),
    "transferências": ("Downloads",),
    "transferencias": ("Downloads",),
    "documents": ("Documents",),
    "documentos": ("Documents",),
    "desktop": ("Desktop",),
    "ambiente de trabalho": ("Desktop",),
    "pictures": ("Pictures",),
    "imagens": ("Pictures",),
    "music": ("Music",),
    "música": ("Music",),
    "musica": ("Music",),
    "videos": ("Videos",),
    "vídeos": ("Videos",),
}

#: Where file.search looks when the user does not say otherwise. Deliberately
#: three user folders, never C:\.
DEFAULT_SEARCH_FOLDERS = ("Desktop", "Documents", "Downloads")

MAX_SEARCH_SECONDS = 8.0
MAX_SEARCH_DEPTH = 6
MAX_SEARCH_ENTRIES = 40_000

#: Directory names never worth descending into: huge, machine-generated, and
#: in the case of the last few, none of Nano's business.
_SKIP_DIRECTORIES = frozenset({
    "node_modules", "__pycache__", ".git", ".svn", ".hg", "venv", ".venv",
    "site-packages", "appdata", "$recycle.bin", "system volume information",
    "windows", "program files", "program files (x86)", "programdata",
    ".ssh", ".aws", ".gnupg", "nanoassistant",
})


def home() -> Path:
    return Path(os.path.expanduser("~"))


def known_folder(name: str) -> Path | None:
    """Resolve a spoken folder name through the user's own profile."""
    key = str(name or "").strip().casefold()
    parts = _KNOWN_FOLDERS.get(key)
    if not parts:
        return None
    candidate = home().joinpath(*parts)
    return candidate if candidate.is_dir() else None


def _protected(path: Path) -> bool:
    """Whether a path is somewhere PC Control must not wander.

    The location half of this question is answered by
    `core.execution_scope.is_protected_location`, which is the single source of
    truth and is also applied centrally to every `path` argument before a
    handler runs. This wrapper adds the two things that are specific to being
    called from a PC tool:

    * an UNRESOLVABLE path is protected -- failing closed on a path we cannot
      even name is the only safe answer, and it is the reason this is not just
      a direct call;
    * it is reachable for arguments that never go through central path
      resolution, such as a known-folder NAME.
    """
    from core.execution_scope import is_protected_location

    try:
        resolved = path.resolve()
    except OSError:
        return True
    return is_protected_location(resolved)


#: The public name. `_protected` predates PC Control V2 and is kept as the
#: internal spelling used inside this module.
is_protected = _protected


def resolve_folder(value: str) -> Path:
    """A known folder name or an explicit directory path, validated."""
    text = str(value or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar a pasta.")

    resolved = known_folder(text)
    if resolved is None:
        candidate = Path(os.path.expandvars(text)).expanduser()
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            raise PCControlError("invalid_input", "Esse caminho não é válido.") from exc

    if not resolved.exists():
        raise PCControlError("not_found", f"A pasta '{text}' não existe.")
    if not resolved.is_dir():
        raise PCControlError("not_a_directory", f"'{text}' não é uma pasta.")
    if _protected(resolved):
        raise PCControlError("protected_path",
                             "Essa pasta está protegida e o Nano não a abre.")
    return resolved


def open_folder(value: str) -> dict:
    """Show a folder in File Explorer. Never creates anything."""
    resolved = resolve_folder(value)
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Só funciona no Windows.")
    try:
        winapi.shell_execute(str(resolved), verb="open")
    except OSError as exc:
        raise PCControlError("open_failed", "O Explorador não conseguiu abrir a pasta.") from exc
    return {"path": str(resolved), "name": resolved.name or str(resolved)}


def _search_roots(roots: list[str] | None) -> list[Path]:
    if not roots:
        found = [home() / name for name in DEFAULT_SEARCH_FOLDERS]
        return [p for p in found if p.is_dir()]
    resolved: list[Path] = []
    for value in roots[:5]:
        resolved.append(resolve_folder(str(value)))
    return resolved


def search_files(query: str, *, roots: list[str] | None = None,
                 max_results: int = 20) -> dict:
    """Find files by name under bounded roots. Metadata only, never contents."""
    needle = str(query or "").strip().casefold()
    if not needle:
        raise PCControlError("invalid_input", "É preciso dizer o que procurar.")

    limit = max(1, min(int(max_results or 20), MAX_FILE_RESULTS))
    started = time.monotonic()
    results: list[dict] = []
    scanned = 0
    truncated = False
    timed_out = False

    for root in _search_roots(roots):
        if len(results) >= limit or timed_out:
            break
        root_depth = len(root.parts)
        # os.walk with followlinks=False: reparse points and junction loops are
        # simply not followed, so a symlink cycle cannot spin this forever.
        for current, directories, filenames in os.walk(root, followlinks=False):
            if time.monotonic() - started > MAX_SEARCH_SECONDS:
                timed_out = True
                break
            current_path = Path(current)
            if len(current_path.parts) - root_depth >= MAX_SEARCH_DEPTH:
                directories[:] = []
                continue
            directories[:] = [d for d in directories
                              if d.casefold() not in _SKIP_DIRECTORIES and not d.startswith("$")]

            for filename in filenames:
                scanned += 1
                if scanned > MAX_SEARCH_ENTRIES:
                    truncated = True
                    break
                if needle not in filename.casefold():
                    continue
                path = current_path / filename
                if _protected(path):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                results.append({
                    "path": clamp_text(str(path), 400),
                    "filename": clamp_text(filename, 200),
                    "extension": path.suffix.lower(),
                    "size_bytes": int(stat.st_size),
                    "modified": time.strftime("%Y-%m-%d %H:%M",
                                              time.localtime(stat.st_mtime)),
                })
                if len(results) >= limit:
                    truncated = True
                    break
            if truncated or len(results) >= limit:
                break

    return {
        "query": clamp_text(query, 200),
        "results": results,
        "count": len(results),
        "truncated": truncated or timed_out,
        "timed_out": timed_out,
        "roots": [str(r) for r in _search_roots(roots)],
        "elapsed_seconds": round(time.monotonic() - started, 2),
    }


def classify_file(path: Path) -> str:
    """"executable" for anything Windows would run, otherwise "document"."""
    return "executable" if path.suffix.lower() in EXECUTABLE_EXTENSIONS else "document"


def open_file(value: str) -> dict:
    """Open an existing document in its default application.

    Executable and script types are REFUSED. This is the whole reason the tool
    can be low-risk: `file.open` can never become "run this", so a
    mis-transcribed sentence cannot turn into code execution.
    """
    text = str(value or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar o ficheiro.")

    try:
        path = Path(os.path.expandvars(text)).expanduser().resolve()
    except OSError as exc:
        raise PCControlError("invalid_input", "Esse caminho não é válido.") from exc

    if not path.exists():
        raise PCControlError("not_found", f"O ficheiro '{text}' não existe.")
    if path.is_dir():
        raise PCControlError("is_a_directory",
                             "Isso é uma pasta. Para abrir uma pasta usa a ferramenta de pastas.")
    if _protected(path):
        raise PCControlError("protected_path", "Esse ficheiro está protegido.")

    kind = classify_file(path)
    if kind == "executable":
        raise PCControlError(
            "executable_refused",
            f"'{path.name}' é um ficheiro executável ou script. O Nano não abre "
            "esse tipo de ficheiro. Para abrir uma aplicação instalada, pede pelo nome.",
            extension=path.suffix.lower())

    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "Só funciona no Windows.")
    try:
        winapi.shell_execute(str(path), verb="open")
    except OSError as exc:
        raise PCControlError("open_failed",
                             f"O Windows não conseguiu abrir '{path.name}'.") from exc

    return {"path": str(path), "filename": path.name, "extension": path.suffix.lower(),
            "kind": kind}


__all__ = ["DEFAULT_SEARCH_FOLDERS", "EXECUTABLE_EXTENSIONS", "MAX_SEARCH_DEPTH",
           "MAX_SEARCH_SECONDS", "classify_file", "home", "is_protected",
           "known_folder", "open_file", "open_folder", "resolve_folder",
           "search_files"]
