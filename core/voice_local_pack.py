"""The natural offline voice, set up in one click.

WHAT IT IS
----------
Natural voices that run on this PC, on the processor, with no internet:

* **Portuguese (Portugal):** Piper "dii" (high quality), 67 MB. A European
  Portuguese voice, not a Brazilian one. Licence CC BY-NC-SA 4.0: free for
  personal, non-commercial use, which is how NANO uses it (it is downloaded on
  the person's own PC, never shipped inside NANO).
* **English:** Kokoro v1.0 (Apache-2.0), 350 MB, with British and American
  voices. One of the most natural open voices there is.

Both run through sherpa-onnx (Apache-2.0), about 20 MB of packages.
Measured in the cloud on 4 slow cores: Piper speaks 14 s of audio in 0.5 s,
Kokoro in 3.5 s, and a transcriber understood both almost word for word.

WHY THESE (measured 2026-11-24, see Nano-Brain "Voz realista")
--------------------------------------------------------------
* Piper "tugão" (the other pt-PT voice) was hard to understand: a transcriber
  got 43 % of its words wrong, against 10 % for "dii" (numbers written as
  digits account for most of those).
* Kokoro speaks Brazilian Portuguese, not European: used for English only.
* sherpa-onnx is pinned to 1.13.x: 2.x no longer reads Piper and Kokoro text
  without an extra frontend.

HOW IT INSTALLS
---------------
Same rules as the speech pack (core/voice_setup.py): the packages come from
``requirements/optional.lock`` with their hashes, and the voice files come from
fixed addresses with a fixed SHA-256. Nothing the window sends can change what
is downloaded or where it goes. Only the voice for the interface language is
downloaded; the other one when the language changes and the person asks.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from core.app_paths import DATA_DIR
from core.i18n import tr
from core import voice_setup

logger = logging.getLogger("nano.voice_local")

PACKAGE = "sherpa-onnx"
_RELEASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/"


@dataclass(frozen=True)
class VoiceModel:
    name: str          # folder name inside the archive and on disk
    kind: str          # "piper" | "kokoro"
    language: str      # "pt-PT" | "en"
    sha256: str
    size_mb: int
    licence: str

    @property
    def url(self) -> str:
        return f"{_RELEASE}{self.name}.tar.bz2"


#: Keys are the ids speech_engines.LOCAL_VOICES uses.
MODELS: dict[str, VoiceModel] = {
    "piper-pt_PT-dii-high": VoiceModel(
        name="vits-piper-pt_PT-dii-high", kind="piper", language="pt-PT",
        sha256="d0859dd3e4fac69aac33a3e58958d56cd4a8721446d597e21734a7048b977225",
        size_mb=67, licence="CC BY-NC-SA 4.0"),
    "kokoro-multi-lang-v1_0": VoiceModel(
        name="kokoro-multi-lang-v1_0", kind="kokoro", language="en",
        sha256="60703707bc05ccd48d60e1eb723f38b07d6e474795a0b6a588d7220400e0f7a4",
        size_mb=350, licence="Apache-2.0"),
}
LANGUAGE_MODEL = {"pt-PT": "piper-pt_PT-dii-high", "en": "kokoro-multi-lang-v1_0"}
#: Never write more than this, whatever the server sends.
MAX_DOWNLOAD_BYTES = 450 * 1024 * 1024
#: The files a model needs, relative to its folder. "ready" means all exist.
_REQUIRED = {
    "piper": ("tokens.txt", "espeak-ng-data"),
    "kokoro": ("model.onnx", "voices.bin", "tokens.txt", "espeak-ng-data", "lexicon-gb-en.txt",
               "lexicon-us-en.txt"),
}


def models_dir() -> Path:
    return DATA_DIR / "voices"


def _model_path(model: str, base: Path | None = None) -> Path:
    return (base or models_dir()) / MODELS[model].name


def model_ready(model: str, base: Path | None = None) -> bool:
    if model not in MODELS:
        return False
    folder = _model_path(model, base)
    spec = MODELS[model]
    try:
        if not all((folder / item).exists() for item in _REQUIRED[spec.kind]):
            return False
        if spec.kind == "piper":
            return any(p.suffix == ".onnx" for p in folder.iterdir())
        return True
    except OSError:
        return False


def sherpa_config(model: str, base: Path | None = None, *, threads: int = 2) -> Any:
    """The sherpa_onnx.OfflineTtsConfig for one downloaded model."""
    preload_onnxruntime()
    import sherpa_onnx

    spec = MODELS[model]
    folder = _model_path(model, base)
    if spec.kind == "piper":
        onnx = next(p for p in sorted(folder.iterdir()) if p.suffix == ".onnx")
        model_config = sherpa_onnx.OfflineTtsModelConfig(
            vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                model=str(onnx), tokens=str(folder / "tokens.txt"),
                data_dir=str(folder / "espeak-ng-data")),
            num_threads=threads)
    else:
        model_config = sherpa_onnx.OfflineTtsModelConfig(
            kokoro=sherpa_onnx.OfflineTtsKokoroModelConfig(
                model=str(folder / "model.onnx"), voices=str(folder / "voices.bin"),
                tokens=str(folder / "tokens.txt"), data_dir=str(folder / "espeak-ng-data"),
                lexicon=str(folder / "lexicon-gb-en.txt"),
                lang="en"),
            num_threads=threads)
    return sherpa_onnx.OfflineTtsConfig(model=model_config, max_num_sentences=1)


def preload_onnxruntime() -> None:
    """Load the voice detector's onnxruntime BEFORE sherpa-onnx, when both exist.

    sherpa-onnx carries its own ``onnxruntime.dll`` and Windows loads a DLL
    name only once per process. Loaded first, the newer onnxruntime of the
    speech pack (1.31) is the one both use; its C API serves older callers,
    while the reverse (an older DLL under the newer Python binding) is not
    guaranteed. Same reasoning as now_playing._load_onnxruntime_first.
    Measured on Linux in one process (2026-11-24): both work; on Windows it is
    on the test guide (item 71).
    """
    import importlib.util

    try:
        if "onnxruntime" not in sys.modules and importlib.util.find_spec("onnxruntime") is not None:
            import onnxruntime  # type: ignore  # noqa: F401
    except Exception:
        logger.debug("onnxruntime preload failed", exc_info=True)


def package_installed() -> bool:
    import importlib
    import importlib.util

    importlib.invalidate_caches()
    try:
        return importlib.util.find_spec("sherpa_onnx") is not None
    except (ImportError, ValueError):
        return False


def _safe_extract(archive: Path, target_parent: Path, folder_name: str) -> None:
    """Extract only ``folder_name/...`` regular files and folders, nothing outside."""
    with tarfile.open(archive, "r:bz2") as tar:
        members = []
        for member in tar.getmembers():
            name = member.name.replace("\\", "/")
            parts = [p for p in name.split("/") if p not in ("", ".")]
            if not parts or parts[0] != folder_name or ".." in parts or name.startswith("/"):
                continue
            if not (member.isfile() or member.isdir()):
                continue   # no links, devices or fifos
            members.append(member)
        kwargs: dict[str, Any] = {}
        if sys.version_info >= (3, 12):
            kwargs["filter"] = "data"
        tar.extractall(target_parent, members=members, **kwargs)  # noqa: S202 - members vetted above


class LocalVoicePack:
    """The one-click installer. One run at a time; status is plain data."""

    def __init__(self, *, language: Callable[[], str] = lambda: "pt-PT",
                 base: Callable[[], Path] = models_dir,
                 lock_path: Path = voice_setup.OPTIONAL_LOCK,
                 python: str | None = None,
                 runner: Callable[..., Any] | None = None,
                 fetch: Callable[[str, Path, Callable[[int], None]], None] | None = None,
                 installed: Callable[[], bool] = package_installed) -> None:
        import subprocess

        self._language = language
        self._base = base
        self._lock_path = lock_path
        self._python = python or sys.executable
        self._runner = runner or subprocess.run
        self._fetch = fetch or _http_fetch
        self._installed = installed
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle"}
        self._on_ready: Callable[[str], None] | None = None

    def model_for(self, language: str | None = None) -> str:
        return LANGUAGE_MODEL.get(language or self._language(), LANGUAGE_MODEL["en"])

    def status(self) -> dict[str, Any]:
        language = self._language()
        model = self.model_for(language)
        with self._lock:
            progress = dict(self._state)
        installed = self._installed()
        ready = model_ready(model, self._base())
        return {
            "ready": installed and ready,
            "packageInstalled": installed,
            "modelReady": ready,
            "language": language,
            "model": model,
            "sizeMb": MODELS[model].size_mb + (0 if installed else 20),
            "licence": MODELS[model].licence,
            "languages": {lang: model_ready(m, self._base()) for lang, m in LANGUAGE_MODEL.items()},
            "install": progress,
        }

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._state = values

    def start(self, on_ready: Callable[[str], None] | None = None) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"ok": True, "already": True, **self.status()}
        model = self.model_for()
        if self._installed() and model_ready(model, self._base()):
            return {"ok": True, "already": True, **self.status()}
        if not self._installed() and not self._lock_path.exists():
            return {"ok": False, "error": "lock_missing",
                    "detail": tr("Falta o ficheiro requirements/optional.lock nesta instalação.")}
        self._on_ready = on_ready
        self._set(state="installing", message=tr("A preparar a voz offline…"))
        self._thread = threading.Thread(target=self._run, args=(model,),
                                        name="nano-local-voice", daemon=True)
        self._thread.start()
        return {"ok": True, "started": True}

    def wait(self, timeout: float = 5.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _run(self, model: str) -> None:
        started = time.monotonic()
        try:
            if not self._installed():
                error = self._install()
                if error:
                    self._set(state="error", message=error)
                    return
            if not model_ready(model, self._base()):
                error = self._download(model)
                if error:
                    self._set(state="error", message=error)
                    return
            logger.info("Voz offline pronta (%s, %.0fs)", model, time.monotonic() - started)
            self._set(state="done", message=tr("A voz offline está pronta."))
            if self._on_ready is not None:
                try:
                    self._on_ready(model)
                except Exception:
                    logger.debug("local voice on_ready failed", exc_info=True)
        except Exception:
            logger.exception("Local voice pack failed")
            self._set(state="error", message=tr("A instalação da voz offline falhou."))

    def _install(self) -> str | None:
        try:
            lock_text = self._lock_path.read_text(encoding="utf-8")
        except OSError:
            return tr("Falta o ficheiro requirements/optional.lock nesta instalação.")
        requirements = voice_setup.requirements_text(lock_text, root=PACKAGE)
        if not requirements:
            return tr("O ficheiro de dependências não tem a voz offline.")
        import subprocess

        handle, path = tempfile.mkstemp(prefix="nano-voice-", suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fp:
                fp.write(requirements)
            command = [self._python, "-m", "pip", "install", "--disable-pip-version-check",
                       "--no-input", "--require-hashes", "--no-deps", "--only-binary=:all:", "-r", path]
            kwargs: dict[str, Any] = {"capture_output": True, "text": True,
                                      "timeout": voice_setup.PIP_TIMEOUT_SECONDS}
            if os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            result = self._runner(command, **kwargs)
        except subprocess.TimeoutExpired:
            return tr("A instalação demorou demasiado. Tenta outra vez.")
        except Exception as exc:
            logger.warning("pip could not run: %s", type(exc).__name__)
            return tr("Não consegui correr o instalador de pacotes (pip).")
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        if getattr(result, "returncode", 1) != 0:
            tail = str(getattr(result, "stderr", "") or "").strip().splitlines()[-3:]
            logger.warning("pip install of the local voice failed: %s", " | ".join(tail)[:400])
            return tr("A instalação da voz offline falhou. Verifica a ligação à internet e tenta outra vez.")
        if not self._installed():
            return tr("A instalação terminou mas a voz offline não ficou disponível.")
        return None

    def _download(self, model: str) -> str | None:
        spec = MODELS[model]
        base = self._base()
        total = spec.size_mb * 1024 * 1024
        self._set(state="downloading", percent=1, completedMb=0, totalMb=spec.size_mb,
                  message=tr("A descarregar a voz ({mb} MB)…", mb=spec.size_mb))

        def progress(done: int) -> None:
            self._set(state="downloading", percent=max(1, min(99, round(done * 100 / total))),
                      completedMb=round(done / (1024 * 1024)), totalMb=spec.size_mb,
                      message=tr("A descarregar a voz ({mb} MB)…", mb=spec.size_mb))

        try:
            base.mkdir(parents=True, exist_ok=True)
            work = Path(tempfile.mkdtemp(prefix="nano-voice-", dir=str(base)))
        except OSError:
            return tr("Não consegui criar a pasta das vozes.")
        try:
            archive = work / "voice.tar.bz2"
            try:
                self._fetch(spec.url, archive, progress)
            except Exception as exc:
                logger.warning("Local voice download failed: %s", type(exc).__name__)
                return tr("Não consegui descarregar a voz. Verifica a ligação à internet e tenta outra vez.")
            digest = hashlib.sha256()
            with open(archive, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != spec.sha256:
                logger.warning("Local voice checksum mismatch for %s", model)
                return tr("A voz descarregada não é a esperada (verificação falhou). Tenta outra vez.")
            self._set(state="extracting", message=tr("A preparar a voz…"))
            _safe_extract(archive, work, spec.name)
            extracted = work / spec.name
            if not extracted.is_dir():
                return tr("A voz descarregada está incompleta.")
            final = base / spec.name
            if final.exists():
                shutil.rmtree(final, ignore_errors=True)
            os.replace(extracted, final)
            if not model_ready(model, base):
                return tr("A voz descarregada está incompleta.")
            return None
        finally:
            shutil.rmtree(work, ignore_errors=True)


def _http_fetch(url: str, target: Path, progress: Callable[[int], None]) -> None:
    """Stream ``url`` into ``target`` (redirects followed, size capped)."""
    import httpx

    from core import http_tls

    done = 0
    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0,
                      verify=http_tls.shared_context()) as response:
        response.raise_for_status()
        with open(target, "wb") as handle:
            for chunk in response.iter_bytes(1024 * 256):
                done += len(chunk)
                if done > MAX_DOWNLOAD_BYTES:
                    raise ValueError("download larger than expected")
                handle.write(chunk)
                progress(done)


__all__ = ["LANGUAGE_MODEL", "LocalVoicePack", "MODELS", "model_ready", "models_dir",
           "package_installed", "sherpa_config"]
