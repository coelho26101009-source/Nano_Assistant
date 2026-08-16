"""
H.E.L.I.O.S. Plugin: Ghost Organizer
Renomeia downloads com IA, organiza ficheiros, limpa cache, monitoriza recursos.
"""

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Any

import psutil

logger = logging.getLogger("helios.plugins.ghost_organizer")

DOWNLOADS_DIR = Path.home() / "Downloads"

# Regras de organização automática: extensão → pasta destino
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


def clean_windows_cache() -> dict:
    """Limpa caches do Windows (temp, prefetch, thumbnails)."""
    results = []
    paths_to_clean = [
        Path(os.environ.get("TEMP", r"C:\Windows\Temp")),
        Path(r"C:\Windows\Temp"),
        Path.home() / "AppData" / "Local" / "Temp",
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Explorer",  # thumbnails
    ]

    total_freed = 0

    for cache_path in paths_to_clean:
        if not cache_path.exists():
            continue
        freed = 0
        deleted = 0
        for item in cache_path.iterdir():
            try:
                size = item.stat().st_size if item.is_file() else _dir_size(item)
                if item.is_file():
                    item.unlink(missing_ok=True)
                elif item.is_dir():
                    shutil.rmtree(str(item), ignore_errors=True)
                freed += size
                deleted += 1
            except Exception:
                pass

        total_freed += freed
        results.append({
            "path":    str(cache_path),
            "deleted": deleted,
            "freed_mb": round(freed / 1e6, 1),
        })

    return {
        "cleaned":        results,
        "total_freed_mb": round(total_freed / 1e6, 1),
        "summary":        f"Libertei {total_freed/1e6:.0f} MB de cache do Windows",
    }


def _dir_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


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
            "description": "Limpa ficheiros temporários e cache do Windows para libertar espaço.",
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
