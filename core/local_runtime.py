"""Local-model detection and diagnostics for HELIOS."""
from __future__ import annotations
from dataclasses import dataclass
import httpx
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

def profile_summary(profile: LocalProfile) -> dict:
    return {"model": profile.model, "ram_gb": round(profile.ram_gb, 1), "reason": profile.reason}
