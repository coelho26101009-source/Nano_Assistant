"""One-time, conservative migration of Nano's user data into the canonical directory.

THE PROBLEM THIS SOLVES
-----------------------
``core.app_paths.data_root()`` resolves to ``%LOCALAPPDATA%\\NanoAssistant``,
and that string is already stable and correct. The divergence is not a
path-resolution bug -- it happens one layer lower.

Nano is commonly launched with the **Microsoft Store** build of Python. Store
apps run inside a container that transparently REDIRECTS writes under
``%LOCALAPPDATA%`` into a per-package cache::

    Nano asks for : C:\\Users\\<you>\\AppData\\Local\\NanoAssistant
    Windows writes: C:\\Users\\<you>\\AppData\\Local\\Packages
                    \\PythonSoftwareFoundation.Python.3.12_<hash>
                    \\LocalCache\\Local\\NanoAssistant

``os.getenv("LOCALAPPDATA")`` still reports the real path, so Nano cannot see
that this happened. Launch the same Nano under a normal Python, the packaged
runtime, or Electron -- none of which are containerised -- and the canonical
directory is empty: the stored API key, the permission policies, the settings,
the task queue and the conversation database all appear to have vanished, and
Nano looks freshly installed.

Electron adds a third location of its own by setting ``NANO_DATA_DIR`` to its
``userData`` folder under ``%APPDATA%``.

THE RULES THIS MODULE FOLLOWS
-----------------------------
Migration is a rescue operation, not a synchronisation, so it is deliberately
timid:

* **Never overwrite.** A file is copied only when nothing exists at the
  destination. Newer destination data is therefore impossible to clobber --
  there is no comparison to get wrong.
* **Never delete the source.** The old directory is left exactly as it was. If
  anything here is wrong, the user's data is still in both places.
* **Never migrate onto a populated destination.** If the canonical directory
  already holds real data, this does nothing at all.
* **Never log a secret.** ``secrets.dat`` is migrated by name and size only;
  its contents are never read, decrypted, or written to the log.
* **Run once.** A receipt is written to the destination so a later launch does
  not re-scan or re-copy.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("nano.data_migration")

# The files that constitute a Nano installation's user data. Anything not named
# here is left alone: this migrates data Nano owns, not the whole directory.
MIGRATABLE_FILES = (
    "secrets.dat",                # OS-encrypted credential store
    "user_settings.json",         # settings chosen in the UI
    "permission_policies.json",   # the security policy store
    "nano_tasks.db",              # task queue
    "helios.db",                  # conversation memory and facts (legacy name)
)

# Written to the destination once a migration has been considered, so startup
# does not repeat the scan on every launch.
RECEIPT_NAME = ".nano_data_migration.json"

# A separate throwaway file for probing where writes actually land. It must NOT
# be the receipt: probing with Path.touch() creates the file, and an empty
# receipt would then read as "migration already done" and suppress the real one
# forever. Two files, two jobs.
_PROBE_NAME = ".nano_write_probe"


def _store_python_candidates() -> list[Path]:
    """Per-package caches created by Store-Python's filesystem redirection."""
    local = os.getenv("LOCALAPPDATA")
    if not local:
        return []
    packages = Path(local) / "Packages"
    if not packages.is_dir():
        return []
    found: list[Path] = []
    try:
        for entry in packages.glob("PythonSoftwareFoundation.Python.*"):
            candidate = entry / "LocalCache" / "Local" / "NanoAssistant"
            if candidate.is_dir():
                found.append(candidate)
    except OSError:
        logger.debug("Could not scan the Store-Python package caches", exc_info=True)
    return found


def legacy_candidates(destination: Path) -> list[Path]:
    """Every directory that may hold data belonging to this installation."""
    candidates: list[Path] = list(_store_python_candidates())

    local = os.getenv("LOCALAPPDATA")
    if local:
        # The pre-rename directory, if this install predates "Nano".
        candidates.append(Path(local) / "HeliosAssistant")

    roaming = os.getenv("APPDATA")
    if roaming:
        # Electron's app.getPath('userData') is %APPDATA%\<productName>, and
        # electron/main.js points NANO_DATA_DIR at its "data" subfolder.
        candidates.append(Path(roaming) / "Nano Assistant" / "data")
        candidates.append(Path(roaming) / "Nano Assistant")

    seen: set[str] = set()
    unique: list[Path] = []
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        key = str(resolved).lower()
        if key in seen or resolved == destination:
            continue
        seen.add(key)
        unique.append(candidate)
    return unique


