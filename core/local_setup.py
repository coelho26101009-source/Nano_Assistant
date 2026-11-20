"""A light local model as NANO's second option, set up in one click.

THE IDEA
--------
NANO answers with a cloud model when it can and falls back to a model on the
person's own PC when the cloud fails or hits its limit (AUTO mode, see
``core.providers``). That fallback only helps if a local model is installed,
and choosing one means knowing your RAM, your graphics card and Ollama's tag
names. This module does that part:

1. **Look at the PC**: RAM, processor cores, an NVIDIA card's memory (when
   ``nvidia-smi`` exists) and free disk space. Nothing is sent anywhere.
2. **Recommend ONE light model that does everything**: writes, sees images
   and the screen, and calls NANO's tools. Qwen3-VL does all three, in sizes
   from about 2 GB, so a single download covers chat, vision and PC control.
3. **Download it when the person presses the button**, with progress, through
   Ollama's own ``/api/pull``, and make it the local model.

WHAT IT NEVER DOES
------------------
It never downloads on its own: ``ollama_service`` keeps its rule that NANO
does not pull a model behind the person's back, and this is the one place a
pull happens, started by a click. Only models from ``RECOMMENDED`` can be
pulled, so the window cannot be used to fetch arbitrary names. It does not
install Ollama itself (an installer needs the person's consent on Windows);
it links to the official download page instead.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import httpx

from core import http_tls
from core.i18n import tr

logger = logging.getLogger("nano.local_setup")

OLLAMA_DOWNLOAD_URL = "https://ollama.com/download"


@dataclass(frozen=True)
class LocalModel:
    name: str
    size_gb: float          # approximate download size
    min_ram_gb: float       # below this the PC struggles
    label: str              # short, in Portuguese (translated with tr)


#: The light models NANO offers, smallest first. All of them write, see
#: images and call tools, so one download covers everything.
RECOMMENDED: tuple[LocalModel, ...] = (
    LocalModel("qwen3-vl:2b", 1.9, 4.0, "Muito leve: para PCs com pouca memória"),
    LocalModel("qwen3-vl:4b", 3.3, 8.0, "Leve e equilibrado: o recomendado para a maioria dos PCs"),
    LocalModel("qwen3-vl:8b", 6.1, 16.0, "Mais inteligente: para PCs com placa gráfica de 8 GB ou mais"),
)
ALLOWED_MODELS = frozenset(model.name for model in RECOMMENDED)


# ── the PC ────────────────────────────────────────────────────────────────

def _ram_gb() -> float:
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except Exception:
        return 0.0


def _cpu_cores() -> int:
    try:
        import psutil
        return int(psutil.cpu_count(logical=False) or os.cpu_count() or 0)
    except Exception:
        return int(os.cpu_count() or 0)


def _nvidia_gpu() -> dict[str, Any] | None:
    """Name and memory of the first NVIDIA card, or None. Bounded to 3 s."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3.0, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = (out.stdout or "").strip().splitlines()[0]
        name, memory = (part.strip() for part in line.split(",", 1))
        return {"name": name[:80], "vram_gb": round(float(memory) / 1024, 1)}
    except Exception:
        return None


def _free_disk_gb() -> float:
    target = Path(os.environ.get("OLLAMA_MODELS") or Path.home())
    try:
        return round(shutil.disk_usage(target if target.exists() else Path.home()).free / (1024 ** 3), 1)
    except Exception:
        return 0.0


#: Swappable in tests.
PROBES: dict[str, Callable[[], Any]] = {
    "ram_gb": _ram_gb, "cpu_cores": _cpu_cores, "gpu": _nvidia_gpu, "free_disk_gb": _free_disk_gb,
}


#: What a PC sold with 16 GB / 8 GB of RAM reports, at the least.
RAM_16_GB = 14.5
RAM_8_GB = 7.0


def detect_hardware() -> dict[str, Any]:
    return {key: probe() for key, probe in PROBES.items()}


