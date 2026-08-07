"""HELIOS local runtime helpers.

Keeps the offline fallback lightweight by selecting a Qwen2.5 model based on
available RAM. Ollama remains an optional dependency: cloud/Groq remains the
primary brain when configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger("helios.local_runtime")

DEFAULT_OLLAMA_URL = "http://localhost:11434"


@dataclass(frozen=True)
class LocalProfile:
    model: str
    ram_gb: float
    reason: str


def detect_profile() -> LocalProfile:
    """Choose a conservative model for the current machine.

    Qwen2.5 0.5B is ~398 MB, 1.5B is ~986 MB and 3B is ~1.9 GB in the
    standard Ollama Q4 variants. We intentionally stay conservative because
    HELIOS itself also uses RAM for Electron, Python, browser/RAG and voice.
    """
    try:
        import psutil

        ram_gb = psutil.virtual_memory().total / (1024**3)
    except Exception:
        ram_gb = 8.0

    if ram_gb < 6:
        return LocalProfile("qwen2.5:0.5b-instruct", ram_gb, "RAM limitada")
    if ram_gb < 12:
        return LocalProfile("qwen2.5:1.5b-instruct", ram_gb, "equilíbrio qualidade/consumo")
    return LocalProfile("qwen2.5:3b", ram_gb, "mais capacidade disponível")


def choose_model(config: dict | None = None) -> LocalProfile:
    cfg = config or {}
    local = cfg.get("local") or {}
    configured = str(local.get("model") or "auto").strip()

    profile = detect_profile()
    if configured.lower() in {"", "auto", "automatic", "automatico", "automático"}:
        return profile

    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024**3)
    except Exception:
        ram_gb = profile.ram_gb

    return LocalProfile(configured, ram_gb, "modelo definido na configuração")


async def ollama_available(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            response = await client.get(base_url.rstrip("/") + "/api/tags")
            return response.is_success
    except Exception:
        return False


async def model_available(model: str, base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(base_url.rstrip("/") + "/api/tags")
            if not response.is_success:
                return False
            names = {m.get("name") for m in response.json().get("models", [])}
            return model in names or model.split(":")[0] in names
    except Exception:
        return False


def profile_summary(profile: LocalProfile) -> dict:
    return {
        "model": profile.model,
        "ram_gb": round(profile.ram_gb, 1),
        "reason": profile.reason,
    }
