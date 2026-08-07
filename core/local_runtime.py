"""Local-model detection, readiness and diagnostics for HELIOS."""
from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import AsyncIterator

import httpx

logger = logging.getLogger("helios.local_runtime")
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"

@dataclass(frozen=True)
class LocalProfile:
    model: str
    ram_gb: float
    reason: str

def detect_profile() -> LocalProfile:
    try:
        import psutil
        ram_gb = psutil.virtual_memory().total / (1024 ** 3)
    except Exception:
        ram_gb = 8.0
    if ram_gb < 6:
        return LocalProfile("qwen2.5:0.5b", ram_gb, "RAM limitada")
    if ram_gb < 12:
        return LocalProfile("qwen2.5:1.5b", ram_gb, "equilíbrio qualidade/consumo")
    return LocalProfile("qwen2.5:3b", ram_gb, "mais capacidade disponível")

def choose_model(config: dict | None = None) -> LocalProfile:
    local = (config or {}).get("local") or {}
    configured = str(local.get("model") or "auto").strip()
    profile = detect_profile()
    if configured.lower() in {"", "auto", "automatic", "automatico", "automático"}:
        return profile
    return LocalProfile(configured, profile.ram_gb, "modelo definido na configuração")

async def ollama_available(base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=1.5) as client:
            return (await client.get(base_url.rstrip("/") + "/api/tags")).is_success
    except Exception:
        return False

async def model_available(model: str, base_url: str = DEFAULT_OLLAMA_URL) -> bool:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(base_url.rstrip("/") + "/api/tags")
            if not response.is_success:
                return False
            names = {str(m.get("name")) for m in response.json().get("models", [])}
            return model in names
    except Exception:
        return False

async def pull_model(model: str, base_url: str = DEFAULT_OLLAMA_URL) -> AsyncIterator[dict]:
    """Pull a model from an already-running Ollama service.

    This deliberately does not install software or execute shell commands. The
    caller can present progress in the UI and decide when the local service is
    enabled. Errors are returned as structured events rather than leaking URLs
    or credentials into the model conversation.
    """
    url = base_url.rstrip("/") + "/api/pull"
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream("POST", url, json={"name": model, "stream": True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    yield {
                        "status": str(data.get("status") or ""),
                        "completed": data.get("completed"),
                        "total": data.get("total"),
                        "done": bool(data.get("done")),
                    }
    except Exception:
        logger.exception("Falha ao descarregar modelo local '%s'", model)
        yield {"ok": False, "error": "local_model_pull_failed", "done": True}

def profile_summary(profile: LocalProfile) -> dict:
    return {"model": profile.model, "ram_gb": round(profile.ram_gb, 1), "reason": profile.reason}
