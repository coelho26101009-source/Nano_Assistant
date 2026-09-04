"""Typed provider failures, and the Groq cooldown that follows from them.

WHY THIS EXISTS

Before this module, `Brain.chat` decided what a provider failure meant by
looking at the exception type at the call site. That produced two concrete
bugs. A 429 was a dead end -- it fell back to nothing, in any mode, and the
user's turn was thrown away. And every other exception fell back
indiscriminately, including the two that must never be papered over: a rejected
API key and a malformed request of our own making, both of which Ollama would
"fix" by hiding.

So failures are classified once, here, and the classification decides three
separate questions:

    may we fall back?      -> FALLBACK_ELIGIBLE
    should we stop asking? -> cooldown_seconds()
    what do we tell the user? -> a plain sentence, never a payload

THE COOLDOWN IS THE OTHER HALF OF THE FIX

Groq's own numbers say a 429 is not one moment but a window: the observed
response carried `retry_after = 3s` alongside `reset_tokens = 47s`. Retrying at
3 s spends the remaining tokens on a request that cannot succeed, and the next
failure arrives immediately. The cooldown therefore takes the LONGER of the two
signals, and while it is active AUTO skips Groq entirely and goes straight to
the local model instead of paying for a doomed round trip.

It is process-local, monotonic, and passive: no thread, no timer, no
background loop. Eligibility is a comparison against the clock, evaluated when
somebody asks.

ONE BREAKER PER PROVIDER
------------------------
With more than one cloud provider the breaker had to stop being a Groq
singleton. A rate-limited Gemini must not stop Nano asking Groq -- routing to
the next provider is precisely what a second provider is for -- so cooldowns
live in a registry keyed by provider id (``cooldown_for``). The classifier is
already provider-parameterised and now produces the right provider's name in
the sentence the user reads, which matters: telling somebody their Groq key was
refused when Gemini's was is a bug report waiting to happen.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("nano.provider_failures")


class FailureType(str, Enum):
    RATE_LIMIT = "RATE_LIMIT"
    TIMEOUT = "TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    SERVER_ERROR = "SERVER_ERROR"
    AUTH_ERROR = "AUTH_ERROR"
    BAD_REQUEST = "BAD_REQUEST"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    UNKNOWN_PROVIDER_ERROR = "UNKNOWN_PROVIDER_ERROR"


#: Failures where trying the other provider is the right answer: the request
#: itself was fine and the provider was temporarily unable to serve it.
#:
#: UNKNOWN_PROVIDER_ERROR is included deliberately. It is, by definition, not
#: one of the failures we know to be our own fault, and refusing to fall back
#: on it would be a regression -- AUTO already recovered from arbitrary
#: exceptions before this module existed. CLOUD mode still never falls back, so
#: a user who asked for cloud-only keeps getting the honest error.
FALLBACK_ELIGIBLE: frozenset[FailureType] = frozenset({
    FailureType.RATE_LIMIT,
    FailureType.TIMEOUT,
    FailureType.CONNECTION_ERROR,
    FailureType.SERVER_ERROR,
    FailureType.MODEL_UNAVAILABLE,
    FailureType.UNKNOWN_PROVIDER_ERROR,
})

#: Failures that must reach the user unchanged.
#:
#: AUTH_ERROR   the key is wrong or revoked. Silently answering from Ollama
#:              would hide a configuration problem the user must fix, possibly
#:              for weeks.
#: BAD_REQUEST  we sent something invalid. Ollama would receive the same
#:              invalid intent; falling back converts our bug into a mystery.
#: CANCELLED    the user or the runtime stopped this turn. Starting a second
#:              provider is the opposite of what was asked.
NEVER_FALLBACK: frozenset[FailureType] = frozenset({
    FailureType.AUTH_ERROR,
    FailureType.BAD_REQUEST,
    FailureType.CANCELLED,
})

#: Bounds on any computed cooldown. The floor stops a 0-second retry-after from
#: disabling the breaker; the ceiling stops a bad header from parking Groq for
#: the rest of the session.
MIN_COOLDOWN_SECONDS = 2.0
MAX_COOLDOWN_SECONDS = 120.0

#: Cooldowns for failures that carry no timing information of their own.
_DEFAULT_COOLDOWNS: dict[FailureType, float] = {
    FailureType.TIMEOUT: 15.0,
    FailureType.CONNECTION_ERROR: 15.0,
    FailureType.SERVER_ERROR: 20.0,
    FailureType.MODEL_UNAVAILABLE: 60.0,
    FailureType.UNKNOWN_PROVIDER_ERROR: 10.0,
    # An auth failure is not transient and a cooldown would only delay the
    # honest error. The provider-status cache already reports a bad key.
    FailureType.AUTH_ERROR: 0.0,
    FailureType.BAD_REQUEST: 0.0,
    FailureType.CANCELLED: 0.0,
}


@dataclass
class ProviderFailure:
    """One classified provider failure. Carries no secret and no payload."""

    type: FailureType
    provider: str = "groq"
    status_code: int | None = None
    message: str = ""
    rate_limit: dict[str, Any] = field(default_factory=dict)

    @property
    def may_fall_back(self) -> bool:
        return self.type in FALLBACK_ELIGIBLE

    def cooldown_seconds(self) -> float:
        """How long to stop asking this provider. 0 means "keep asking"."""
        if self.type is not FailureType.RATE_LIMIT:
            return _DEFAULT_COOLDOWNS.get(self.type, 0.0)

        # THE CONSERVATIVE CHOICE, AND THE REASON THIS FUNCTION EXISTS.
        #
        # Groq answered a real 429 with retry_after=3 and reset_tokens=47.
        # retry_after says "you may send another request"; reset_tokens says
        # "your token budget is back". Coming back at 3 s with a full
        # conversation plus tool schemas simply fails again and spends the
        # little budget that remains. The longer signal is the honest one.
        candidates = [
            value for value in (
                self.rate_limit.get("retry_after_seconds"),
                self.rate_limit.get("reset_tokens_seconds"),
                self.rate_limit.get("wait_seconds"),
            )
            if isinstance(value, (int, float)) and value > 0
        ]
        if not candidates:
            return 30.0
        return float(max(candidates))

    @property
    def provider_label(self) -> str:
        """The provider's human name, for a sentence shown to the user.

        Resolved through core.providers so a new provider gets a correct
        sentence the day it is added, instead of a message that still says
        "Groq" because the taxonomy predates it. That was a real hazard: with
        two cloud providers, "a chave da Groq foi recusada" for a Gemini auth
        failure would send the user to fix the wrong credential.
        """
        from core import providers

        return providers.provider_name(self.provider)

    def user_message(self) -> str:
        """One clean Portuguese sentence. Never JSON, never a stack trace."""
        name = self.provider_label
        return {
            FailureType.RATE_LIMIT: f"O limite temporário do {name} foi atingido.",
            FailureType.TIMEOUT: f"O {name} demorou demasiado a responder.",
            FailureType.CONNECTION_ERROR: f"Não foi possível contactar o {name}.",
            FailureType.SERVER_ERROR: f"O {name} está com problemas de serviço.",
            FailureType.AUTH_ERROR: (f"A chave de API do {name} foi recusada. "
                                     "Verifica-a em Definições → Inteligência Artificial."),
            FailureType.BAD_REQUEST: f"O pedido enviado ao {name} foi recusado.",
            FailureType.MODEL_UNAVAILABLE: f"O modelo pedido não está disponível no {name}.",
            FailureType.CANCELLED: "O pedido foi cancelado.",
            FailureType.UNKNOWN_PROVIDER_ERROR: f"O {name} não respondeu.",
        }.get(self.type, f"O {name} não respondeu.")

    def as_dict(self) -> dict[str, Any]:
        """Structured diagnostics. This is where rate-limit numbers belong.

        Never concatenated into an assistant answer -- that is what produced
        `_ratelimit_:{...}` in the chat bubble and, on a voice turn, in speech.
        """
        payload: dict[str, Any] = {
            "provider": self.provider,
            "failure_type": self.type.value,
            "status_code": self.status_code,
            "message": self.message[:300],
        }
        for key in ("retry_after_seconds", "reset_tokens_seconds", "wait_seconds",
                    "remaining_tokens", "limit_tokens", "remaining_requests"):
            if self.rate_limit.get(key) is not None:
                payload[key] = self.rate_limit[key]
        return payload


def _status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) if response is not None else None
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _headers(exc: BaseException) -> Any:
    response = getattr(exc, "response", None)
    return getattr(response, "headers", {}) if response is not None else {}


def classify(exc: BaseException, *, provider: str = "groq") -> ProviderFailure:
    """Map any exception from a provider call onto a typed failure.

    Ordered from most specific to least: an HTTP status is authoritative when
    present, then exception identity, then the message text as a last resort.
    """
    import asyncio

    from core import providers

    name = type(exc).__name__
    message = str(exc)

    if isinstance(exc, asyncio.CancelledError):
        return ProviderFailure(FailureType.CANCELLED, provider, None, message)

    status = _status_code(exc)
    if status == 429:
        return ProviderFailure(
            FailureType.RATE_LIMIT, provider, status, message,
            providers.parse_rate_limit(_headers(exc)),
        )
    if status in (401, 403):
        return ProviderFailure(FailureType.AUTH_ERROR, provider, status, message)
    if status == 404:
        return ProviderFailure(FailureType.MODEL_UNAVAILABLE, provider, status, message)
    if status == 408:
        return ProviderFailure(FailureType.TIMEOUT, provider, status, message)
    if status is not None and 400 <= status < 500:
        return ProviderFailure(FailureType.BAD_REQUEST, provider, status, message)
    if status is not None and status >= 500:
        return ProviderFailure(FailureType.SERVER_ERROR, provider, status, message)

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return ProviderFailure(FailureType.TIMEOUT, provider, None, message)

    lowered = f"{name} {message}".lower()
    if "timeout" in lowered or "timed out" in lowered:
        return ProviderFailure(FailureType.TIMEOUT, provider, None, message)
    if any(marker in lowered for marker in
           ("connection", "connect", "network", "unreachable", "dns", "ssl")):
        return ProviderFailure(FailureType.CONNECTION_ERROR, provider, None, message)
    if "ratelimit" in lowered.replace(" ", "").replace("_", ""):
        return ProviderFailure(FailureType.RATE_LIMIT, provider, None, message,
                               getattr(exc, "info", {}) or {})
    if "authentication" in lowered or "api key" in lowered or "unauthorized" in lowered:
        return ProviderFailure(FailureType.AUTH_ERROR, provider, None, message)

    return ProviderFailure(FailureType.UNKNOWN_PROVIDER_ERROR, provider, None, message)


class ProviderCooldown:
    """A passive circuit breaker for one provider. No thread, no timer.

    Eligibility is a comparison against ``time.monotonic()`` performed when a
    caller asks, so an expired cooldown costs nothing to clear and there is no
    background work while Nano idles. The monotonic clock is deliberate: a
    wall-clock cooldown would break when the system clock moves.
    """

    def __init__(self, provider: str = "groq"):
        self.provider = provider
        self._until: float | None = None
        self._reason: ProviderFailure | None = None
        self._consecutive_failures = 0

    # ------------------------------------------------------------- state

    def remaining_seconds(self) -> float:
        if self._until is None:
            return 0.0
        remaining = self._until - time.monotonic()
        if remaining <= 0.0:
            # Expired: clear it here so state never lingers, and so the very
            # next request is a normal one.
            self._until = None
            self._reason = None
            return 0.0
        return remaining

    def is_cooling_down(self) -> bool:
        return self.remaining_seconds() > 0.0

    @property
    def last_failure(self) -> ProviderFailure | None:
        return self._reason if self.is_cooling_down() else None

    # ------------------------------------------------------------ updates

    def note_failure(self, failure: ProviderFailure) -> float:
        """Record a failure and return the cooldown actually applied."""
        seconds = failure.cooldown_seconds()
        if seconds <= 0.0:
            return 0.0
        self._consecutive_failures += 1
        seconds = max(MIN_COOLDOWN_SECONDS, min(MAX_COOLDOWN_SECONDS, seconds))
        self._until = time.monotonic() + seconds
        self._reason = failure
        logger.info(
            "%s cooldown %.0fs after %s (consecutive=%d)",
            self.provider, seconds, failure.type.value, self._consecutive_failures,
        )
        return seconds

    def note_success(self) -> None:
        """A successful call clears the breaker completely."""
        if self._until is not None or self._consecutive_failures:
            logger.info("%s recovered; cooldown cleared", self.provider)
        self._until = None
        self._reason = None
        self._consecutive_failures = 0

    def reset(self) -> None:
        """Clear without logging a recovery. For mode changes and tests."""
        self._until = None
        self._reason = None
        self._consecutive_failures = 0

    def status(self) -> dict[str, Any]:
        """In-memory only. Never triggers a provider call -- Settings polls
        this once a second and must never turn that into API traffic."""
        remaining = self.remaining_seconds()
        payload: dict[str, Any] = {
            "provider": self.provider,
            "temporarily_limited": remaining > 0.0,
            "retry_in_seconds": round(remaining, 1) if remaining > 0.0 else None,
            "consecutive_failures": self._consecutive_failures,
        }
        if self._reason is not None and remaining > 0.0:
            payload["failure_type"] = self._reason.type.value
        return payload


#: One breaker PER PROVIDER, process-wide, shared by the Brain and the
#: settings/status surface so both see the same state without extra probing.
#:
#: Per provider is the whole point of the registry. A single shared breaker
#: would mean a rate-limited Gemini also stopped Nano asking Groq -- which is
#: the exact opposite of what a second cloud provider is for. Each provider
#: exhausts, cools down and recovers on its own clock.
_COOLDOWNS: dict[str, ProviderCooldown] = {}
_COOLDOWN_LOCK = __import__("threading").RLock()


def cooldown_for(provider: str) -> ProviderCooldown:
    """The breaker for one provider, created on first use."""
    key = str(provider or "").strip().lower() or "unknown"
    with _COOLDOWN_LOCK:
        breaker = _COOLDOWNS.get(key)
        if breaker is None:
            breaker = ProviderCooldown(key)
            _COOLDOWNS[key] = breaker
        return breaker


def all_cooldowns() -> dict[str, ProviderCooldown]:
    """Every breaker created so far. For status surfaces only."""
    with _COOLDOWN_LOCK:
        return dict(_COOLDOWNS)


def reset_all_cooldowns() -> None:
    """Clear every breaker. For an explicit mode/provider change, and tests."""
    for breaker in all_cooldowns().values():
        breaker.reset()


#: Kept as module-level names because the Brain, main.py and the existing
#: regression suite all reference GROQ_COOLDOWN directly. They are the registry
#: entries, not copies, so `cooldown_for("groq") is GROQ_COOLDOWN`.
GROQ_COOLDOWN = cooldown_for("groq")
GOOGLE_COOLDOWN = cooldown_for("google")


__all__ = [
    "FALLBACK_ELIGIBLE",
    "GOOGLE_COOLDOWN",
    "GROQ_COOLDOWN",
    "MAX_COOLDOWN_SECONDS",
    "MIN_COOLDOWN_SECONDS",
    "NEVER_FALLBACK",
    "FailureType",
    "ProviderCooldown",
    "ProviderFailure",
    "all_cooldowns",
    "classify",
    "cooldown_for",
    "reset_all_cooldowns",
]
