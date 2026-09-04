"""Provider routing for Nano: one or more cloud providers, and Ollama locally.

MODE IS NOT PROVIDER
--------------------
This module used to equate CLOUD with Groq. It no longer does, and the
distinction is the point of the file:

    MODE      what the user asked for      AUTO | CLOUD | LOCAL
    PROVIDER  who actually answers          google | groq | mistral | ollama

    AUTO    the preferred cloud provider first, then the other configured
            cloud providers, then Ollama. Every hop is reported.
    CLOUD   the preferred cloud provider only. If it is unavailable, say so;
            never silently downgrade to the local model.
    LOCAL   Ollama only. Nothing leaves the machine -- not even a status probe
            (see core.provider_status.describe_all).

LOCAL's meaning is unchanged by the arrival of a second cloud provider, and
that is deliberate: a mode whose privacy guarantee shifted because a new
provider was added would be worse than useless.

Whichever provider actually answered is always reported, so "fell back" is
visible in the UI rather than silent.

ADDING THE NEXT PROVIDER
------------------------
A cloud provider is a ``describe_*`` function returning the payload shape below
plus an entry in ``CLOUD_PROVIDER_IDS``. Routing, cooldowns, the failure
taxonomy, the settings surface and the model switcher are all keyed on
``payload["id"]`` and need no change. Mistral and SambaNova are meant to arrive
that way.
"""
from __future__ import annotations

import logging
import re
import time
from enum import Enum
from typing import Any

import httpx

from core import google_provider, mistral_provider, ollama_service, secret_store

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


class ProviderId(str, Enum):
    """Every provider Nano can route to. The value IS the payload's "id"."""

    GOOGLE = "google"
    GROQ = "groq"
    MISTRAL = "mistral"
    OLLAMA = "ollama"

    @classmethod
    def parse(cls, value: Any) -> "ProviderId | None":
        try:
            return cls(str(value).strip().lower())
        except ValueError:
            return None


#: Cloud providers, in the order AUTO tries them once the user's preferred one
#: has been moved to the front. Adding SambaNova later means adding an id here
#: and a describe_* function -- nothing in the Brain changes, which is what
#: adding Mistral demonstrated.
CLOUD_PROVIDER_IDS: tuple[str, ...] = (
    ProviderId.GOOGLE.value, ProviderId.GROQ.value, ProviderId.MISTRAL.value,
)

#: Which cloud provider AUTO and CLOUD prefer when the user has expressed no
#: choice. Groq, deliberately: it is the measured baseline this account has
#: been running on, and a new provider does not become the default until a
#: benchmark says it earned it.
DEFAULT_CLOUD_PROVIDER = ProviderId.GROQ.value

#: Human names, for sentences shown to the user.
PROVIDER_NAMES: dict[str, str] = {
    ProviderId.GOOGLE.value: "Google",
    ProviderId.GROQ.value: "Groq",
    ProviderId.MISTRAL.value: "Mistral",
    ProviderId.OLLAMA.value: "Ollama",
}


def provider_name(provider: Any) -> str:
    key = str(provider or "").strip().lower()
    return PROVIDER_NAMES.get(key, key.capitalize() or "Provedor")


def parse_preferred_cloud(value: Any) -> str:
    """Normalise a stored preference. Anything unknown falls back to the
    default rather than routing to a provider that does not exist."""
    parsed = ProviderId.parse(value)
    if parsed is None or parsed.value not in CLOUD_PROVIDER_IDS:
        return DEFAULT_CLOUD_PROVIDER
    return parsed.value


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


def cloud_model_for(payload: dict, tier: str = "FAST") -> str:
    """The model a cloud provider should use for a tier. Provider-agnostic.

    Same rule as ``groq_model_for`` and the same conservatism -- an
    unrecognised tier is FAST, never STRONG -- but without a Groq-shaped
    default, because a default belonging to one vendor has no meaning for
    another. A provider with nothing configured returns "", and the caller
    reports that honestly rather than substituting a model nobody chose.
    """
    tiers = payload.get("tiers") or {}
    if str(tier).upper() == "STRONG":
        return str(tiers.get("complex") or payload.get("model") or "")
    return str(tiers.get("fast") or payload.get("model") or "")


