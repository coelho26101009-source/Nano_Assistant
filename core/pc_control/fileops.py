"""Creating, copying, moving, renaming -- and recycling, never deleting.

THE ONE SENTENCE THAT MATTERS: PC Control cannot permanently delete anything.

There is no `unlink`, no `rmdir`, no `shutil.rmtree` in this module, and no
fallback that reaches for one. "Delete" means SHFileOperationW with
FOF_ALLOWUNDO -- the Recycle Bin, the same thing pressing Delete in Explorer
does -- and the operation is then VERIFIED against the bin's item count, so a
removal that did not actually land in the bin is reported as exactly that
rather than as a success.

The other four rules:

* **Never a shell bypass.** A text file may only be created with an extension
  from `TEXT_EXTENSIONS`. Windows executes `.bat`, `.ps1`, `.vbs`, `.js` and a
  dozen others on a double click, so "write a file" must not be a way to author
  one. `.html` and `.svg` are refused too: both can carry script that runs the
  moment the file is opened.
* **Never a place Nano should not be.** Every path is checked against
  `files.is_protected` -- Windows internals, Program Files, credential stores,
  browser profiles, the Startup folder, and Nano's own directories.
* **Never a surprise overwrite.** A destination that already exists is a
  `conflict`, refused. There is no overwrite flag in V2; replacing a file the
  user did not mention is not something an assistant should be able to do by
  picking a name.
* **Never unverified.** Each operation re-reads the filesystem afterwards and
  reports what is actually there.

Names are a separate attack surface from paths, so `safe_name` is strict: one
path component, no separators, no traversal, no Windows device names, no
trailing dot or space (which Windows silently strips, letting "report.txt." and
"report.txt" be the same file).
"""
from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from core.pc_control import files, winapi
from core.pc_control.results import PCControlError, clamp_text

logger = logging.getLogger("nano.pc_control.fileops")

#: Extensions a text-creation tool may produce. An allow-list, not a deny-list:
#: the question is not "is this dangerous?" but "is this plainly inert text?".
TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".yaml",
    ".yml", ".ini", ".cfg", ".conf", ".rst", ".tex", ".srt",
})

#: The extension used when the user just says "a text file".
DEFAULT_TEXT_EXTENSION = ".txt"

#: A created text file is bounded. This is a note or a small document, not a
#: data channel.
MAX_TEXT_BYTES = 32 * 1024

#: A single copy is bounded too, so one tool call cannot fill the disk.
MAX_COPY_BYTES = 256 * 1024 * 1024

MAX_NAME_LENGTH = 128

_WINDOWS_RESERVED_NAMES = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})


def _require_windows() -> None:
    if not winapi.IS_WINDOWS:
        raise PCControlError("unsupported_platform", "As operações de ficheiros só funcionam no Windows.")


def safe_name(value) -> str:
    """One filesystem component the caller may create. Anything else is refused.

    This is where a model-supplied NAME is prevented from becoming a model-
    supplied PATH. `..`, separators, drive letters, device names, control
    characters and Windows' silently-stripped trailing dots and spaces are all
    rejected rather than sanitised -- a sanitised name is not the name the user
    approved on the confirmation card.
    """
    if not isinstance(value, str):
        raise PCControlError("invalid_input", "O nome tem de ser texto.")
    name = value.strip()
    if not name:
        raise PCControlError("invalid_input", "É preciso indicar um nome.")
    if len(name) > MAX_NAME_LENGTH:
        raise PCControlError("invalid_input",
                             f"O nome é demasiado longo (máximo {MAX_NAME_LENGTH} caracteres).")
    if any(character in name for character in '\\/:*?"<>|'):
        raise PCControlError("invalid_input",
                             "O nome não pode conter \\ / : * ? \" < > | .")
    if any(ord(character) < 0x20 for character in name):
        raise PCControlError("invalid_input", "O nome contém caracteres inválidos.")
    if name in {".", ".."} or name.startswith(".."):
        raise PCControlError("invalid_input", "Esse nome não é válido.")
    if name.endswith((".", " ")):
        # Windows silently strips a trailing dot or space, so "report.txt." and
        # "report.txt" are the same file. A conflict check on one would pass
        # while the write landed on the other, which is how a "new" file
        # quietly replaces an existing one. Surrounding whitespace is trimmed
        # above and is not this hazard.
        raise PCControlError("invalid_input",
                             "O nome não pode acabar em ponto nem em espaço.")
    if Path(name).stem.casefold() in _WINDOWS_RESERVED_NAMES:
        raise PCControlError("invalid_input",
                             f"'{name}' é um nome reservado do Windows.")
    return name


