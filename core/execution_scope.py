"""Central filesystem scope and path validation authority for Nano.

Every filesystem target reaching a tool handler is resolved and classified here,
exactly once, before any policy decision is made. Plugins must never validate
paths themselves: the executor rewrites tool arguments with the resolved value
returned by this module, so a handler only ever receives an approved path.

Scopes
------
current_workspace  the Nano project root (the repository / installed app root)
current_project    Nano's own persistent data directory
explicit_target    a real path outside both, but inside the user's home or temp
system             anywhere else, including OS directories

Only ``current_workspace`` is eligible for autonomous access. Everything else is
raised to the policy engine as an explicit target that requires a decision.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.app_paths import DATA_DIR, ROOT


class Scope(str, Enum):
    CURRENT_WORKSPACE = "current_workspace"
    CURRENT_PROJECT = "current_project"
    EXPLICIT_TARGET = "explicit_target"
    SYSTEM = "system"


class PathValidationError(ValueError):
    """Raised when a path cannot be resolved to a safe, classifiable target."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}:{detail}" if detail else code)


# Files that must never be read or written autonomously even though they live
# inside the workspace. Secrets, the permission store itself, and state DBs.
_PROTECTED_NAMES = frozenset({
    ".env",
    ".env.local",
    "permission_policies.json",
    "helios.db",
    "nano_tasks.db",
})

_PROTECTED_SUFFIXES = (".pem", ".key", ".pfx", ".p12", ".db", ".sqlite3")

_PROTECTED_DIR_PARTS = frozenset({".git", ".ssh", ".aws", ".gnupg", ".config/gcloud"})

# Directory NAMES that are sensitive wherever they appear in a path.
_PROTECTED_DIRECTORY_NAMES = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".azure", ".docker",
    "$recycle.bin", "system volume information",
})

# Sub-trees of the user profile holding credentials, keys, or code Windows runs
# on its own. Relative to the home directory, matched case-blind.
#
# Startup is here for a reason that is not obvious: no tool can create an
# executable today, but a folder Windows runs at every login turns "create a
# file" into "run this at every boot" the moment an extension policy is ever
# relaxed. Making the LOCATION unreachable is cheaper than relying on the
# extension check forever.
_PROTECTED_PROFILE_SUBTREES = (
    "AppData/Roaming/Microsoft/Crypto",
    "AppData/Roaming/Microsoft/Protect",
    "AppData/Roaming/Microsoft/Windows/Start Menu/Programs/Startup",
    "AppData/Local/Google/Chrome/User Data",
    "AppData/Local/Microsoft/Edge/User Data",
    "AppData/Local/BraveSoftware",
    "AppData/Roaming/Mozilla/Firefox/Profiles",
    "AppData/Roaming/Opera Software",
)


def is_protected_location(resolved: Path) -> bool:
    """Whether a resolved path is somewhere Nano must never write or remove.

    THE SINGLE SOURCE OF TRUTH for this question. `core.pc_control.files`
    consults it, and so does `_is_protected` below, so the central path
    authority and the PC-control layer cannot disagree about what is off
    limits -- which they did until PC Control V2, when only the PC layer knew
    about Windows, Program Files and the browser profiles.

    Covers: OS directories, Program Files, ProgramData, Nano's own application
    and data directories, credential stores, browser profiles, the Startup
    folder, and a bare drive root.
    """
    lowered = str(resolved).casefold()

    for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        root = os.environ.get(variable)
        if root and lowered.startswith(str(Path(root)).casefold()):
            return True

    # Nano must not be able to rewrite or recycle itself.
    for own in (DATA_DIR, ROOT):
        try:
            if lowered.startswith(str(Path(own).resolve()).casefold()):
                return True
        except OSError:
            continue

    # "recycle C:\" is never a request to honour.
    if resolved.parent == resolved:
        return True

    if any(part.casefold() in _PROTECTED_DIRECTORY_NAMES for part in resolved.parts):
        return True

    try:
        home = Path.home()
    except (OSError, RuntimeError):
        return False
    for subtree in _PROTECTED_PROFILE_SUBTREES:
        if lowered.startswith(str(home.joinpath(*subtree.split("/"))).casefold()):
            return True
    return False


# Windows device names that must never be opened as files.
_WINDOWS_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