def cloud_candidates(preferred: str | None,
                     payloads: dict[str, dict]) -> list[tuple[str, dict]]:
    """(provider_id, payload) pairs in the order AUTO should try them.

    The preferred provider is always first. The rest keep the declaration order
    of ``CLOUD_PROVIDER_IDS`` -- a stable, explainable order beats one derived
    from measured latency, which would make the same question route differently
    on two consecutive turns for reasons the user cannot see.

    The id comes from the MAPPING KEY, never from ``payload["id"]``. The key is
    how the caller looked the provider up and is therefore the authoritative
    answer; trusting the body would let a payload that omits the field route to
    a provider literally named "None".

    A provider with no payload (never described, e.g. because the mode forbade
    contacting it) is simply absent.
    """
    chosen = str(preferred or DEFAULT_CLOUD_PROVIDER).lower()
    order = [chosen] + [pid for pid in CLOUD_PROVIDER_IDS if pid != chosen]
    seen: set[str] = set()
    result: list[tuple[str, dict]] = []
    for pid in order:
        payload = payloads.get(pid)
        if payload and pid not in seen:
            seen.add(pid)
            result.append((pid, payload))
    return result


def _is_ready(payload: dict | None) -> bool:
    return bool(payload) and payload.get("state") == ProviderState.READY.value


def _route(provider: str, model: str, *, usable: bool, fallback: bool,
           mode: ProviderMode, tier: str, reason: str,
           alternatives: list[str] | None = None) -> dict[str, Any]:
    """One routing decision, in the single shape every caller reads.

    ``alternatives`` is the ordered list of OTHER cloud providers that were
    ready at decision time. The Brain uses it to fail over inside a turn
    without re-probing, and it is what makes "o Gemini atingiu o limite, a usar
    o Groq" a decision rather than a retry loop.
    """
    return {
        "provider": provider, "model": model, "usable": usable,
        "fallback": fallback, "mode": mode.value, "tier": tier,
        "reason": reason, "alternatives": list(alternatives or []),
    }


def resolve_route(mode: ProviderMode, groq: dict, ollama: dict, *,
                  tier: str = "FAST", google: dict | None = None,
                  mistral: dict | None = None,
                  preferred: str | None = None) -> dict[str, Any]:
    """Decide which provider AND model a request should use, and say why.

    This is the single authoritative routing decision. ``Brain.chat`` calls it
    directly; nothing else may decide a provider. It never returns a provider
    it believes is unusable without also flagging the decision, so the UI can
    show "fell back to local" rather than pretending the primary answered.

    EVERY cloud provider but Groq is optional, and an absent one is simply
    not a candidate. When none is supplied the behaviour is exactly what it
    was when Groq was the only cloud provider, which is what keeps a machine
    that has never configured Google or Mistral routing the way it always did.

    One keyword per provider rather than a mapping is deliberate: the caller
    has to name what it is passing, so a payload cannot be filed under the
    wrong provider by a typo in a dictionary key. The ids the router then
    uses come from :func:`cloud_candidates`, which reads the mapping KEY and
    never ``payload["id"]``.
    """
    payloads = {ProviderId.GROQ.value: groq}
    if google:
        payloads[ProviderId.GOOGLE.value] = google
    if mistral:
        payloads[ProviderId.MISTRAL.value] = mistral
    clouds = cloud_candidates(preferred, payloads)
    ollama_ready = _is_ready(ollama)

    if mode == ProviderMode.LOCAL:
        return _route("ollama", ollama["model"], usable=ollama_ready, fallback=False,
                      mode=mode, tier=tier,
                      reason=("Modo Local: apenas o Ollama é usado." if ollama_ready
                              else ollama["detail"]))

    if mode == ProviderMode.CLOUD:
        # CLOUD honours the user's preference exactly. It does NOT quietly try
        # the other cloud provider: "use this provider" is an instruction, and
        # substituting a different vendor is the same class of surprise as
        # falling back to local without saying so.
        chosen_id, chosen = clouds[0] if clouds else (ProviderId.GROQ.value, groq)
        ready = _is_ready(chosen)
        return _route(chosen_id, cloud_model_for(chosen, tier),
                      usable=ready, fallback=False, mode=mode, tier=tier,
                      reason=(f"Modo Cloud: apenas o {provider_name(chosen_id)} é usado."
                              if ready else str(chosen.get("detail") or "")))

    # AUTO
    ready_clouds = [(pid, payload) for pid, payload in clouds if _is_ready(payload)]
    if ready_clouds:
        (chosen_id, _chosen), *rest = ready_clouds
        return _route(chosen_id, cloud_model_for(_chosen, tier),
                      usable=True, fallback=False, mode=mode, tier=tier,
                      reason=f"{provider_name(chosen_id)} disponível (sem custo de RAM local).",
                      alternatives=[pid for pid, _ in rest])
    if ollama_ready:
        details = " ".join(str(p.get("detail") or "") for _, p in clouds).strip()
        return _route("ollama", ollama["model"], usable=True,
                      # 'tier' belongs on EVERY branch. It was set on CLOUD and
                      # LOCAL but dropped on both AUTO branches, so
                      # Brain.last_metadata["tier"] read None exactly when Nano
                      # had fallen back -- the case the diagnostics panel most
                      # needs to explain.
                      fallback=True, mode=mode, tier=tier,
                      reason=f"Cloud indisponível — a usar o Ollama local. {details}")

    cloud_detail = " ".join(f"{provider_name(pid)}: {p.get('detail')}" for pid, p in clouds)
    return _route("none", "", usable=False, fallback=False, mode=mode, tier=tier,
                  reason=f"Nenhum provedor disponível. {cloud_detail} Ollama: {ollama['detail']}")


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


