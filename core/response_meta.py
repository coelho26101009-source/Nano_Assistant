"""What ONE assistant message may say about how it was produced.

WHY A MODULE, AND NOT A DICTIONARY LITERAL IN main.py
-----------------------------------------------------
``Brain.last_metadata`` is a working scratchpad. It accumulates whatever the
turn needed -- the classified failure, the raw provider sentence, cooldown
seconds, the composer's token accounting -- and it is overwritten by the next
turn. Two different consumers then read it:

    the LIVE panel   "Detalhes técnicos" under the message being streamed
    the STORED row   the same panel, after the thread is closed and reopened

Both need the same answer, and neither may receive the scratchpad as-is. The
scratchpad carries ``provider_failure.message``, which is the provider's own
error string: "UNAVAILABLE This model is currently experiencing high demand".
That is a raw backend exception. It is fine in a log and wrong on screen, and
once it is written into a message row it is on disk forever.

So this module is the one place that decides what a message is allowed to
remember about itself. Everything that reaches the renderer or the database
goes through :func:`for_message`, and nothing else is copied.

THE TOP SELECTOR IS NOT THE ANSWER TO THIS QUESTION
---------------------------------------------------
The pill in the top bar reads ``providers.route``, which is a live answer to
"who would answer if you asked right now". A message's metadata answers a
different and permanent question: "who DID answer this one". They disagree
legitimately and often -- the user switches model between turns, or a turn
fell over to the second provider -- and the moment the panel starts rendering
the live route, history is being rewritten. Nothing here may consult the
current route.

REASON CATEGORIES ARE MACHINE-READABLE, THE SENTENCE IS THE UI'S JOB
--------------------------------------------------------------------
``fallback_reason`` used to be whatever string the failing branch happened to
set: a ``FailureType`` value, ``"no_cloud_available"``, ``"google_cooldown"``,
``"not_eligible:AUTH_ERROR"``. Every consumer had to parse that shape, and a
new branch silently produced a category nobody rendered. :func:`reason_category`
collapses all of them onto the fixed vocabulary in :data:`REASON_CATEGORIES`,
so the UI maps a known set of tokens to sentences instead of guessing.
"""
from __future__ import annotations

from typing import Any

#: Every fallback/failure reason the UI may be asked to explain.
#:
#: ``routing_bug`` has no producer in Nano and that is the point of listing it:
#: it is what a reason that cannot be classified becomes when it names a
#: provider Nano never routes to, and a value appearing there is a bug report,
#: not a provider problem.
REASON_CATEGORIES: tuple[str, ...] = (
    "rate_limit",          # 429 / quota exhausted for now
    "timeout",             # the provider did not answer in time
    "unavailable",         # could not be reached at all (DNS, connection, offline)
    "provider_error",      # reached it, it failed (5xx, "high demand")
    "auth",                # the key was refused
    "bad_request",         # Nano sent something the provider rejected
    "model_unavailable",   # the chosen model does not exist on this account
    "cooldown",            # skipped: a recent failure is still cooling down
    "setup_required",      # no key, or no model chosen yet
    "cancelled",           # the user or the runtime stopped the turn
    "no_cloud_available",  # nothing cloud-side was usable at all
    "cloud_mode",          # CLOUD mode: fallback is forbidden by the user
    "partial_answer",      # text was already on screen; it was not replaced
    "routing_bug",         # unclassifiable: see above
    "other",
)

#: ``FailureType`` value -> category. Keyed by the string so this module does
#: not import ``provider_failures`` (which imports ``providers``, which imports
#: the transports): metadata shaping must stay cheap and dependency-free.
_FAILURE_CATEGORY: dict[str, str] = {
    "RATE_LIMIT": "rate_limit",
    "TIMEOUT": "timeout",
    "CONNECTION_ERROR": "unavailable",
    "SERVER_ERROR": "provider_error",
    "AUTH_ERROR": "auth",
    "BAD_REQUEST": "bad_request",
    "MODEL_UNAVAILABLE": "model_unavailable",
    "CANCELLED": "cancelled",
    "UNKNOWN_PROVIDER_ERROR": "other",
}

#: Reasons produced by routing rather than by a provider failure.
_ROUTING_CATEGORY: dict[str, str] = {
    "no_cloud_available": "no_cloud_available",
    "cloud_mode_no_fallback": "cloud_mode",
    "partial_stream_not_replaced": "partial_answer",
    "setup_required": "setup_required",
    "not_configured": "setup_required",
    "no_model": "setup_required",
    "cooldown": "cooldown",
}