def resolve_parent(folder: str | None = None, path: str | None = None) -> Path:
    """The directory a new item goes into: a known folder name, or a real path.

    The split mirrors `pc_folder_open`: ToolExecutor resolves every argument
    called `path` against the workspace root before a handler sees it, which is
    right for a real path and wrong for the word "Ambiente de Trabalho". Known
    NAMES therefore travel as `folder`.
    """
    target = str(folder or "").strip() or str(path or "").strip()
    if not target:
        raise PCControlError("invalid_input", "É preciso indicar a pasta.")
    return files.resolve_folder(target)


def _validated_target(parent: Path, name: str) -> Path:
    candidate = (parent / name)
    try:
        resolved = candidate.resolve()
    except OSError as exc:
        raise PCControlError("invalid_input", "Esse caminho não é válido.") from exc
    # Belt and braces on top of safe_name: after resolution the result must
    # still be a direct child of the approved parent.
    if resolved.parent != parent.resolve():
        raise PCControlError("invalid_input",
                             "O nome tem de ficar dentro da pasta indicada.")
    if files.is_protected(resolved):
        raise PCControlError("protected_path", "Essa localização está protegida.")
    return resolved


def _describe(path: Path) -> dict:
    try:
        stat = path.stat()
    except OSError:
        return {"path": clamp_text(str(path), 400), "name": path.name, "exists": False}
    return {
        "path": clamp_text(str(path), 400),
        "name": path.name,
        "exists": True,
        "is_directory": path.is_dir(),
        "size_bytes": int(stat.st_size) if path.is_file() else None,
        "modified": time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)),
    }