def recommend(hardware: dict[str, Any]) -> dict[str, Any]:
    """The model to offer for this PC, and why, in the person's language."""
    ram = float(hardware.get("ram_gb") or 0)
    gpu = hardware.get("gpu") or None
    vram = float(gpu.get("vram_gb") or 0) if gpu else 0.0
    # Windows reports less than the RAM on the box: a "16 GB" PC shows 15.7,
    # or under 15 when integrated graphics reserve some. Exact 16/8 sent every
    # such PC one model down. The same holds for an "8 GB" graphics card.
    if ram >= RAM_16_GB and vram >= 7.5:
        model = RECOMMENDED[2]
        why = tr("Tens {ram} GB de RAM e uma placa gráfica com {vram} GB: dá para o modelo maior.",
                 ram=ram, vram=vram)
    elif ram >= RAM_8_GB or (gpu and vram >= 3.5):
        model = RECOMMENDED[1]
        why = tr("Com {ram} GB de RAM, este é o melhor equilíbrio entre rapidez e inteligência.", ram=ram)
    else:
        model = RECOMMENDED[0]
        why = tr("Com {ram} GB de RAM, um modelo muito leve é o que corre bem.", ram=ram)
    warnings = []
    if ram and ram < 4:
        warnings.append(tr("O teu PC tem pouca memória: a IA local vai ser lenta. A nuvem continua a ser a melhor opção."))
    free = float(hardware.get("free_disk_gb") or 0)
    if free and free < model.size_gb + 1:
        warnings.append(tr("Falta espaço no disco: precisas de cerca de {need} GB livres.",
                           need=round(model.size_gb + 1, 1)))
    return {"model": model.name, "sizeGb": model.size_gb, "label": tr(model.label),
            "why": why, "warnings": warnings}


def choices() -> list[dict[str, Any]]:
    return [{"model": m.name, "sizeGb": m.size_gb, "minRamGb": m.min_ram_gb, "label": tr(m.label)}
            for m in RECOMMENDED]


# ── the download ──────────────────────────────────────────────────────────

def _stream_pull(base_url: str, model: str) -> Iterable[dict]:
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=5.0, read=600.0),
                      verify=http_tls.shared_context()) as client:
        with client.stream("POST", base_url.rstrip("/") + "/api/pull",
                           json={"model": model, "stream": True}) as response:
            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}")
            for line in response.iter_lines():
                if line.strip():
                    try:
                        yield json.loads(line)
                    except ValueError:
                        continue


#: (base_url, model) -> iterator of Ollama's progress objects. Swappable in tests.
PULL: Callable[[str, str], Iterable[dict]] = _stream_pull


class Downloader:
    """One download at a time, with progress the window polls."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] = {"state": "idle"}
        self._thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def _set(self, **values: Any) -> None:
        with self._lock:
            self._state = {**self._state, **values}

    def start(self, model: str, base_url: str, *,
              on_done: Callable[[str], None] | None = None) -> dict[str, Any]:
        if model not in ALLOWED_MODELS:
            return {"ok": False, "error": "model_not_offered"}
        with self._lock:
            if self._state.get("state") == "downloading":
                return {"ok": False, "error": "already_downloading"}
            self._state = {"state": "downloading", "model": model, "percent": 0,
                           "completedGb": 0.0, "totalGb": 0.0, "message": tr("A preparar..."),
                           "startedAt": time.time()}
        self._thread = threading.Thread(target=self._run, args=(model, base_url, on_done),
                                        name="nano-local-pull", daemon=True)
        self._thread.start()
        return {"ok": True, "model": model}

    def wait(self, timeout: float = 5.0) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _run(self, model: str, base_url: str, on_done: Callable[[str], None] | None) -> None:
        layers: dict[str, tuple[int, int]] = {}
        succeeded = False
        try:
            for event in PULL(base_url, model):
                if event.get("error"):
                    raise RuntimeError(str(event["error"])[:200])
                digest = str(event.get("digest") or "")
                total = int(event.get("total") or 0)
                if digest and total:
                    layers[digest] = (int(event.get("completed") or 0), total)
                done = sum(c for c, _ in layers.values())
                whole = sum(t for _, t in layers.values())
                self._set(percent=int(done * 100 / whole) if whole else 0,
                          completedGb=round(done / 1e9, 2), totalGb=round(whole / 1e9, 2),
                          message=tr("A descarregar {model}...", model=model))
                if event.get("status") == "success":
                    succeeded = True
                    break
            if not succeeded:
                # The stream ended without Ollama's "success": the connection
                # dropped or Ollama closed mid-download. The model is NOT
                # installed, and it must not become NANO's local model.
                raise RuntimeError("pull ended without success")
            if on_done:
                on_done(model)
            self._set(state="done", percent=100,
                      message=tr("{model} está instalado e é agora o modelo local do NANO.", model=model))
        except Exception as exc:
            logger.warning("Download de %s falhou: %s", model, str(exc)[:120])
            self._set(state="error", message=tr(
                "Não foi possível descarregar {model}. Confirma que o Ollama está aberto e que "
                "tens internet, e tenta de novo.", model=model), detail=str(exc)[:200])


__all__ = ["ALLOWED_MODELS", "Downloader", "LocalModel", "OLLAMA_DOWNLOAD_URL", "RECOMMENDED",
           "choices", "detect_hardware", "recommend"]
