"""
Nano Plugin: Ghost Organizer
Renames downloads with AI, organizes files, clears cache, monitors resources.
"""

import logging
import os
import re
import shutil
from pathlib import Path
from datetime import datetime

import psutil

logger = logging.getLogger("nano.plugins.ghost_organizer")

DOWNLOADS_DIR = Path.home() / "Downloads"

# Automatic organization rules: extension -> destination folder
ORGANIZE_RULES: dict[str, str] = {
    ".pdf":  "Documentos/PDFs",
    ".docx": "Documentos/Word",
    ".xlsx": "Documentos/Excel",
    ".pptx": "Documentos/PowerPoint",
    ".txt":  "Documentos/Texto",
    ".jpg":  "Imagens",
    ".jpeg": "Imagens",
    ".png":  "Imagens",
    ".gif":  "Imagens",
    ".mp4":  "Videos",
    ".mkv":  "Videos",
    ".mp3":  "Musica",
    ".wav":  "Musica",
    ".zip":  "Arquivos",
    ".rar":  "Arquivos",
    ".7z":   "Arquivos",
    ".exe":  "Instaladores",
    ".msi":  "Instaladores",
}


def get_system_stats() -> dict:
    """Devolve estatísticas actuais de CPU, RAM e disco."""
    try:
        cpu    = psutil.cpu_percent(interval=1)
        ram    = psutil.virtual_memory()
        disk   = psutil.disk_usage("/")
        temps  = {}
        try:
            sensors = psutil.sensors_temperatures()
            if sensors:
                for name, entries in sensors.items():
                    if entries:
                        temps[name] = round(entries[0].current, 1)
        except Exception:
            pass

        # Top 5 processos por CPU
        top_procs = sorted(
            [p.info for p in psutil.process_iter(["name","cpu_percent","memory_percent"])
             if p.info["cpu_percent"] is not None],
            key=lambda x: x["cpu_percent"], reverse=True
        )[:5]

        warnings = []
        if cpu > 85:
            warnings.append(f"CPU a {cpu:.0f}% — pode querer matar alguns processos")
        if ram.percent > 85:
            warnings.append(f"RAM a {ram.percent:.0f}% ({ram.available/1e9:.1f} GB livre)")
        if disk.percent > 90:
            warnings.append(f"Disco C: a {disk.percent:.0f}% de capacidade")

        return {
            "cpu_percent":    round(cpu, 1),
            "ram_percent":    round(ram.percent, 1),
            "ram_available_gb": round(ram.available / 1e9, 2),
            "disk_percent":   round(disk.percent, 1),
            "disk_free_gb":   round(disk.free / 1e9, 2),
            "temperatures":   temps,
            "top_processes":  top_procs,
            "warnings":       warnings,
        }
    except Exception as exc:
        return {"error": f"Não consegui ler estatísticas: {exc}"}


def organize_downloads(dry_run: bool = False) -> dict:
    """Organiza a pasta Downloads em subpastas por tipo."""
    if not DOWNLOADS_DIR.exists():
        return {"error": "Pasta Downloads não encontrada"}

    moved  = []
    errors = []
    skipped = []

    for file in DOWNLOADS_DIR.iterdir():
        if file.is_dir() or file.name.startswith("."):
            skipped.append(file.name)
            continue

        ext     = file.suffix.lower()
        subdir  = ORGANIZE_RULES.get(ext)

        if not subdir:
            skipped.append(file.name)
            continue

        dest_dir = DOWNLOADS_DIR / subdir
        dest     = dest_dir / file.name

        # Resolve conflito de nomes
        if dest.exists():
            stem = file.stem
            ts   = datetime.now().strftime("%H%M%S")
            dest = dest_dir / f"{stem}_{ts}{ext}"

        try:
            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(file), str(dest))
            moved.append({"from": file.name, "to": str(dest.relative_to(DOWNLOADS_DIR))})
        except Exception as exc:
            errors.append({"file": file.name, "error": str(exc)})

    return {
        "moved":   moved,
        "errors":  errors,
        "skipped": len(skipped),
        "dry_run": dry_run,
        "summary": f"{len(moved)} ficheiros {'seriam movidos' if dry_run else 'movidos'}, {len(errors)} erros",
    }


#: Files younger than this are left alone. The temp folders also hold the
#: working files of an installer that is half-way through, of an app that is
#: open right now and of a download being unpacked; deleting those breaks the
#: thing that is running. Windows' own Disk Cleanup uses a similar rule.
CACHE_MIN_AGE_SECONDS = 24 * 60 * 60

