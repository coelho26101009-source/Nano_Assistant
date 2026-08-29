"""Finding installed applications, and launching only what was found.

THE SECURITY IDEA, IN ONE LINE: the model never supplies a path.

`app.launch` does not accept an executable. It accepts a NAME, which is
resolved against a catalogue this module builds from the machine itself --
Start Menu shortcuts plus a small fixed table of Windows built-ins. If a name
does not resolve to a catalogue entry, nothing launches. There is therefore no
argument the model can craft that reaches ShellExecuteExW with a path of its
choosing, which is what stops `app.launch` from degenerating into "run this
executable".

Discovery is deliberately cheap and never a disk scan. V2 reads three
sources, all of them maintained by Windows itself: the two Start Menu
trees, the registry's App Paths keys (how ordinary desktop software
announces itself), and the Store execution aliases in
%LOCALAPPDATA%\\Microsoft\\WindowsApps (how packaged apps like Spotify
announce themselves, since they have no shortcut and no plain executable).
Widening DISCOVERY is not widening what may be launched: every launch
target still comes from the machine, and the model still contributes only a
name to match against it.

Alias handling is deliberately dull. "VS Code" -> "Visual Studio Code" is a
fixed synonym; there is no phonetic matching and no edit-distance guessing,
because a fuzzy matcher that can turn a misheard word into the wrong
application is exactly the failure mode the speech benchmark warned about. When
several entries are credible the tool returns them ALL and Nano asks which one.
"""
from __future__ import annotations

import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from core.pc_control import winapi
from core.pc_control.results import MAX_APP_CANDIDATES, PCControlError

logger = logging.getLogger("nano.pc_control.applications")

#: Rebuilt at most this often. The sources change when software is
#: installed, which is rare; scanning on every query would be wasteful.
_CATALOGUE_TTL_SECONDS = 300.0

#: A hard ceiling on each discovery source, so a machine with an unusual
#: registry cannot turn catalogue building into an unbounded walk.
_MAX_REGISTRY_ENTRIES = 400

#: `python3.12`, `idle3`, `pip3` -- version-suffixed aliases for something
#: already in the catalogue under its real name.
_VERSION_SUFFIX = re.compile(r"[0-9]+(\.[0-9]+)*$")

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



# --------------------------------------------------------------------------
#  V2 discovery sources
#
#  Both answer the same question the Start Menu answers -- "what is installed?"
#  -- from data Windows maintains. The model contributes no path to either; it
#  contributes a NAME which is matched against what was found.
# --------------------------------------------------------------------------

#: Substrings that mark a registered executable as plumbing rather than an
#: application: the helpers, servers, installers and per-version aliases that
#: would otherwise fill the catalogue with things nobody asks for by name.
_NOT_AN_APPLICATION = (
    "setup", "install", "uninstall", "update", "helper", "server", "host",
    "service", "elevated", "crashpad", "reporter", "_cli", "shellext",
    "packaging", "adminserver",
)

#: Exact names to drop. Two groups: interpreters and package managers that are
#: not applications a person opens, and -- importantly -- the command
#: interpreters. `cmd` and `powershell` are registered under App Paths, and
#: `app.launch` is not the place to surface them: PC Control's whole premise is
#: that there is no general command line, and a catalogue entry called
#: "powershell" is the beginning of an argument that there is one.
_NOT_AN_APPLICATION_EXACT = frozenset({
    "cmd", "conhost", "regedit", "rundll32", "dllhost", "mshta", "wscript",
    "cscript", "powershell", "pwsh", "bash", "sh", "wsl", "wt",
    "pip", "python", "pythonw", "idle", "winget", "table30", "dfshim",
    "cmmgr32", "fsquirt", "wabmig", "wab", "licensemanagershellext",
})


def _looks_like_an_application(stem: str) -> bool:
    """Whether a registered executable name is something a person asks for.

    Deliberately biased towards DROPPING entries. A missing application surfaces
    as "não encontrei", which the user corrects in one sentence; a catalogue
    full of `XboxPcAppCE` and `pip3.12` makes every ambiguous match worse and
    every candidate list harder to read.
    """
    lowered = stem.casefold()
    if lowered in _NOT_AN_APPLICATION_EXACT:
        return False
    if any(marker in lowered for marker in _NOT_AN_APPLICATION):
        return False
    if _VERSION_SUFFIX.search(lowered):
        return False
    return True