def rate_limit_message(info: dict[str, Any], provider: Any = None) -> str:
    """Portuguese explanation of a 429. Never just 'Error'.

    The provider is named when it is known -- from the argument, or from a
    ``provider`` key the caller put in ``info``. With three cloud providers a
    sentence that always said "Groq" would send a user whose Gemini or
    Mistral quota ran out to fix the wrong credential. Groq stays the wording
    when nothing says otherwise, because the one caller that supplies no
    provider is the Groq transport itself.
    """
    name = provider_name(provider or info.get("provider") or ProviderId.GROQ.value)
    wait = info.get("wait_seconds")
    if wait and wait > 0:
        rounded = int(wait) if wait >= 1 else 1
        return (f"Limite temporário do {name} atingido. "
                f"Disponível novamente em ~{rounded} s.")
    return f"Limite temporário do {name} atingido. Tenta novamente dentro de momentos."



# --------------------------------------------------------------------------
# Google (thin re-exports)
# --------------------------------------------------------------------------
#
# The Gemini transport lives in core.google_provider, which owns the wire
# format. These names exist so every caller keeps importing ONE module for
# "which providers are there and what state are they in" -- the same reason
# describe_ollama is a two-line wrapper over ollama_service.

GOOGLE_SECRET_NAME = google_provider.GOOGLE_SECRET_NAME
describe_google = google_provider.describe_google
google_api_key = google_provider.google_api_key
list_google_models = google_provider.list_google_models
test_google = google_provider.test_google


# --------------------------------------------------------------------------
# Mistral (thin re-exports)
# --------------------------------------------------------------------------
#
# Same arrangement as Google and for the same reason: core.mistral_provider
# owns the wire format, and every caller keeps importing ONE module for "which
# providers are there and what state are they in".

MISTRAL_SECRET_NAME = mistral_provider.MISTRAL_SECRET_NAME
describe_mistral = mistral_provider.describe_mistral
mistral_api_key = mistral_provider.mistral_api_key
list_mistral_models = mistral_provider.list_mistral_models
test_mistral = mistral_provider.test_mistral


