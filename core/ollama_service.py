"""Detect, and if necessary start, the local Ollama server.

Design rules this module exists to enforce:

* **Reuse before start.** The API is probed first. If Ollama is already running
  — started by the user or by a previous Nano session — we attach to it and
  never spawn a second server.
* **Server only, never a model.** Starting `ollama serve` brings up the HTTP API
  and nothing else. We never send a warm-up inference, never `pull`, and never
  preload a model. On a 16 GB machine the idle cost of the server is tens of
  megabytes; an 8B model is several gigabytes, so it must only load when a real
  user request needs it.
* **Never download.** No `ollama pull`, ever. A missing model is reported, not
  fetched.
* **Nano does not own the user's Ollama.** We do not kill it on shutdown: it is
  a shared background service, other tools may be using it, and the model
  unloads by itself via OLLAMA_KEEP_ALIVE.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx

logger = logging.getLogger("nano.ollama")

DEFAULT_BASE_URL = "http://127.0.0.1:11434"

# How long a model stays resident after its last use. Keeping this modest
# matters on 16 GB: an idle 8B model otherwise holds gigabytes indefinitely.
DEFAULT_KEEP_ALIVE = "5m"

# Where Ollama installs itself on Windows, beyond whatever is on PATH.
_WINDOWS_CANDIDATES = (
    r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe",
    r"%PROGRAMFILES%\Ollama\ollama.exe",
    r"%PROGRAMFILES(X86)%\Ollama\ollama.exe",
    r"%USERPROFILE%\AppData\Local\Programs\Ollama\ollama.exe",
)


class OllamaState:
    """States the UI may display. Nothing here is assumed — each is measured."""

    READY = "READY"                          # API up and the configured model is installed
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"  # API up, configured model not installed
    OLLAMA_UNAVAILABLE = "OLLAMA_UNAVAILABLE"  # binary exists but the API is not answering
    NOT_INSTALLED = "OLLAMA_NOT_INSTALLED"   # no ollama executable found
    DISABLED = "DISABLED"                    # local models switched off in config


def find_executable() -> str | None:
    """Locate ollama.exe on PATH or in the standard Windows install locations."""
    found = shutil.which("ollama")
    if found:
        return found
    if os.name == "nt":
        for raw in _WINDOWS_CANDIDATES:
            candidate = Path(os.path.expandvars(raw))
            if candidate.exists():
                return str(candidate)
    return None


def api_available(base_url: str = DEFAULT_BASE_URL, *, timeout: float = 2.0) -> bool:
    """One cheap GET. This is the only thing that decides 'is Ollama up'."""
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
        return response.is_success
    except Exception:
        return False


def list_models(base_url: str = DEFAULT_BASE_URL, *, timeout: float = 4.0) -> list[str]:
    """Installed models. Read-only: /api/tags never loads anything into RAM."""
    try:
        response = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
        response.raise_for_status()
        return [str(item.get("name")) for item in response.json().get("models", []) if item.get("name")]
    except Exception:
        return []


def model_installed(model: str, installed: list[str]) -> bool:
    """Match a configured tag against installed names, tolerating ':latest'."""
    if not model:
        return False
    wanted = {model, f"{model}:latest"}
    if model.endswith(":latest"):
        wanted.add(model.removesuffix(":latest"))
    return any(name in wanted for name in installed)


def ensure_running(
    base_url: str = DEFAULT_BASE_URL,
    *,
    autostart: bool = True,
    timeout_seconds: float = 25.0,
    keep_alive: str = DEFAULT_KEEP_ALIVE,
) -> dict:
    """Make the Ollama API available, starting the server only if needed.

    Returns a dict describing what happened:
        available   bool   the API answers now
        started     bool   True only if THIS call spawned the server
        reused      bool   True if it was already running
        executable  str|None
        detail      str    human-readable explanation
    """
    if api_available(base_url):
        # Already up — attach to it. This is what stops duplicate servers when
        # the user has Ollama Desktop open or restarts Nano.
        logger.info("Ollama já está a correr em %s (a reutilizar).", base_url)
        return {
            "available": True, "started": False, "reused": True,
            "executable": find_executable(),
            "detail": "Ollama já estava a correr; a reutilizar a instância existente.",
        }

    executable = find_executable()
    if executable is None:
        return {
            "available": False, "started": False, "reused": False, "executable": None,
            "detail": "O Ollama não está instalado (ollama.exe não encontrado). Instala em https://ollama.com para usar modelos locais.",
        }

    if not autostart:
        return {
            "available": False, "started": False, "reused": False, "executable": executable,
            "detail": "Ollama está instalado mas não está a correr, e o arranque automático está desligado.",
        }

    logger.info("Ollama não está a responder; a arrancar o servidor: %s serve", executable)
    env = dict(os.environ)
    # Applies to models this server loads later; the server itself loads none.
    env.setdefault("OLLAMA_KEEP_ALIVE", keep_alive)

    try:
        creation_flags = 0
        if os.name == "nt":
            # No console window, and detached from Nano's process group so that
            # closing Nano does not take the user's model server down with it.
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen(
            [executable, "serve"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creation_flags,
        )
    except Exception as exc:
        logger.warning("Falha ao arrancar o Ollama: %s", exc)
        return {
            "available": False, "started": False, "reused": False, "executable": executable,
            "detail": f"Não foi possível arrancar o Ollama: {exc}",
        }

    # `ollama serve` binds its port in a second or two; poll rather than sleep.
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if api_available(base_url, timeout=1.0):
            elapsed = timeout_seconds - (deadline - time.monotonic())
            logger.info("Ollama pronto ao fim de %.1fs (nenhum modelo carregado).", elapsed)
            return {
                "available": True, "started": True, "reused": False, "executable": executable,
                "detail": "Ollama arrancado pelo Nano. Nenhum modelo carregado até ser preciso.",
            }
        time.sleep(0.5)

    return {
        "available": False, "started": True, "reused": False, "executable": executable,
        "detail": f"O Ollama foi arrancado mas a API não respondeu em {timeout_seconds:.0f}s.",
    }


def describe(model: str, base_url: str = DEFAULT_BASE_URL, *, local_enabled: bool = True) -> dict:
    """Full, honest status for the UI. Performs no start and no download."""
    if not local_enabled:
        return {
            "state": OllamaState.DISABLED, "ollamaUp": False, "modelReady": False,
            "model": model, "url": base_url, "installed": [],
            "detail": "Modelos locais desativados na configuração.",
        }

    if not api_available(base_url):
        installed_binary = find_executable()
        state = OllamaState.OLLAMA_UNAVAILABLE if installed_binary else OllamaState.NOT_INSTALLED
        detail = (
            f"O Ollama está instalado mas a API não responde em {base_url}."
            if installed_binary
            else "O Ollama não está instalado; os modelos locais não estão disponíveis."
        )
        return {
            "state": state, "ollamaUp": False, "modelReady": False,
            "model": model, "url": base_url, "installed": [], "detail": detail,
        }

    installed = list_models(base_url)
    if not model_installed(model, installed):
        return {
            "state": OllamaState.MODEL_UNAVAILABLE, "ollamaUp": True, "modelReady": False,
            "model": model, "url": base_url, "installed": installed,
            # Never silently substitute a different model.
            "detail": (
                f"O Ollama está a correr mas o modelo '{model}' não está instalado. "
                f"Instalados: {', '.join(installed) or 'nenhum'}. Instala com: ollama pull {model}"
            ),
        }

    return {
        "state": OllamaState.READY, "ollamaUp": True, "modelReady": True,
        "model": model, "url": base_url, "installed": installed,
        "detail": f"Ollama disponível com '{model}'.",
    }


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_KEEP_ALIVE",
    "OllamaState",
    "api_available",
    "describe",
    "ensure_running",
    "find_executable",
    "list_models",
    "model_installed",
]