#: In Explorer's folder only the thumbnail and icon caches are cache; the
#: other files there are settings Windows keeps for Explorer.
_EXPLORER_CACHE_PREFIXES = ("thumbcache_", "iconcache_")


def _cache_folders() -> list[Path]:
    """The temp folders, each once (TEMP is usually AppData/Local/Temp)."""
    candidates = [
        Path(os.environ.get("TEMP", r"C:\Windows\Temp")),
        Path(r"C:\Windows\Temp"),
        Path.home() / "AppData" / "Local" / "Temp",
    ]
    seen: set[str] = set()
    folders: list[Path] = []
    for folder in candidates:
        try:
            key = os.path.normcase(str(folder.resolve()))
        except OSError:
            key = os.path.normcase(str(folder))
        if key in seen:
            continue
        seen.add(key)
        folders.append(folder)
    return folders


def _explorer_cache_folder() -> Path:
    return Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Explorer"


def _newest_time(info: os.stat_result) -> float:
    """The most recent of written, created (Windows) or changed (Linux).

    An installer that unpacks a zip keeps the ORIGINAL dates on the files, so
    the modification date alone would call a file unpacked a minute ago "two
    years old". The creation date says when it really arrived.
    """
    return max(info.st_mtime, info.st_ctime, getattr(info, "st_birthtime", 0.0) or 0.0)


def _is_link(entry: os.DirEntry) -> bool:
    try:
        return entry.is_symlink() or bool(getattr(entry, "is_junction", lambda: False)())
    except OSError:
        return True


class _CleanTally:
    def __init__(self) -> None:
        self.freed = 0
        self.deleted = 0
        self.kept_recent = 0
        self.in_use = 0
        self.no_access = 0

    def refused(self, exc: OSError) -> None:
        """Count a file Windows would not delete, under the right reason.

        Windows says "in use" with error 32 (sharing violation) or 33 (lock
        violation); "access denied" (5) is a file that belongs to another
        account, like most of C:\\Windows\\Temp for a normal user. Python
        raises PermissionError for all three, so the number decides.
        """
        import errno
        winerror = getattr(exc, "winerror", None)
        if winerror in (32, 33) or exc.errno in (errno.EBUSY, errno.ETXTBSY):
            self.in_use += 1
        else:
            self.no_access += 1


def _clean_folder(folder: Path, now: float, tally: _CleanTally, *, top: bool) -> bool:
    """Delete what is old inside ``folder``. True when ``folder`` ends up empty.

    Links (symlinks and junctions) are never followed and never deleted: they
    point somewhere else, and that somewhere is not cache.
    """
    try:
        with os.scandir(folder) as listing:
            entries = list(listing)
    except OSError:
        return False
    empty = True
    for entry in entries:
        if _is_link(entry):
            empty = False
            continue
        try:
            info = entry.stat(follow_symlinks=False)
        except OSError:
            empty = False
            continue
        old = now - _newest_time(info) >= CACHE_MIN_AGE_SECONDS
        if entry.is_dir(follow_symlinks=False):
            child_empty = _clean_folder(Path(entry.path), now, tally, top=False)
            if child_empty and old:
                try:
                    os.rmdir(entry.path)
                    continue
                except OSError:
                    pass
            empty = False
            continue
        if not old:
            tally.kept_recent += 1
            empty = False
            continue
        try:
            os.unlink(entry.path)
        except OSError as exc:
            # Open in some program right now, or not ours to delete.
            tally.refused(exc)
            empty = False
            continue
        tally.freed += info.st_size
        tally.deleted += 1
    return empty and not top


