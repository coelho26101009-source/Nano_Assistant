"""The speech pack, set up in one click: NANO ready to listen.

THE PROBLEM
-----------
NANO speaks out of the box (edge-tts and PyAudio are in the runtime lock), but
it could not LISTEN: local speech recognition needs ``faster-whisper``, which
lives in the optional lock, and its ``small`` model (~480 MB), which is
downloaded on first use. Neither was offered anywhere. The person had to find
a pip command in docs/VOICE.md, and that command did not even work: pip does
not take hashes from a constraints file (``-c``), so ``--require-hashes`` with
``-c requirements/optional.lock faster-whisper`` is refused.

WHAT THIS DOES
--------------
One button in Definições → Voz (and in the first-run guide):

1. **Install** ``faster-whisper`` and exactly the packages it needs, with the
   versions and hashes from ``requirements/optional.lock``. The lines are
   copied from the lock into a temporary requirements file, so pip checks
   every download against its hash. Nothing that is not in the lock can be
   installed, and nothing else in the optional lock is pulled in.
2. **Download** the transcription model the voice settings name (``small``),
   into the normal Hugging Face cache that faster-whisper reads.
3. **Say it is ready.** The wake phrase is NOT switched on here: the window
   then asks "Queres ligar o «Ei NANO»?" and only a click turns it on.

Progress is read back with :meth:`SpeechPack.status`. Nothing runs unless the
person pressed the button, and only the two fixed steps above can run: the
package list and the model name do not come from the window.
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from core.app_paths import ROOT
from core.i18n import tr

logger = logging.getLogger("nano.voice_setup")

OPTIONAL_LOCK = ROOT / "requirements" / "optional.lock"
#: The package that gives NANO its ears. The rest is its dependency closure.
SPEECH_PACKAGE = "faster-whisper"
#: The models this pack may download (faster-whisper's own names).
ALLOWED_MODELS = {"tiny": "Systran/faster-whisper-tiny", "base": "Systran/faster-whisper-base",
                  "small": "Systran/faster-whisper-small",
                  # "Melhor qualidade" in Settings > Voz: opt-in, much bigger.
                  "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo"}
#: Approximate download size, for the progress bar only.
MODEL_MB = {"tiny": 75, "base": 145, "small": 484, "large-v3-turbo": 1620}
#: Settings > Voz > "Qualidade do reconhecimento": which model each choice is.
QUALITY_MODELS = {"fast": "small", "best": "large-v3-turbo"}


def model_for_quality(quality: str | None) -> str:
    return QUALITY_MODELS.get(str(quality or "fast"), "small")
#: pip can take a while on a slow line; a hung pip must not hold the pack forever.
PIP_TIMEOUT_SECONDS = 20 * 60

_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==")


def _canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_lock(text: str) -> dict[str, dict[str, Any]]:
    """``{package: {"lines": [...], "via": {...}}}`` from a uv/pip-compile lock.

    ``lines`` is the requirement with its ``--hash`` continuation lines, exactly
    as written; ``via`` names what pulled the package in (lower-case, and
    ``-c ...`` constraint markers dropped).
    """
    packages: dict[str, dict[str, Any]] = {}
    current: dict[str, Any] | None = None
    in_via = False
    for raw in text.splitlines():
        line = raw.rstrip()
        match = _NAME.match(line)
        if match:
            current = {"lines": [line], "via": set()}
            packages[_canon(match.group(1))] = current
            in_via = False
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("--hash=") and not in_via:
            current["lines"].append(line)
        elif stripped.startswith("# via"):
            in_via = True
            rest = stripped[len("# via"):].strip()
            if rest:
                current["via"].add(_canon(rest))
        elif in_via and stripped.startswith("#"):
            item = stripped.lstrip("#").strip()
            if item and not item.startswith("-c") and not item.startswith("-r"):
                current["via"].add(_canon(item))
        elif not stripped:
            in_via = False
    return packages


def closure(packages: dict[str, dict[str, Any]], root: str = SPEECH_PACKAGE) -> list[str]:
    """``root`` and every locked package that is there because of it."""
    wanted = {_canon(root)}
    if wanted - set(packages):
        return []
    changed = True
    while changed:
        changed = False
        for name, info in packages.items():
            if name not in wanted and info["via"] & wanted:
                wanted.add(name)
                changed = True
    return sorted(wanted)


def requirements_text(lock_text: str, root: str = SPEECH_PACKAGE) -> str:
    """The hash-pinned requirements file for ``root`` alone, from the lock."""
    packages = parse_lock(lock_text)
    names = closure(packages, root)
    if not names:
        return ""
    lines = ["# Generated by NANO from requirements/optional.lock (speech pack)."]
    for name in names:
        block = packages[name]["lines"]
        # Re-join the requirement and its hashes with explicit continuations.
        lines.append(" \\\n".join(part.rstrip(" \\") for part in block))
    return "\n".join(lines) + "\n"


def hub_cache_dir() -> Path:
    """Where huggingface_hub (and so faster-whisper) keeps downloaded models."""
    explicit = os.environ.get("HF_HUB_CACHE") or os.environ.get("HUGGINGFACE_HUB_CACHE")
    if explicit:
        return Path(explicit)
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home) / "hub"
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "huggingface" / "hub"


def _model_dir(model: str) -> Path:
    repo = ALLOWED_MODELS.get(model, ALLOWED_MODELS["small"])
    return hub_cache_dir() / ("models--" + repo.replace("/", "--"))


def model_cached(model: str) -> bool:
    """Whether the model is fully on disk (its weights are in a snapshot)."""
    snapshots = _model_dir(model) / "snapshots"
    try:
        return any((snap / "model.bin").exists() for snap in snapshots.iterdir())
    except OSError:
        return False


def _dir_mb(path: Path) -> float:
    total = 0
    try:
        for entry in path.rglob("*"):
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0.0
    return total / (1024 * 1024)


def package_installed() -> bool:
    importlib.invalidate_caches()
    return importlib.util.find_spec("faster_whisper") is not None


class SpeechPack:
    """The one-click installer. One run at a time; status is plain data."""

    def __init__(self, *, model: Callable[[], str] = lambda: "small",
                 lock_path: Path = OPTIONAL_LOCK,
                 python: str | None = None,
                 runner: Callable[..., Any] = subprocess.run,
                 downloader: Callable[[str], Any] | None = None,
                 installed: Callable[[], bool] = package_installed,
                 cached: Callable[[str], bool] = model_cached) -> None:
        self._model = model
        self._lock_path = lock_path
        self._python = python or sys.executable
        self._runner = runner
        self._downloader = downloader
        self._installed = installed
        self._cached = cached
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._state: dict[str, Any] = {"state": "idle"}
        self._on_ready: Callable[[], None] | None = None

    # ── reading ──────────────────────────────────────────────────────────
    def model_name(self) -> str:
        name = str(self._model() or "small")
        return name if name in ALLOWED_MODELS else "small"

    def status(self) -> dict[str, Any]:
        model = self.model_name()
        with self._lock:
            progress = dict(self._state)
        installed = self._installed()
        cached = self._cached(model)
        if progress.get("state") == "downloading":
            done = _dir_mb(_model_dir(model))
            total = MODEL_MB.get(model, 484)
            progress["percent"] = max(1, min(99, round(done * 100 / total)))
            progress["completedMb"] = round(done)
            progress["totalMb"] = total
        return {
            "ready": installed and cached,
            "packageInstalled": installed,
            "modelCached": cached,
            "model": model,
            "sizeMb": MODEL_MB.get(model, 484) + (0 if installed else 150),
            "lockAvailable": self._lock_path.exists(),
            **{"install": progress},
        }

    # ── running ──────────────────────────────────────────────────────────
    def _set(self, **values: Any) -> None:
        with self._lock:
            self._state = values

    def start(self, on_ready: Callable[[], None] | None = None) -> dict[str, Any]:
        if self._thread and self._thread.is_alive():
            return {"ok": True, "already": True, **self.status()}
        model = self.model_name()
        if self._installed() and self._cached(model):
            return {"ok": True, "already": True, **self.status()}
        if not self._installed() and not self._lock_path.exists():
            return {"ok": False, "error": "lock_missing",
                    "detail": tr("Falta o ficheiro requirements/optional.lock nesta instalação.")}
        self._on_ready = on_ready
        self._set(state="installing", message=tr("A instalar o reconhecimento de voz…"))
        self._thread = threading.Thread(target=self._run, args=(model,),
                                        name="nano-speech-pack", daemon=True)
        self._thread.start()
        return {"ok": True, "started": True}

    def wait(self, timeout: float = 5.0) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _run(self, model: str) -> None:
        started = time.monotonic()
        try:
            if not self._installed():
                error = self._install()
                if error:
                    self._set(state="error", message=error)
                    return
            if not self._cached(model):
                self._set(state="downloading",
                          message=tr("A descarregar o modelo de voz ({model})…", model=model))
                try:
                    self._download(model)
                except Exception as exc:
                    logger.warning("Speech model download failed: %s", type(exc).__name__)
                    self._set(state="error",
                              message=tr("Não consegui descarregar o modelo de voz. Verifica a "
                                         "ligação à internet e tenta outra vez."))
                    return
            logger.info("Speech pack ready (model=%s, %.0fs)", model, time.monotonic() - started)
            self._set(state="done", message=tr("A voz está pronta."))
            if self._on_ready is not None:
                try:
                    self._on_ready()
                except Exception:
                    logger.debug("speech pack on_ready failed", exc_info=True)
        except Exception:
            logger.exception("Speech pack failed")
            self._set(state="error", message=tr("A instalação da voz falhou."))

    def _install(self) -> str | None:
        """pip-install the locked closure. Returns a user-facing error or None."""
        try:
            lock_text = self._lock_path.read_text(encoding="utf-8")
        except OSError:
            return tr("Falta o ficheiro requirements/optional.lock nesta instalação.")
        requirements = requirements_text(lock_text)
        if not requirements:
            return tr("O ficheiro de dependências não tem o reconhecimento de voz.")
        handle, path = tempfile.mkstemp(prefix="nano-speech-", suffix=".txt")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fp:
                fp.write(requirements)
            command = [self._python, "-m", "pip", "install", "--disable-pip-version-check",
                       "--no-input", "--require-hashes", "--no-build-isolation",
                       "--only-binary=:all:", "-r", path]
            kwargs: dict[str, Any] = {"capture_output": True, "text": True,
                                      "timeout": PIP_TIMEOUT_SECONDS}
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
            logger.warning("pip install of the speech pack failed: %s", " | ".join(tail)[:400])
            return tr("A instalação do reconhecimento de voz falhou. Verifica a ligação à "
                      "internet e tenta outra vez.")
        if not self._installed():
            return tr("A instalação terminou mas o reconhecimento de voz não ficou disponível.")
        return None

    def _download(self, model: str) -> None:
        if self._downloader is not None:
            self._downloader(model)
            return
        from faster_whisper.utils import download_model  # installed by step 1

        download_model(model)


__all__ = ["ALLOWED_MODELS", "SpeechPack", "closure", "hub_cache_dir", "model_cached",
           "package_installed", "parse_lock", "requirements_text"]