@dataclass(frozen=True)
class ResolvedTarget:
    """A path that has been fully resolved and classified."""

    path: Path
    scope: Scope
    protected: bool
    exists: bool

    @property
    def scope_value(self) -> str:
        return self.scope.value

    def as_dict(self) -> dict:
        return {
            "path": str(self.path),
            "scope": self.scope.value,
            "protected": self.protected,
            "exists": self.exists,
        }


def workspace_root() -> Path:
    """The Nano project root. Overridable for tests via NANO_WORKSPACE_ROOT."""
    configured = os.getenv("NANO_WORKSPACE_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    try:
        return Path(ROOT).resolve()
    except OSError:
        return Path.cwd().resolve()


def data_root() -> Path:
    try:
        return Path(DATA_DIR).resolve()
    except OSError:
        return Path(DATA_DIR)


def _is_relative_to(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _is_protected(resolved: Path) -> bool:
    name = resolved.name.lower()
    if name in _PROTECTED_NAMES:
        return True
    if any(name.endswith(suffix) for suffix in _PROTECTED_SUFFIXES):
        return True
    lowered_parts = {part.lower() for part in resolved.parts}
    if lowered_parts & _PROTECTED_DIR_PARTS:
        return True
    return is_protected_location(resolved)


def classify_path(resolved: Path) -> Scope:
    """Classify an already-resolved absolute path into an execution scope."""
    workspace = workspace_root()
    data = data_root()

    # Nano's own data directory can live inside the workspace in dev setups;
    # check it first so its stricter classification wins.
    if _is_relative_to(resolved, data):
        return Scope.CURRENT_PROJECT
    if _is_relative_to(resolved, workspace):
        return Scope.CURRENT_WORKSPACE

    try:
        home = Path.home().resolve()
    except (OSError, RuntimeError):
        home = None
    try:
        import tempfile

        temp = Path(tempfile.gettempdir()).resolve()
    except OSError:
        temp = None

    if temp is not None and _is_relative_to(resolved, temp):
        return Scope.EXPLICIT_TARGET
    if home is not None and _is_relative_to(resolved, home):
        return Scope.EXPLICIT_TARGET
    return Scope.SYSTEM


def _reject_dangerous_syntax(raw: str) -> None:
    if "\x00" in raw:
        raise PathValidationError("path_null_byte")

    # Reject traversal in the literal input before any resolution. Checking the
    # raw text as well as the resolved path stops a symlinked parent from
    # silently absorbing a "..".
    normalized = raw.replace("\\", "/")
    if any(segment == ".." for segment in normalized.split("/")):
        raise PathValidationError("path_traversal_blocked")

    if sys.platform == "win32":
        # Device paths and UNC shares bypass normal drive semantics entirely.
        if raw.startswith(("\\\\?\\", "\\\\.\\", "//?/", "//./")):
            raise PathValidationError("device_path_blocked")
        if raw.startswith(("\\\\", "//")) and not raw.startswith(("///", "\\\\\\")):
            raise PathValidationError("unc_path_blocked")
        stem = Path(normalized).name.split(".")[0].lower()
        if stem in _WINDOWS_RESERVED:
            raise PathValidationError("reserved_device_name", stem)


def resolve_target(
    value: str | os.PathLike | None,
    *,
    base: str | os.PathLike | None = None,
    must_exist: bool = False,
) -> ResolvedTarget:
    """Resolve and classify a filesystem target.

    Relative paths resolve against ``base`` (the workspace root by default).
    Symlinks and Windows junctions are followed by ``Path.resolve`` before
    classification, so a link inside the workspace pointing outside it is
    classified by where it actually lands, not by where it sits.
    """
    if value is None:
        raise PathValidationError("path_required")
    raw = str(value).strip()
    if not raw:
        raise PathValidationError("path_required")

    _reject_dangerous_syntax(raw)

    candidate = Path(raw)
    root = Path(base).expanduser() if base is not None else workspace_root()
    if not candidate.is_absolute():
        candidate = root / candidate

    try:
        resolved = candidate.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise PathValidationError("path_unresolvable", str(exc)) from exc

    exists = resolved.exists()
    if must_exist and not exists:
        raise PathValidationError("path_not_found", str(resolved))

    return ResolvedTarget(
        path=resolved,
        scope=classify_path(resolved),
        protected=_is_protected(resolved),
        exists=exists,
    )


def is_within_workspace(value: str | os.PathLike | None) -> bool:
    """Convenience predicate used by tests and by the project test runner."""
    try:
        target = resolve_target(value)
    except PathValidationError:
        return False
    return target.scope == Scope.CURRENT_WORKSPACE and not target.protected