def clean_windows_cache(now: float | None = None) -> dict:
    """Empty the Windows temp folders and thumbnail cache, gently.

    Only files older than 24 hours go; anything newer, or open in a program,
    stays. ``now`` exists for the tests.
    """
    import time as _time
    moment = _time.time() if now is None else float(now)
    results = []
    total = _CleanTally()

    for cache_path in _cache_folders():
        if not cache_path.is_dir():
            continue
        tally = _CleanTally()
        _clean_folder(cache_path, moment, tally, top=True)
        results.append({
            "path": str(cache_path),
            "deleted": tally.deleted,
            "freed_mb": round(tally.freed / 1e6, 1),
            "kept_recent": tally.kept_recent,
            "in_use": tally.in_use,
            "no_access": tally.no_access,
        })
        for name in ("freed", "deleted", "kept_recent", "in_use", "no_access"):
            setattr(total, name, getattr(total, name) + getattr(tally, name))

    explorer = _explorer_cache_folder()
    if explorer.is_dir():
        tally = _CleanTally()
        try:
            names = [p for p in explorer.iterdir()
                     if p.name.lower().startswith(_EXPLORER_CACHE_PREFIXES)
                     and p.suffix.lower() == ".db" and p.is_file() and not p.is_symlink()]
        except OSError:
            names = []
        for item in names:
            try:
                info = item.stat()
            except OSError:
                continue
            if moment - _newest_time(info) < CACHE_MIN_AGE_SECONDS:
                tally.kept_recent += 1
                continue
            try:
                item.unlink()
            except OSError as exc:
                tally.refused(exc)
                continue
            tally.freed += info.st_size
            tally.deleted += 1
        results.append({
            "path": str(explorer),
            "deleted": tally.deleted,
            "freed_mb": round(tally.freed / 1e6, 1),
            "kept_recent": tally.kept_recent,
            "in_use": tally.in_use,
            "no_access": tally.no_access,
        })
        for name in ("freed", "deleted", "kept_recent", "in_use", "no_access"):
            setattr(total, name, getattr(total, name) + getattr(tally, name))

    summary = f"Libertei {total.freed / 1e6:.0f} MB de cache do Windows"
    kept = []
    if total.kept_recent:
        kept.append(f"{total.kept_recent} ficheiro(s) com menos de 24 horas")
    if total.in_use:
        kept.append(f"{total.in_use} em uso")
    if total.no_access:
        kept.append(f"{total.no_access} sem permissão para apagar")
    if kept:
        summary += "; deixei " + " e ".join(kept)
    return {
        "cleaned": results,
        "total_freed_mb": round(total.freed / 1e6, 1),
        "deleted": total.deleted,
        "kept_recent": total.kept_recent,
        "in_use": total.in_use,
        "no_access": total.no_access,
        "min_age_hours": CACHE_MIN_AGE_SECONDS // 3600,
        "summary": summary,
    }


def rename_file_smart(file_path: str, new_name: str) -> dict:
    """Renomeia um ficheiro com o nome sugerido pela IA."""
    src = Path(file_path)
    if not src.exists():
        return {"error": f"Ficheiro não encontrado: {file_path}"}

    # Sanitiza o nome
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', new_name)
    if not safe_name.endswith(src.suffix):
        safe_name += src.suffix

    dest = src.parent / safe_name
    try:
        src.rename(dest)
        return {"success": True, "from": src.name, "to": dest.name}
    except Exception as exc:
        return {"error": f"Falha ao renomear: {exc}"}


def get_tools() -> list[dict]:
    return [
        {"type": "function", "function": {
            "name": "system_stats",
            "description": "Devolve estatísticas do sistema: CPU, RAM, disco, temperatura e top processos. Avisa se algo está a estrangular.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "organize_downloads",
            "description": "Organiza a pasta Downloads em subpastas por tipo de ficheiro (PDFs, Imagens, Vídeos, etc.).",
            "parameters": {"type": "object", "properties": {
                "dry_run": {"type": "boolean",
                            "description": "Se true, só mostra o que faria sem mover nada"},
            }},
        }},
        {"type": "function", "function": {
            "name": "clean_windows_cache",
            "description": "Limpa ficheiros temporários e a cache de miniaturas do Windows para libertar espaço. Só apaga ficheiros com mais de 24 horas que não estejam em uso.",
            "parameters": {"type": "object", "properties": {}},
        }},
        {"type": "function", "function": {
            "name": "rename_file_smart",
            "description": "Renomeia um ficheiro com um nome descritivo sugerido pela IA.",
            "parameters": {"type": "object", "required": ["file_path","new_name"], "properties": {
                "file_path": {"type": "string", "description": "Caminho completo do ficheiro"},
                "new_name":  {"type": "string", "description": "Novo nome (sem extensão ou com)"},
            }},
        }},
    ]


TOOL_HANDLERS: dict = {
    "system_stats":        lambda _: get_system_stats(),
    "organize_downloads":  lambda a: organize_downloads(**a),
    "clean_windows_cache": lambda _: clean_windows_cache(),
    "rename_file_smart":   lambda a: rename_file_smart(**a),
}
