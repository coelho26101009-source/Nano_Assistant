"""Provider routing for Nano: Groq (cloud, primary) and Ollama (local, fallback).

Groq is the normal brain. It is fast, it is free at the tier the user is on,
and — importantly on a 16 GB machine — it costs no local RAM. Ollama stays as
the local fallback and the privacy option, and its model is only loaded when a
request actually routes there.

Three modes:

    AUTO    Groq first, fall back to Ollama when Groq is unavailable.
    CLOUD   Groq only. If it is unavailable, say so; never silently downgrade.
    LOCAL   Ollama only. Nothing leaves the machine.

Whichever provider actually answered is always reported, so "fell back to
local" is visible in the UI rather than silent.
"""
from __future__ import annotations

import logging
import re
import time
from enum import Enum
from typing import Any

import httpx

from core import ollama_service, secret_store

logger = logging.getLogger("nano.providers")

GROQ_API_BASE = "https://api.groq.com/openai/v1"
GROQ_SECRET_NAME = "groq_api_key"

# Two Groq tiers. Ordinary conversation must never pay for the big model, and
# the big model must never be chosen just because a message is long -- only an
# explicit COMPLEX classification promotes a request (see core.model_selection).
#
# These defaults were chosen from a measured benchmark on this account, not
# from documentation: llama-3.1-8b-instant and llama-3.3-70b-versatile are NOT
# available here, and qwen/qwen3.6-27b leaks raw <think> reasoning into the
# answer while spending 400-700 completion tokens on a greeting.
DEFAULT_FAST_MODEL = "openai/gpt-oss-20b"
DEFAULT_COMPLEX_MODEL = "openai/gpt-oss-120b"

# Model families Nano supports on Groq. Concrete ids are discovered from the
# account at runtime -- pinning a literal id is what left the project calling a
# decommissioned model and 404-ing on every single message.
_GROQ_PREFERRED_ORDER = (
    "llama-3.3", "llama-3.1", "llama3", "openai/gpt-oss", "mixtral", "gemma",
)

# Models that exist on the account but cannot serve chat completions.
_GROQ_NON_CHAT_MARKERS = ("whisper", "guard", "tts", "embed", "prompt-guard", "safeguard", "orpheus", "playai")


class ProviderMode(str, Enum):
    AUTO = "AUTO"
    CLOUD = "CLOUD"
    LOCAL = "LOCAL"

    @classmethod
    def parse(cls, value: Any) -> "ProviderMode":
        try:
            return cls(str(value).strip().upper())
        except ValueError:
            return cls.AUTO


class ProviderState(str, Enum):
    READY = "READY"
    SETUP_REQUIRED = "SETUP_REQUIRED"   # no API key
    UNAVAILABLE = "UNAVAILABLE"          # configured but unreachable
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    NOT_INSTALLED = "NOT_INSTALLED"
    DISABLED = "DISABLED"
    ERROR = "ERROR"


# --------------------------------------------------------------------------
# Groq
# --------------------------------------------------------------------------

def groq_api_key() -> str:
    return secret_store.get_secret(GROQ_SECRET_NAME)


def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _GROQ_NON_CHAT_MARKERS)


def rank_groq_model(model_id: str) -> int:
    """Lower is better. Unknown families sort last but stay usable."""
    lowered = model_id.lower()
    for index, prefix in enumerate(_GROQ_PREFERRED_ORDER):
        if lowered.startswith(prefix) or prefix in lowered:
            return index
    return len(_GROQ_PREFERRED_ORDER)