def _payload_files(directory: Path) -> list[Path]:
    """Migratable files that actually exist and carry something."""
    present: list[Path] = []
    for name in MIGRATABLE_FILES:
        path = directory / name
        try:
            if path.is_file() and path.stat().st_size > 0:
                present.append(path)
        except OSError:
            continue
    return present


def _receipt_path(destination: Path) -> Path:
    return destination / RECEIPT_NAME


def _has_valid_receipt(destination: Path) -> bool:
    """True only for a receipt that actually records an outcome.

    An empty or unparseable file is treated as no receipt at all. Presence
    alone is too weak a signal: a zero-byte file created by anything else would
    otherwise suppress the migration permanently.
    """
    path = _receipt_path(destination)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    if not raw.strip():
        return False
    try:
        return isinstance(json.loads(raw), dict)
    except json.JSONDecodeError:
        return False


def _write_receipt(destination: Path, payload: dict) -> None:
    try:
        destination.mkdir(parents=True, exist_ok=True)
        _receipt_path(destination).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not record the data-migration receipt: %s", exc)


def migrate_user_data(destination: Path | None = None, *, force: bool = False) -> dict:
    """Bring user data into ``destination`` if it is empty and data exists elsewhere.

    Returns a summary describing exactly what happened. Safe to call on every
    startup: it exits immediately once a receipt exists.
    """
    from core.app_paths import DATA_DIR

    destination = Path(destination) if destination is not None else Path(DATA_DIR)
    summary: dict = {
        "destination": str(destination),
        "status": "skipped",
        "source": None,
        "copied": [],
        "skipped_existing": [],
        "errors": [],
    }

    if not force and _has_valid_receipt(destination):
        summary["status"] = "already_done"
        return summary

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        summary["status"] = "destination_unavailable"
        summary["errors"].append(str(exc))
        logger.warning("Data directory %s is not usable: %s", destination, exc)
        return summary

    # A destination that already holds data is never touched. This is the rule
    # that makes the operation safe: there is no merge, so there is no way to
    # overwrite something newer.
    existing = _payload_files(destination)
    if existing:
        summary["status"] = "destination_already_populated"
        summary["skipped_existing"] = [p.name for p in existing]
        _write_receipt(destination, {**summary, "at": datetime.now(timezone.utc).isoformat()})
        return summary

    for source in legacy_candidates(destination):
        payload = _payload_files(source)
        if not payload:
            continue

        summary["source"] = str(source)
        for path in payload:
            target = destination / path.name
            if target.exists():
                summary["skipped_existing"].append(path.name)
                continue
            try:
                shutil.copy2(path, target)
                # Name and size only. secrets.dat is copied as opaque bytes and
                # its contents are never read into this process.
                summary["copied"].append({"file": path.name, "bytes": path.stat().st_size})
            except OSError as exc:
                summary["errors"].append(f"{path.name}: {exc}")

        summary["status"] = "migrated" if summary["copied"] else "nothing_to_copy"
        logger.info(
            "Dados do Nano migrados de %s para %s: %d ficheiro(s). A origem foi mantida.",
            source, destination, len(summary["copied"]),
        )
        break
    else:
        summary["status"] = "no_source_found"

    _write_receipt(destination, {**summary, "at": datetime.now(timezone.utc).isoformat()})
    return summary


def describe_data_location() -> dict:
    """Where Nano's data is, and whether the process can see it truthfully.

    ``effective`` is the path a write actually lands in. Under Store Python it
    differs from ``configured``, and saying so is the whole point: a user whose
    settings "disappeared" needs to be told the directory moved, not that the
    data is gone.
    """
    from core.app_paths import DATA_DIR

    configured = Path(DATA_DIR)
    effective = configured
    probe = configured / _PROBE_NAME
    try:
        configured.mkdir(parents=True, exist_ok=True)
        probe.touch(exist_ok=True)
        effective = probe.resolve().parent
        probe.unlink(missing_ok=True)
    except OSError:
        pass

    return {
        "configured": str(configured),
        "effective": str(effective),
        "redirected": str(effective).lower() != str(configured).lower(),
        "files": [p.name for p in _payload_files(configured)],
    }


__all__ = [
    "MIGRATABLE_FILES",
    "RECEIPT_NAME",
    "describe_data_location",
    "legacy_candidates",
    "migrate_user_data",
]