def _app_paths_entries() -> list[AppEntry]:
    """Applications that registered themselves under App Paths.

    The LAUNCH TARGET is the registry KEY NAME (`chrome.exe`), not the path
    stored beside it. ShellExecuteExW resolves App Paths keys itself, so Nano
    hands Windows a name Windows already knows instead of re-typing a path. The
    path is read only to confirm the software is really installed, and to tell
    the user what the entry points at.
    """
    try:
        import winreg
    except ImportError:
        return []

    subkey = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    entries: list[AppEntry] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                count = winreg.QueryInfoKey(key)[0]
                for index in range(min(count, _MAX_REGISTRY_ENTRIES)):
                    try:
                        name = winreg.EnumKey(key, index)
                        with winreg.OpenKey(key, name) as entry:
                            target, _kind = winreg.QueryValueEx(entry, "")
                    except OSError:
                        continue
                    if not name.lower().endswith(".exe") or not target:
                        continue
                    stem = Path(name).stem
                    if not _looks_like_an_application(stem):
                        continue
                    expanded = os.path.expandvars(str(target).strip('"'))
                    try:
                        if not Path(expanded).is_file():
                            continue
                    except OSError:
                        continue
                    entries.append(AppEntry(
                        name=stem, launch_target=name, source="registered_app",
                        resolved_executable=expanded))
        except OSError:
            logger.debug("App Paths hive unavailable", exc_info=True)
    return entries


def _store_alias_entries() -> list[AppEntry]:
    """Microsoft Store applications, through their execution aliases.

    A packaged (UWP) application has no Start Menu shortcut and no ordinary
    executable, which is why V1 could not find Spotify. Windows publishes an
    ALIAS for each one in %LOCALAPPDATA%\\Microsoft\\WindowsApps, and launching
    that alias is the supported way to start the app without resolving an
    AppUserModelID by hand. The alias is a real file this function found on
    disk, so the rule holds: the catalogue supplies the target, the model
    supplies only a name.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return []
    folder = Path(local) / "Microsoft" / "WindowsApps"
    try:
        if not folder.is_dir():
            return []
        candidates = sorted(folder.glob("*.exe"))[:_MAX_REGISTRY_ENTRIES]
    except OSError:
        logger.debug("could not read the Store alias folder", exc_info=True)
        return []

    entries: list[AppEntry] = []
    for alias in candidates:
        if not _looks_like_an_application(alias.stem):
            continue
        entries.append(AppEntry(name=alias.stem, launch_target=str(alias),
                                source="store_app", resolved_executable=str(alias)))
    return entries


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

    # Registered and packaged applications come last, so a Start Menu shortcut
    # -- which carries the name the user actually sees, "Visual Studio Code"
    # rather than "code" -- always wins the name it shares.
    for discovered in (*_app_paths_entries(), *_store_alias_entries()):
        key = _fold(discovered.name)
        if not key or key in seen:
            continue
        seen.add(key)
        entries.append(discovered)

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



# --------------------------------------------------------------------------
#  What is actually running
# --------------------------------------------------------------------------

#: Bound on the running-application list. This answers "what do I have open?",
#: which is a handful of things -- never the process table.
MAX_RUNNING_APPS = 30


def running_applications() -> list[dict]:
    """Applications the user would say are OPEN, i.e. ones with a window.

    NOT the process list. A process table is hundreds of entries, most of them
    services and helpers, and handing that to a model is both useless and a
    small disclosure of everything installed. This walks the same window
    enumeration `window.list` uses and groups it by process, so the answer is
    "Discord, VS Code, Brave" rather than 300 lines.
    """
    from core.pc_control import windows as window_control

    grouped: dict[str, dict] = {}
    for window in window_control.list_windows():
        process = (window.get("process") or "").lower()
        if not process:
            continue
        entry = grouped.setdefault(process, {
            "process": process,
            "windows": 0,
            "titles": [],
            "focused": False,
        })
        entry["windows"] += 1
        if len(entry["titles"]) < 3:
            entry["titles"].append(window["title"])
        entry["focused"] = entry["focused"] or bool(window.get("focused"))
        if len(grouped) >= MAX_RUNNING_APPS:
            break
    return sorted(grouped.values(), key=lambda item: item["process"])


def executable_names_for(entry: AppEntry) -> set[str]:
    """The process names an entry could plausibly be running as.

    Used to connect "Spotify" the catalogue entry to "spotify.exe" the running
    process. Both the resolved executable and the entry's own name are offered,
    because a Start Menu shortcut's display name and its executable often
    differ ("Visual Studio Code" / "Code.exe").
    """
    names: set[str] = set()
    if entry.resolved_executable:
        names.add(Path(entry.resolved_executable).name.lower())
    target = entry.launch_target
    if target and not target.lower().endswith(".lnk"):
        names.add(Path(target).name.lower())
    else:
        resolved = winapi.resolve_shortcut(target) if target else None
        if resolved:
            names.add(Path(resolved).name.lower())
    folded = _fold(entry.name).replace(" ", "")
    if folded:
        names.add(f"{folded}.exe")
    return {name for name in names if name.endswith(".exe")}


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


__all__ = ["MAX_RUNNING_APPS", "AppEntry", "build_catalogue",
           "executable_names_for", "find_by_app_id", "launch", "resolve",
           "running_applications", "running_process_names", "search"]