#: The name of the describe_*/test_* function for each cloud provider, keyed
#: by the id everything else is keyed by. This is what lets
#: ``provider_status.describe_all`` probe N providers concurrently without
#: naming any of them, so the next provider is one entry here plus one line in
#: CLOUD_PROVIDER_IDS.
#:
#: NAMES AND NOT THE FUNCTIONS THEMSELVES, WHICH IS THE WHOLE POINT.
#:
#: A dict of function objects is bound once, at import. Every caller would then
#: hold a reference that a later reassignment of ``providers.describe_groq``
#: cannot reach -- so a test that substitutes a probe would silently keep
#: exercising the real one, and a probe that must not run over the network
#: would run anyway. Resolving the attribute at CALL time keeps one authority
#: for "how is this provider described", and makes substituting it work the way
#: substituting any module attribute does.
_CLOUD_DESCRIBER_NAMES: dict[str, str] = {
    ProviderId.GOOGLE.value: "describe_google",
    ProviderId.GROQ.value: "describe_groq",
    ProviderId.MISTRAL.value: "describe_mistral",
}

_CLOUD_TESTER_NAMES: dict[str, str] = {
    ProviderId.GOOGLE.value: "test_google",
    ProviderId.GROQ.value: "test_groq",
    ProviderId.MISTRAL.value: "test_mistral",
}


#: The secret each cloud provider stores its key under. One entry per provider
#: and never a shared name: removing one key must not disable another provider.
CLOUD_SECRET_NAMES: dict[str, str] = {
    ProviderId.GOOGLE.value: GOOGLE_SECRET_NAME,
    ProviderId.GROQ.value: GROQ_SECRET_NAME,
    ProviderId.MISTRAL.value: MISTRAL_SECRET_NAME,
}


def _resolve(name: str) -> Any:
    import sys

    return getattr(sys.modules[__name__], name)


def describe_cloud(provider_id: str, fast_model: str = "",
                   complex_model: str = "") -> dict[str, Any]:
    """Status for ONE cloud provider, by id. Never returns a key.

    The single entry point every status surface goes through, so a provider is
    described the same way whoever asked.
    """
    name = _CLOUD_DESCRIBER_NAMES.get(str(provider_id or "").strip().lower())
    if name is None:
        raise KeyError(f"unknown cloud provider: {provider_id!r}")
    return _resolve(name)(fast_model, complex_model)


def test_cloud(provider_id: str, api_key: str | None = None) -> dict[str, Any]:
    """Validate a candidate key for ONE cloud provider, without storing it."""
    name = _CLOUD_TESTER_NAMES.get(str(provider_id or "").strip().lower())
    if name is None:
        raise KeyError(f"unknown cloud provider: {provider_id!r}")
    return _resolve(name)(api_key)


def cloud_model_choices(provider_id: str) -> tuple[list[str], str | None]:
    """The model ids one cloud provider really exposes. Returns (ids, error).

    A model choice is validated against the ACCOUNT, never accepted on trust:
    a model that does not exist 404s on every single message, which is the
    failure that made discovery mandatory for Groq in the first place.
    """
    pid = str(provider_id or "").strip().lower()
    if pid == ProviderId.GROQ.value:
        return list_groq_models()
    if pid == ProviderId.GOOGLE.value:
        records, error = list_google_models()
        return (google_provider.model_ids(records) if not error else []), error
    if pid == ProviderId.MISTRAL.value:
        records, error = list_mistral_models()
        return (mistral_provider.model_ids(records) if not error else []), error
    return [], "unknown_provider"


__all__ = [
    "CLOUD_PROVIDER_IDS",
    "DEFAULT_CLOUD_PROVIDER",
    "DEFAULT_COMPLEX_MODEL",
    "DEFAULT_FAST_MODEL",
    "CLOUD_SECRET_NAMES",
    "GOOGLE_SECRET_NAME",
    "GROQ_SECRET_NAME",
    "MISTRAL_SECRET_NAME",
    "PROVIDER_NAMES",
    "ProviderId",
    "ProviderMode",
    "ProviderState",
    "cloud_candidates",
    "cloud_model_choices",
    "cloud_model_for",
    "describe_google",
    "describe_cloud",
    "describe_groq",
    "describe_mistral",
    "describe_ollama",
    "google_api_key",
    "groq_api_key",
    "groq_model_for",
    "list_google_models",
    "list_groq_models",
    "list_mistral_models",
    "mistral_api_key",
    "parse_preferred_cloud",
    "parse_rate_limit",
    "provider_name",
    "rank_groq_model",
    "rate_limit_message",
    "resolve_route",
    "test_cloud",
    "test_google",
    "test_groq",
    "test_mistral",
]