def list_groq_models(api_key: str | None = None, *, timeout: float = 10.0) -> tuple[list[str], str | None]:
    """Chat-capable models on the account, best first. Returns (models, error)."""
    key = (api_key or groq_api_key()).strip()
    if not key:
        return [], "no_api_key"
    try:
        response = httpx.get(
            f"{GROQ_API_BASE}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
    except Exception as exc:
        return [], f"network_error: {exc}"

    if response.status_code in (401, 403):
        return [], "invalid_api_key"
    if not response.is_success:
        return [], f"http_{response.status_code}"

    try:
        ids = [str(item.get("id")) for item in response.json().get("data", []) if item.get("id")]
    except Exception as exc:
        return [], f"bad_response: {exc}"

    chat_models = sorted((m for m in ids if is_chat_model(m)), key=lambda m: (rank_groq_model(m), m))
    return chat_models, None


def test_groq(api_key: str | None = None) -> dict[str, Any]:
    """Validate a key without storing it. Used by the Test connection button."""
    started = time.monotonic()
    models, error = list_groq_models(api_key)
    latency_ms = int((time.monotonic() - started) * 1000)

    if error == "no_api_key":
        return {"ok": False, "error": "no_api_key", "detail": "Nenhuma chave de API configurada.", "models": []}
    if error == "invalid_api_key":
        return {"ok": False, "error": "invalid_api_key", "detail": "A chave de API foi recusada pelo Groq.", "models": []}
    if error:
        return {"ok": False, "error": error, "detail": f"Não foi possível contactar o Groq: {error}", "models": []}
    if not models:
        return {"ok": False, "error": "no_chat_models", "detail": "A chave é válida mas a conta não tem modelos de chat.", "models": []}

    return {
        "ok": True,
        "detail": f"Ligação estabelecida ({len(models)} modelos disponíveis).",
        "models": models,
        "suggested_model": models[0],
        "latency_ms": latency_ms,
    }


def describe_groq(configured_model: str = "", complex_model: str = "") -> dict[str, Any]:
    """Groq status for the UI. Never returns or logs the key itself.

    ``configured_model`` is the fast conversation model and ``complex_model``
    the strong one; both are reported so Settings can show the pair honestly
    rather than implying a single model answers everything.
    """
    fast = str(configured_model or DEFAULT_FAST_MODEL)
    strong = str(complex_model or DEFAULT_COMPLEX_MODEL)
    tiers = {"fast": fast, "complex": strong}
    secret = secret_store.describe(GROQ_SECRET_NAME)
    if not secret["configured"]:
        return {
            "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
            "state": ProviderState.SETUP_REQUIRED.value,
            "model": fast, "models": [], "secret": secret, "tiers": tiers,
            "detail": "Adiciona uma chave de API do Groq nas Definições para usar a cloud.",
        }

    models, error = list_groq_models()
    if error:
        state = ProviderState.ERROR if error == "invalid_api_key" else ProviderState.UNAVAILABLE
        detail = (
            "A chave de API do Groq foi recusada. Verifica-a nas Definições."
            if error == "invalid_api_key"
            else f"O Groq não está acessível ({error})."
        )
        return {
            "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
            "state": state.value, "model": fast, "models": [],
            "secret": secret, "tiers": tiers, "detail": detail,
        }

    # Both tiers are validated against the account. A configured model that no
    # longer exists is reported as a configuration error -- Nano never silently
    # substitutes a different model, because that hid a decommissioned model
    # 404-ing on every message.
    fast_ok = fast in models
    strong_ok = strong in models
    missing = [m for m, ok in ((fast, fast_ok), (strong, strong_ok)) if not ok]

    if fast_ok and strong_ok:
        detail = f"Pronto. Conversa: '{fast}'. Complexo: '{strong}'."
    elif fast_ok:
        detail = (f"Conversa pronta com '{fast}'. O modelo complexo '{strong}' não existe "
                  f"nesta conta; pedidos complexos usam '{fast}'.")
    else:
        detail = (f"O modelo '{', '.join(missing)}' não existe nesta conta. "
                  f"Disponíveis: {', '.join(models[:4])}.")

    return {
        "id": "groq", "name": "Groq", "kind": "cloud", "role": "primary",
        # Conversation is what makes Nano usable: if the fast model resolves,
        # Groq is READY even when the optional strong model is missing.
        "state": ProviderState.READY.value if fast_ok else ProviderState.MODEL_UNAVAILABLE.value,
        "model": fast, "models": models, "secret": secret,
        "tiers": {"fast": fast, "complex": strong if strong_ok else fast},
        "tiers_ok": {"fast": fast_ok, "complex": strong_ok},
        "detail": detail,
    }


# --------------------------------------------------------------------------
# Ollama
# --------------------------------------------------------------------------

def describe_ollama(model: str, base_url: str, *, local_enabled: bool = True) -> dict[str, Any]:
    status = ollama_service.describe(model, base_url, local_enabled=local_enabled)
    mapping = {
        ollama_service.OllamaState.READY: ProviderState.READY,
        ollama_service.OllamaState.MODEL_UNAVAILABLE: ProviderState.MODEL_UNAVAILABLE,
        ollama_service.OllamaState.OLLAMA_UNAVAILABLE: ProviderState.UNAVAILABLE,
        ollama_service.OllamaState.NOT_INSTALLED: ProviderState.NOT_INSTALLED,
        ollama_service.OllamaState.DISABLED: ProviderState.DISABLED,
    }
    state = mapping.get(status["state"], ProviderState.UNAVAILABLE)
    return {
        "id": "ollama", "name": "Ollama", "kind": "local", "role": "fallback",
        "state": state.value, "model": status["model"], "models": status.get("installed", []),
        "secret": {"configured": True, "masked": "", "source": "none", "encrypted": False},
        "detail": status.get("detail", ""),
        "url": status.get("url", base_url),
    }


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

def groq_model_for(groq: dict, tier: str = "FAST") -> str:
    """Pick the Groq model for a tier, falling back to the configured default.

    ``groq`` is a describe_groq() payload. The tier comes from
    ``core.model_selection.tier_for``; anything unrecognised is treated as FAST
    so an unexpected value can never silently escalate cost.
    """
    tiers = groq.get("tiers") or {}
    if str(tier).upper() == "STRONG":
        return str(tiers.get("complex") or groq.get("model") or DEFAULT_COMPLEX_MODEL)
    return str(tiers.get("fast") or groq.get("model") or DEFAULT_FAST_MODEL)


def resolve_route(mode: ProviderMode, groq: dict, ollama: dict, *, tier: str = "FAST") -> dict[str, Any]:
    """Decide which provider AND model a request should use, and say why.

    This is the single authoritative routing decision. ``Brain.chat`` calls it
    directly; nothing else may decide a provider. Never returns a provider it
    believes is unusable without also flagging the decision, so the UI can show
    "fell back to local" rather than pretending the primary answered.
    """
    groq_ready = groq["state"] == ProviderState.READY.value
    ollama_ready = ollama["state"] == ProviderState.READY.value
    cloud_model = groq_model_for(groq, tier)

    if mode == ProviderMode.LOCAL:
        return {
            "provider": "ollama", "model": ollama["model"], "usable": ollama_ready,
            "fallback": False, "mode": mode.value, "tier": tier,
            "reason": "Modo Local: apenas o Ollama é usado." if ollama_ready else ollama["detail"],
        }

    if mode == ProviderMode.CLOUD:
        return {
            "provider": "groq", "model": cloud_model, "usable": groq_ready,
            "fallback": False, "mode": mode.value, "tier": tier,
            "reason": "Modo Cloud: apenas o Groq é usado." if groq_ready else groq["detail"],
        }

    # AUTO
    if groq_ready:
        return {
            "provider": "groq", "model": cloud_model, "usable": True,
            "fallback": False, "mode": mode.value, "tier": tier,
            "reason": "Groq disponível (sem custo de RAM local).",
        }
    if ollama_ready:
        return {
            "provider": "ollama", "model": ollama["model"], "usable": True,
            # 'tier' belongs on EVERY branch. It was set on CLOUD and LOCAL but
            # dropped on both AUTO branches, so Brain.last_metadata["tier"] read
            # None exactly when Nano had fallen back -- the case the diagnostics
            # panel most needs to explain.
            "fallback": True, "mode": mode.value, "tier": tier,
            "reason": f"Groq indisponível — a usar o Ollama local. {groq['detail']}",
        }
    return {
        "provider": "none", "model": "", "usable": False,
        "fallback": False, "mode": mode.value, "tier": tier,
        "reason": f"Nenhum provedor disponível. Groq: {groq['detail']} Ollama: {ollama['detail']}",
    }


def parse_rate_limit(headers: Any) -> dict[str, Any]:
    """Turn Groq's rate-limit headers into something the UI can explain.

    A 429 used to be invisible: the SDK slept through it and the user saw a
    30-46 second freeze with no explanation. Nano now reads the real headers
    and tells the user how long the wait is.
    """
    def _get(name: str) -> str:
        try:
            return str(headers.get(name) or "")
        except Exception:
            return ""

    def _seconds(raw: str) -> float | None:
        """Groq sends either plain seconds ("12") or "1m31.2s"."""
        raw = raw.strip()
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            pass
        match = re.match(r"(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?$", raw)
        if not match or not any(match.groups()):
            return None
        minutes = float(match.group(1) or 0.0)
        seconds = float(match.group(2) or 0.0)
        return minutes * 60.0 + seconds

    retry_after = _seconds(_get("retry-after"))
    reset_tokens = _seconds(_get("x-ratelimit-reset-tokens"))
    # retry-after is the authoritative "come back then"; the token reset is the
    # honest upper bound when the header is absent.
    wait = retry_after if retry_after is not None else reset_tokens

    def _int(raw: str) -> int | None:
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    return {
        "retry_after_seconds": retry_after,
        "reset_tokens_seconds": reset_tokens,
        "wait_seconds": wait,
        "limit_tokens": _int(_get("x-ratelimit-limit-tokens")),
        "remaining_tokens": _int(_get("x-ratelimit-remaining-tokens")),
        "limit_requests": _int(_get("x-ratelimit-limit-requests")),
        "remaining_requests": _int(_get("x-ratelimit-remaining-requests")),
    }


def rate_limit_message(info: dict[str, Any]) -> str:
    """Portuguese explanation of a 429. Never just 'Error'."""
    wait = info.get("wait_seconds")
    if wait and wait > 0:
        rounded = int(wait) if wait >= 1 else 1
        return (f"Limite temporário da Groq atingido. "
                f"Disponível novamente em ~{rounded} s.")
    return "Limite temporário da Groq atingido. Tenta novamente dentro de momentos."


__all__ = [
    "DEFAULT_COMPLEX_MODEL",
    "DEFAULT_FAST_MODEL",
    "GROQ_SECRET_NAME",
    "ProviderMode",
    "ProviderState",
    "describe_groq",
    "describe_ollama",
    "groq_api_key",
    "groq_model_for",
    "list_groq_models",
    "parse_rate_limit",
    "rank_groq_model",
    "rate_limit_message",
    "resolve_route",
    "test_groq",
]
