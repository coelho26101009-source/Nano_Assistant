"""Finding installed applications, and launching only what was found.

THE SECURITY IDEA, IN ONE LINE: the model never supplies a path.

`app.launch` does not accept an executable. It accepts a NAME, which is
resolved against a catalogue this module builds from the machine itself --
Start Menu shortcuts plus a small fixed table of Windows built-ins. If a name
does not resolve to a catalogue entry, nothing launches. There is therefore no
argument the model can craft that reaches ShellExecuteExW with a path of its
choosing, which is what stops `app.launch` from degenerating into "run this
executable".

Discovery is deliberately cheap: two Start Menu trees, never a disk scan.

Alias handling is deliberately dull. "VS Code" -> "Visual Studio Code" is a
fixed synonym; there is no phonetic matching and no edit-distance guessing,
because a fuzzy matcher that can turn a misheard word into the wrong
application is exactly the failure mode the speech benchmark warned about. When
several entries are credible the tool returns them ALL and Nano asks which one.
"""
from __future__ import annotations

import logging
import os
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from core.pc_control import winapi
from core.pc_control.results import MAX_APP_CANDIDATES, PCControlError

logger = logging.getLogger("nano.pc_control.applications")

#: Rebuilt at most this often. Start Menu contents change when software is
#: installed, which is rare; scanning on every query would be wasteful.
_CATALOGUE_TTL_SECONDS = 300.0

#: Windows inbox applications have no Start Menu shortcut on this machine
#: (Calculator and Notepad are packaged apps), so they are declared here as a
#: FIXED table. These strings are constants in source, never anything the model
#: supplied, which is what keeps them safe to pass to the shell.
_BUILTIN_APPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Calculadora", "calc.exe", ("calculator", "calc", "calculadora")),
    ("Bloco de Notas", "notepad.exe", ("notepad", "bloco de notas", "bloco")),
    ("Explorador de Ficheiros", "explorer.exe",
     ("file explorer", "explorer", "explorador", "explorador de ficheiros",
      "windows explorer", "gestor de ficheiros")),
    ("Paint", "mspaint.exe", ("paint", "mspaint")),
    ("Definições do Windows", "ms-settings:", ("settings", "definicoes", "definições",
                                               "windows settings", "configuracoes")),
)

#: Conservative, hand-written synonyms. Every entry is an EXACT alternative
#: name for one product -- never a sound-alike, never a prefix guess.
_ALIASES: dict[str, str] = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "vs": "visual studio",
    "chrome": "google chrome",
    "explorador de ficheiros": "file explorer",
    "navegador": "brave",
}