def reason_category(value: Any) -> str:
    """One of :data:`REASON_CATEGORIES` for any reason string Nano produces.

    Accepts a ``FailureType`` value, one of the routing strings above, the
    ``"<provider>_cooldown"`` shape the chain emits, and the
    ``"not_eligible:<FailureType>"`` prefix ``_handle_provider_failure`` uses.
    An empty or unrecognised value is ``"other"`` -- never a guess, and never
    the raw string passed through, which is how a provider sentence would leak
    into a field the UI renders.
    """
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw in REASON_CATEGORIES:
        return raw

    # "not_eligible:AUTH_ERROR" -> the failure that was not eligible.
    if ":" in raw:
        head, _, tail = raw.partition(":")
        if head == "not_eligible":
            return _FAILURE_CATEGORY.get(tail.strip().upper(), "other")

    upper = raw.upper()
    if upper in _FAILURE_CATEGORY:
        return _FAILURE_CATEGORY[upper]
    if raw in _ROUTING_CATEGORY:
        return _ROUTING_CATEGORY[raw]
    # "google_cooldown", "groq_cooldown"
    if raw.endswith("_cooldown"):
        return "cooldown"
    return "other"


#: Keys copied verbatim from ``Brain.last_metadata`` onto a message.
#:
#: An ALLOW-LIST, not a deny-list, and the difference is the whole point: a new
#: diagnostic key added to the scratchpad tomorrow is invisible here until
#: somebody decides it is safe to persist. The reverse arrangement leaks by
#: default.
_SCALAR_KEYS: tuple[str, ...] = (
    "provider", "model", "mode", "tier", "task",
    "attempted_provider", "attempted_model", "local_model",
    "preferred_cloud",
    "tools_offered", "tools_available",
    "prompt_tokens", "completion_tokens",
    "time_to_first_token_ms", "total_latency_ms",
)

#: How many failover hops a message records. A turn crosses at most
#: google -> groq -> ollama, so three is the real ceiling; the bound exists so
#: a future routing loop cannot write an unbounded list into every row.
MAX_ATTEMPTS = 4


def _attempt(entry: Any) -> dict | None:
    """One provider hop, reduced to (provider, model, outcome). Never a message."""
    if not isinstance(entry, dict):
        return None
    provider = str(entry.get("provider") or "").strip()
    if not provider:
        return None
    shaped: dict[str, Any] = {"provider": provider}
    model = str(entry.get("model") or "").strip()
    if model:
        shaped["model"] = model
    outcome = str(entry.get("outcome") or "").strip()
    if outcome:
        # "ok" is a real outcome, not a reason, and must survive intact.
        shaped["outcome"] = outcome if outcome == "ok" else reason_category(outcome)
    return shaped


def for_message(meta: Any) -> dict[str, Any]:
    """The safe, bounded metadata one assistant message carries.

    Used for BOTH the live payload and the stored row, deliberately: a panel
    that shows different fields before and after a reload is a panel the user
    stops trusting. Returns ``{}`` for anything that is not a dict.

    Never contains: a prompt, a tool argument, recalled memory text, an API
    key, a masked key hint, or a provider's own error string.
    """
    if not isinstance(meta, dict):
        return {}

    out: dict[str, Any] = {}
    for key in _SCALAR_KEYS:
        value = meta.get(key)
        if value is None or value == "":
            continue
        if isinstance(value, (int, float, bool)):
            out[key] = value
        else:
            out[key] = str(value)[:120]

    out["fallback_used"] = bool(meta.get("fallback_used"))

    category = reason_category(meta.get("fallback_reason"))
    if not category:
        # A failure that ended the turn sets `error` rather than a fallback
        # reason. It is the same question -- "why did this not go as planned?"
        # -- so it resolves to the same vocabulary.
        category = reason_category(meta.get("error"))
    if category:
        out["fallback_reason"] = category

    attempts = meta.get("provider_attempts")
    if isinstance(attempts, (list, tuple)):
        shaped = [entry for entry in (_attempt(item) for item in attempts[:MAX_ATTEMPTS])
                  if entry is not None]
        if shaped:
            out["provider_attempts"] = shaped
            first = shaped[0]["provider"]
            if out["fallback_used"] and first != out.get("provider"):
                out["fallback_from"] = first

    # Waiting time is a number the user acts on ("try again in 44 s"), so it
    # survives; the rate-limit payload it came from does not.
    for key, value in meta.get("rate_limited", {}).items() if isinstance(
            meta.get("rate_limited"), dict) else ():
        if key == "wait_seconds" and isinstance(value, (int, float)):
            out["retry_in_seconds"] = round(float(value), 1)

    for key, value in meta.items():
        if key.endswith("_cooldown_seconds") and isinstance(value, (int, float)):
            out.setdefault("retry_in_seconds", round(float(value), 1))

    memory = meta.get("memory")
    if isinstance(memory, dict):
        # Counts and token spend only. The composer already strips recalled
        # text before it gets here; this keeps that true if it ever stops being.
        shaped_memory = {
            key: value for key, value in memory.items()
            if isinstance(value, (int, float, bool))
        }
        if shaped_memory:
            out["memory"] = shaped_memory

    return out


__all__ = [
    "MAX_ATTEMPTS",
    "REASON_CATEGORIES",
    "for_message",
    "reason_category",
]