def _existing(value: str, *, must_be_file: bool = False,
              must_be_directory: bool = False) -> Path:
    text = str(value or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar o ficheiro.")
    try:
        path = Path(os.path.expandvars(text)).expanduser().resolve()
    except OSError as exc:
        raise PCControlError("invalid_input", "Esse caminho não é válido.") from exc
    if not path.exists():
        raise PCControlError("not_found", f"'{text}' não existe.")
    if files.is_protected(path):
        raise PCControlError("protected_path",
                             "Esse caminho está protegido e o Nano não lhe toca.")
    if must_be_file and not path.is_file():
        raise PCControlError("invalid_input", f"'{path.name}' não é um ficheiro.")
    if must_be_directory and not path.is_dir():
        raise PCControlError("invalid_input", f"'{path.name}' não é uma pasta.")
    return path


# --------------------------------------------------------------------------
#  Creating
# --------------------------------------------------------------------------


def create_folder(name, *, folder: str | None = None, path: str | None = None) -> dict:
    _require_windows()
    parent = resolve_parent(folder, path)
    target = _validated_target(parent, safe_name(name))
    if target.exists():
        raise PCControlError("conflict", f"Já existe '{target.name}' nessa pasta.",
                             path=clamp_text(str(target), 400))
    try:
        target.mkdir(parents=False, exist_ok=False)
    except OSError as exc:
        logger.warning("folder creation failed: %s", exc)
        raise PCControlError("failed", f"Não consegui criar a pasta '{target.name}'.") from exc
    if not target.is_dir():
        raise PCControlError("failed", "A pasta não ficou criada.")
    return _describe(target)


def validate_text_filename(name) -> str:
    """A safe name WITH a plainly-inert extension. This is the shell-bypass gate."""
    candidate = safe_name(name)
    suffix = Path(candidate).suffix.lower()
    if not suffix:
        candidate = f"{candidate}{DEFAULT_TEXT_EXTENSION}"
        suffix = DEFAULT_TEXT_EXTENSION
    if suffix in files.EXECUTABLE_EXTENSIONS or suffix not in TEXT_EXTENSIONS:
        raise PCControlError(
            "blocked",
            f"O Nano só cria ficheiros de texto simples; '{suffix}' não é um deles.",
            allowed=sorted(TEXT_EXTENSIONS))
    return candidate


def create_text_file(name, content, *, folder: str | None = None,
                     path: str | None = None) -> dict:
    _require_windows()
    if not isinstance(content, str):
        raise PCControlError("invalid_input", "O conteúdo tem de ser texto.")
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_TEXT_BYTES:
        raise PCControlError(
            "invalid_input",
            f"O conteúdo é demasiado grande ({len(encoded)} bytes; o máximo é "
            f"{MAX_TEXT_BYTES}).")

    parent = resolve_parent(folder, path)
    target = _validated_target(parent, validate_text_filename(name))
    if target.exists():
        raise PCControlError("conflict", f"Já existe '{target.name}' nessa pasta.",
                             path=clamp_text(str(target), 400))
    try:
        target.write_text(content, encoding="utf-8")
    except OSError as exc:
        logger.warning("text file creation failed: %s", exc)
        raise PCControlError("failed", f"Não consegui criar '{target.name}'.") from exc

    described = _describe(target)
    if not described.get("exists"):
        raise PCControlError("failed", "O ficheiro não ficou criado.")
    described["bytes_written"] = len(encoded)
    return described


# --------------------------------------------------------------------------
#  Copying, moving, renaming
# --------------------------------------------------------------------------


def _destination_for(source: Path, destination: str) -> Path:
    """Where a copy or move lands: into a directory, or at an exact new path."""
    text = str(destination or "").strip()
    if not text:
        raise PCControlError("invalid_input", "É preciso indicar o destino.")
    try:
        candidate = Path(os.path.expandvars(text)).expanduser().resolve()
    except OSError as exc:
        raise PCControlError("invalid_input", "Esse destino não é válido.") from exc

    if candidate.is_dir():
        target = (candidate / source.name).resolve()
    else:
        if not candidate.parent.is_dir():
            raise PCControlError("not_found",
                                 f"A pasta de destino '{candidate.parent.name}' não existe.")
        target = candidate

    if files.is_protected(target):
        raise PCControlError("protected_path",
                             "O destino está protegido e o Nano não escreve lá.")
    if target.suffix.lower() in files.EXECUTABLE_EXTENSIONS:
        # Copying a document to `payload.bat` would turn a file operation into
        # authoring an executable, which is the boundary file.open defends too.
        raise PCControlError(
            "blocked",
            f"O Nano não cria ficheiros com a extensão '{target.suffix.lower()}'.")
    if target == source:
        raise PCControlError("invalid_input", "A origem e o destino são o mesmo ficheiro.")
    if target.exists():
        raise PCControlError("conflict", f"Já existe '{target.name}' no destino.",
                             path=clamp_text(str(target), 400))
    return target


def copy_file(source, destination) -> dict:
    _require_windows()
    src = _existing(source, must_be_file=True)
    if src.stat().st_size > MAX_COPY_BYTES:
        raise PCControlError(
            "invalid_input",
            f"O ficheiro é demasiado grande para o Nano copiar "
            f"({src.stat().st_size // (1024 * 1024)} MB).")
    target = _destination_for(src, destination)
    try:
        shutil.copy2(str(src), str(target))
    except OSError as exc:
        logger.warning("copy failed: %s", exc)
        raise PCControlError("failed", f"Não consegui copiar '{src.name}'.") from exc
    if not target.is_file():
        raise PCControlError("failed", "A cópia não apareceu no destino.")
    return {"source": _describe(src), "destination": _describe(target)}


def move_file(source, destination) -> dict:
    _require_windows()
    src = _existing(source, must_be_file=True)
    target = _destination_for(src, destination)
    try:
        shutil.move(str(src), str(target))
    except OSError as exc:
        logger.warning("move failed: %s", exc)
        raise PCControlError("failed", f"Não consegui mover '{src.name}'.") from exc
    if not target.exists() or src.exists():
        raise PCControlError("failed", "A mudança de sítio não ficou completa.")
    return {"source": clamp_text(str(src), 400), "destination": _describe(target)}


def rename_path(source, new_name) -> dict:
    """Rename a file or folder in place. The new name is a component, not a path."""
    _require_windows()
    src = _existing(source)
    name = safe_name(new_name)
    if src.is_file():
        suffix = Path(name).suffix.lower()
        if suffix in files.EXECUTABLE_EXTENSIONS:
            raise PCControlError(
                "blocked",
                f"O Nano não renomeia ficheiros para a extensão '{suffix}'.")
        if not suffix and src.suffix:
            # Dropping the extension silently is how a document stops opening.
            name = f"{name}{src.suffix}"
    target = _validated_target(src.parent, name)
    if target.exists():
        raise PCControlError("conflict", f"Já existe '{target.name}' nessa pasta.")
    try:
        src.rename(target)
    except OSError as exc:
        logger.warning("rename failed: %s", exc)
        raise PCControlError("failed", f"Não consegui mudar o nome de '{src.name}'.") from exc
    if not target.exists() or src.exists():
        raise PCControlError("failed", "O nome não chegou a mudar.")
    return {"previous_name": src.name, **_describe(target)}


# --------------------------------------------------------------------------
#  Recycling -- the only removal PC Control has
# --------------------------------------------------------------------------


def _recycle(path: Path) -> dict:
    root = f"{path.drive}\\" if path.drive else None
    before = winapi.recycle_bin_items(root) if root else None

    code, aborted = winapi.shell_recycle(str(path))
    if aborted:
        raise PCControlError(
            "refused",
            f"O Windows cancelou a remoção de '{path.name}'. Nada foi apagado.")
    if code != 0:
        raise PCControlError(
            "failed",
            f"O Windows não conseguiu enviar '{path.name}' para a reciclagem "
            f"(código {code}).")

    # VERIFICATION, and the honest branch. The source being gone is not proof
    # it went to the bin: Windows will offer to destroy an item it cannot
    # recycle, and the user may have accepted that prompt. So the bin's own
    # item count decides which of two different true statements is reported.
    time.sleep(0.15)
    after = winapi.recycle_bin_items(root) if root else None
    still_there = path.exists()
    recycled = (before is not None and after is not None and after > before)

    if still_there:
        raise PCControlError("failed", f"'{path.name}' continua onde estava.")
    return {
        "recycled": recycled,
        "bin_items_before": before,
        "bin_items_after": after,
    }


def recycle_file(source) -> dict:
    _require_windows()
    src = _existing(source, must_be_file=True)
    described = _describe(src)
    outcome = _recycle(src)
    return {**outcome, "item": described}


def recycle_folder(source) -> dict:
    """Send a folder and its contents to the Recycle Bin.

    The item count is reported so the user can see the size of what they are
    approving before they approve it; a folder with a thousand files inside is
    a very different decision from an empty one.
    """
    _require_windows()
    src = _existing(source, must_be_directory=True)
    try:
        contents = sum(1 for _ in src.rglob("*"))
    except OSError:
        contents = None
    described = _describe(src)
    described["contains"] = contents
    outcome = _recycle(src)
    return {**outcome, "item": described}


def preview_recycle(source) -> dict:
    """What a recycle WOULD affect, for the confirmation card. Changes nothing."""
    src = _existing(source)
    described = _describe(src)
    if src.is_dir():
        try:
            described["contains"] = sum(1 for _ in src.rglob("*"))
        except OSError:
            described["contains"] = None
    return described


__all__ = [
    "DEFAULT_TEXT_EXTENSION",
    "MAX_COPY_BYTES",
    "MAX_NAME_LENGTH",
    "MAX_TEXT_BYTES",
    "TEXT_EXTENSIONS",
    "copy_file",
    "create_folder",
    "create_text_file",
    "move_file",
    "preview_recycle",
    "recycle_file",
    "recycle_folder",
    "rename_path",
    "resolve_parent",
    "safe_name",
    "validate_text_filename",
]