def _fold(text: str) -> str:
    """Lowercase, accent-free comparison form.

    Accents are folded HERE and only here, for matching an application name the
    user spoke. This is not the transcription-scoring path, where accents carry
    real information; "definicoes" and "definições" are the same program.
    """
    decomposed = unicodedata.normalize("NFD", str(text or "").strip().lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@dataclass(frozen=True)
class AppEntry:
    name: str
    launch_target: str          # a .lnk path, or a fixed built-in command
    source: str                 # start_menu_user | start_menu_system | builtin
    resolved_executable: str | None = None

    def as_dict(self, confidence: float | None = None) -> dict:
        payload = {
            "name": self.name,
            "app_id": self.app_id,
            "source": self.source,
            "executable": self.resolved_executable,
        }
        if confidence is not None:
            payload["confidence"] = round(confidence, 2)
        return payload

    @property
    def app_id(self) -> str:
        """Stable identity handed back to the model.

        Deliberately NOT a filesystem path: it is a name the catalogue can
        resolve again. A model that echoes an app_id back cannot smuggle a path
        through it, because the path is looked up, never taken.
        """
        return f"{self.source}:{self.name}"


_catalogue: list[AppEntry] = []
_catalogue_built_at: float = 0.0


def _start_menu_roots() -> list[tuple[Path, str]]:
    """The two Start Menu trees, from the environment -- never a hard-coded user."""
    roots: list[tuple[Path, str]] = []
    appdata = os.environ.get("APPDATA")
    program_data = os.environ.get("ProgramData")
    if appdata:
        roots.append((Path(appdata) / "Microsoft/Windows/Start Menu/Programs", "start_menu_user"))
    if program_data:
        roots.append((Path(program_data) / "Microsoft/Windows/Start Menu/Programs", "start_menu_system"))
    return roots


def build_catalogue(force: bool = False) -> list[AppEntry]:
    """Enumerate installed applications. Bounded, cached, no disk scan."""
    global _catalogue, _catalogue_built_at
    now = time.monotonic()
    if _catalogue and not force and (now - _catalogue_built_at) < _CATALOGUE_TTL_SECONDS:
        return _catalogue

    entries: list[AppEntry] = []
    seen: set[str] = set()

    for name, target, _aliases in _BUILTIN_APPS:
        entries.append(AppEntry(name=name, launch_target=target, source="builtin",
                                resolved_executable=target))
        seen.add(_fold(name))

    for root, source in _start_menu_roots():
        if not root.exists():
            continue
        try:
            shortcuts = sorted(root.rglob("*.lnk"))
        except OSError:
            logger.debug("could not read start menu root %s", root, exc_info=True)
            continue
        for shortcut in shortcuts:
            name = shortcut.stem
            key = _fold(name)
            if not key or key in seen:
                continue
            seen.add(key)
            entries.append(AppEntry(name=name, launch_target=str(shortcut), source=source))

    _catalogue = entries
    _catalogue_built_at = now
    logger.info("PC control app catalogue: %d entries", len(entries))
    return entries


def _score(query: str, entry: AppEntry) -> float:
    """How credible this entry is for the query. Exactness only, never sound.

    1.0  exact name match, or a declared built-in alias
    0.9  the query is a whole leading word of the name ("brave" -> "Brave Browser")
    0.6  the query appears as a whole word inside the name
    0.0  no match -- and 0.0 means the entry is not returned at all

    There is no substring-anywhere rule and no fuzzy distance. "apaga" must
    never score against "Paint".
    """
    folded_query = _fold(query)
    folded_name = _fold(entry.name)
    if not folded_query:
        return 0.0
    if folded_query == folded_name:
        return 1.0

    if entry.source == "builtin":
        for name, _target, aliases in _BUILTIN_APPS:
            if _fold(name) == folded_name and folded_query in {_fold(a) for a in aliases}:
                return 1.0

    name_words = folded_name.split()
    query_words = folded_query.split()
    if not query_words:
        return 0.0
    if name_words[:len(query_words)] == query_words:
        return 0.9
    # Whole-word containment, in order.
    for index in range(len(name_words) - len(query_words) + 1):
        if name_words[index:index + len(query_words)] == query_words:
            return 0.6
    return 0.0


def search(query: str, *, limit: int = MAX_APP_CANDIDATES) -> list[tuple[AppEntry, float]]:
    """Credible catalogue matches for a spoken or typed application name."""
    text = str(query or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso dizer o nome da aplicação.")

    canonical = _ALIASES.get(_fold(text), text)
    scored = [(entry, _score(canonical, entry)) for entry in build_catalogue()]
    matches = [(entry, score) for entry, score in scored if score > 0.0]
    matches.sort(key=lambda pair: (-pair[1], pair[0].name.lower()))
    return matches[:limit]


def resolve(query: str) -> tuple[AppEntry | None, list[tuple[AppEntry, float]]]:
    """Resolve to exactly one application, or report the ambiguity.

    A single top-scoring candidate wins. Two candidates tied at the top are
    AMBIGUOUS and neither is chosen -- Nano asks. Silently picking the
    alphabetically-first of two equally good matches is how the wrong program
    gets launched.
    """
    matches = search(query)
    if not matches:
        return None, []
    best = matches[0][1]
    tied = [pair for pair in matches if pair[1] == best]
    if len(tied) == 1:
        return tied[0][0], matches
    return None, matches


def find_by_app_id(app_id: str) -> AppEntry | None:
    """Look an app_id back up in the catalogue. The id is a key, never a path."""
    wanted = str(app_id or "").strip()
    for entry in build_catalogue():
        if entry.app_id == wanted:
            return entry
    return None


def running_process_names() -> set[str]:
    try:
        import psutil
    except ImportError:
        return set()
    names: set[str] = set()
    for process in psutil.process_iter(["name"]):
        name = (process.info.get("name") or "").lower()
        if name:
            names.add(name)
    return names


def launch(entry: AppEntry) -> dict:
    """Start one catalogue entry and REPORT WHAT WINDOWS ACTUALLY DID.

    ShellExecuteExW gets the target as a typed field, so nothing is parsed as a
    command line. The returned PID is then used to say something true: a
    process id means it really started, and its absence is reported as such
    rather than being narrated as success.
    """
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "O controlo de PC só funciona no Windows.")

    executable = entry.resolved_executable
    if entry.launch_target.lower().endswith(".lnk"):
        if not Path(entry.launch_target).exists():
            raise PCControlError("not_found",
                                 f"O atalho de '{entry.name}' já não existe.")
        executable = executable or winapi.resolve_shortcut(entry.launch_target)

    before = running_process_names()
    try:
        pid = winapi.shell_execute(entry.launch_target)
    except OSError as exc:
        logger.warning("launch failed for %s: %s", entry.name, exc)
        raise PCControlError("launch_failed",
                             f"O Windows não conseguiu abrir '{entry.name}'.") from exc

    executable_name = Path(executable).name.lower() if executable else ""
    already_running = bool(executable_name and executable_name in before)

    return {
        "pid": pid or None,
        "already_running": already_running,
        "executable": executable,
    }


__all__ = ["AppEntry", "build_catalogue", "find_by_app_id", "launch", "resolve",
           "running_process_names", "search"]
