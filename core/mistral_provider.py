"""Mistral as a first-class Nano cloud provider.

WHY DIRECT HTTP AND NOT THE SDK
-------------------------------
``pip install mistralai`` was rejected for the same reason ``google-genai`` was
(see core.google_provider): Mistral's chat surface is, on the wire, a POST with
an OpenAI-shaped JSON body and one ``Authorization`` header, and the repository
already depends on ``httpx`` and already speaks raw HTTP to Groq's /models
endpoint, to Gemini and to Ollama. The SDK would pull pydantic and its
transitive tree into an MSI that has to stay small, and it would raise its own
exception hierarchy that ``provider_failures.classify`` does not understand.

WHY NOT POINT THE GROQ SDK AT api.mistral.ai
--------------------------------------------
This was the obvious "generalise the existing cloud transport" move and it is
the wrong one. ``AsyncGroq`` is a vendor client: its base URL, its retry policy,
its ``x_groq`` usage extension and its error classes are Groq's, and pointing it
at another vendor's host is an unsupported configuration that would break on any
SDK release. What IS shared is everything above the wire -- the failure
taxonomy, the cooldown registry, the execution ledger, the collector shape, the
routing and the diagnostics -- and none of it needed changing to accept a third
provider. The transport is the only part that is per-vendor, which is exactly
the seam this module sits on.

TOOL CALL IDS ARE NOT INTERCHANGEABLE, AND THAT MATTERS FOR FAILOVER
--------------------------------------------------------------------
Mistral requires a tool call id to be exactly nine alphanumeric characters. The
rest of Nano stores OpenAI-shaped ids (``call_0`` from the Gemini adapter,
``call_<random>`` from Groq), so a turn that starts on another provider, runs a
tool and then fails over to Mistral would send back a history Mistral answers
422 to -- and BAD_REQUEST is the one failure class that never falls back. So
:func:`to_messages` rewrites the ids deterministically, re-pointing each
``tool`` message at the call it answers. Nothing else in Nano learns about it,
and the stored history keeps its own ids.

WHAT THIS MODULE IS NOT
-----------------------
It is not an execution path. Mistral produces *tool intentions*; every one of
them still travels MODEL -> REQUEST -> POLICY -> PERMISSION -> ToolExecutor ->
NARROW TOOL. Nothing here calls a tool, and nothing here can.

THE KEY
-------
The key travels in the ``Authorization`` header and nowhere else. Nothing in
this module logs it, formats it into a message, or returns it to a caller; the
only thing that ever leaves is ``secret_store.mask()`` output and booleans.
Exception text is reduced to the exception TYPE wherever httpx is involved,
because httpx puts the full request URL into its messages.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from typing import Any, AsyncIterator, Iterable

import httpx

from core import secret_store

logger = logging.getLogger("nano.mistral")

MISTRAL_API_BASE = "https://api.mistral.ai/v1"

#: The credential identifier in the encrypted store. Deliberately distinct from
#: ``groq_api_key`` and ``google_api_key``: one provider's key must never
#: overwrite or satisfy another's, and removing one must not disable the others.
MISTRAL_SECRET_NAME = "mistral_api_key"

#: Families Nano prefers, best first. RANKING HINTS FOR THE PICKER, NOT A CLAIM
#: THAT ANY OF THEM EXISTS -- concrete ids are discovered from the account, the
#: same rule that applies to Groq and Google. Nothing here is a default: an
#: unconfigured install simply does not route to Mistral.
_PREFERRED_FAMILIES = (
    "ministral", "mistral-small", "mistral-medium", "open-mistral", "mistral-large",
)

#: Ids that exist on an account but cannot serve an ordinary Nano chat turn.
#: Used only when the account's own capability metadata is missing; the
#: metadata is authoritative when present (see :func:`_model_record`).
_NON_CHAT_MARKERS = (
    "embed", "moderation", "ocr", "voxtral", "whisper", "transcri", "tts",
    "audio", "rerank", "classifier", "guardrail",
)

#: Tool ids Mistral accepts: exactly nine alphanumeric characters. Anything else
#: is a 422 on the whole request, not a warning on one call.
_TOOL_ID_LENGTH = 9
_TOOL_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

DEFAULT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


class MistralAPIError(RuntimeError):
    """A Mistral failure shaped so ``provider_failures.classify`` understands it.

    ``status_code`` and ``response.headers`` are the two things the shared
    classifier reads, so carrying them here means Mistral failures land in the
    same taxonomy as Groq's and Gemini's with no provider-specific branch at
    the call site.

    Mistral's rate-limit headers are named for what they meter
    (``ratelimitbysize-*``) rather than for tokens, so they are normalised on
    the way past into the header names ``providers.parse_rate_limit`` reads.
    """

    def __init__(self, status_code: int, message: str = "", headers: dict | None = None):
        super().__init__(message or f"Mistral API error ({status_code})")
        self.status_code = int(status_code)
        self.response = type("_R", (), {"status_code": self.status_code,
                                        "headers": headers or {}})()


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def mistral_api_key() -> str:
    """The stored key, or the environment fallback. Never logged, never returned
    to the renderer."""
    return secret_store.get_secret(MISTRAL_SECRET_NAME)


def is_configured() -> bool:
    return bool(mistral_api_key())


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


# --------------------------------------------------------------------------
# Rate-limit headers
# --------------------------------------------------------------------------

#: Mistral header -> the name ``providers.parse_rate_limit`` already parses.
#: A mapping rather than teaching the parser a third vendor's vocabulary: the
#: parser's job is "how long must we wait", and every provider answers that
#: question in its own words.
_RATE_LIMIT_ALIASES: dict[str, str] = {
    "retry-after": "retry-after",
    "ratelimitbysize-reset": "x-ratelimit-reset-tokens",
    "ratelimitbysize-limit": "x-ratelimit-limit-tokens",
    "ratelimitbysize-remaining": "x-ratelimit-remaining-tokens",
    "x-ratelimit-reset-tokens": "x-ratelimit-reset-tokens",
    "x-ratelimit-limit-tokens": "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens": "x-ratelimit-remaining-tokens",
    "x-ratelimit-limit-requests": "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests": "x-ratelimit-remaining-requests",
}


def rate_limit_headers(headers: Any) -> dict[str, str]:
    """Mistral's throttling headers, renamed to what Nano's parser reads.

    Absent headers stay absent. A 429 that carried no timing information must
    report none rather than a plausible-looking guess -- the cooldown already
    has a conservative default for exactly that case.
    """
    out: dict[str, str] = {}
    for source, target in _RATE_LIMIT_ALIASES.items():
        try:
            value = headers.get(source)
        except Exception:
            value = None
        if value not in (None, ""):
            out.setdefault(target, str(value))
    return out


# --------------------------------------------------------------------------
# Model discovery
# --------------------------------------------------------------------------

def is_chat_model(model_id: str) -> bool:
    lowered = str(model_id or "").lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


def rank_model(model_id: str) -> int:
    """Lower is better. Unknown families sort last but stay usable."""
    lowered = str(model_id or "").lower()
    best = len(_PREFERRED_FAMILIES)
    for index, family in enumerate(_PREFERRED_FAMILIES):
        if family in lowered:
            best = min(best, index)
    return best


def _capabilities(item: dict) -> dict:
    caps = item.get("capabilities")
    return caps if isinstance(caps, dict) else {}


def _model_record(item: dict) -> dict[str, Any] | None:
    """One /v1/models entry, reduced to what Nano needs. None if unusable.

    THE ACCOUNT'S OWN CAPABILITY FLAGS WIN over any inference from the id.
    Mistral publishes ``capabilities.completion_chat`` and
    ``capabilities.function_calling`` per model, which is real evidence; the
    name-marker list is consulted only when the account said nothing, and a
    guess from a model name is what this project already got wrong twice with
    Gemma (see core.google_provider).
    """
    model_id = str(item.get("id") or "").strip()
    if not model_id:
        return None

    caps = _capabilities(item)
    chat = caps.get("completion_chat")
    if chat is False:
        return None
    if chat is None and not is_chat_model(model_id):
        return None

    tool_calling = caps.get("function_calling")
    return {
        "id": model_id,
        "display_name": str(item.get("name") or model_id),
        "description": str(item.get("description") or "")[:280],
        "input_tokens": item.get("max_context_length"),
        "output_tokens": None,
        # Streaming is a property of the chat endpoint, not of an individual
        # model: /v1/chat/completions accepts stream=true for every
        # chat-capable model and there is no per-model flag saying otherwise.
        # Reporting False here would be Nano stating a limitation it does not
        # have -- the mirror image of claiming one it lacks.
        "streaming": True,
        # None means "the account did not say". That is NOT False, and the
        # request builder treats it as permission to offer tools: withholding
        # tools from a model that has them makes it report actions it never
        # performed, which was measured on Gemma and is the worse failure.
        "tool_calling": True if tool_calling is None else bool(tool_calling),
        "vision": bool(caps.get("vision")),
        "deprecated": bool(item.get("deprecation")),
        "aliases": [str(alias) for alias in (item.get("aliases") or []) if alias][:8],
    }


def list_mistral_models(api_key: str | None = None, *, timeout: float = 10.0
                        ) -> tuple[list[dict[str, Any]], str | None]:
    """Chat-capable models on the account, best first. Returns (models, error).

    Each entry is safe metadata only: id, display name, context length and the
    capability flags the account itself published. No credential is included.
    """
    key = (api_key or mistral_api_key()).strip()
    if not key:
        return [], "no_api_key"

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.get(f"{MISTRAL_API_BASE}/models", headers=_headers(key))
    except httpx.HTTPError as exc:
        # The exception TYPE only: httpx puts the full request URL in its
        # message, and this must stay credential-free even though the key
        # travels in a header.
        return [], f"network_error: {type(exc).__name__}"

    if response.status_code in (401, 403):
        return [], "invalid_api_key"
    if response.status_code == 429:
        return [], "rate_limited"
    if not response.is_success:
        return [], f"http_{response.status_code}"

    try:
        payload = response.json()
    except (ValueError, TypeError) as exc:
        return [], f"bad_response: {type(exc).__name__}"

    records: list[dict[str, Any]] = []
    for item in (payload.get("data") or []) if isinstance(payload, dict) else []:
        if not isinstance(item, dict):
            continue
        record = _model_record(item)
        if record is not None:
            records.append(record)

    records.sort(key=lambda record: (rank_model(record["id"]), record["id"]))
    return records, None


def model_ids(records: Iterable[dict]) -> list[str]:
    return [str(record.get("id")) for record in records if record.get("id")]


def test_mistral(api_key: str | None = None) -> dict[str, Any]:
    """Validate a key without storing it. Used by the Test connection button."""
    started = time.monotonic()
    records, error = list_mistral_models(api_key)
    latency_ms = int((time.monotonic() - started) * 1000)

    if error == "no_api_key":
        return {"ok": False, "error": "no_api_key",
                "detail": "Nenhuma chave de API configurada.", "models": []}
    if error == "invalid_api_key":
        return {"ok": False, "error": "invalid_api_key",
                "detail": "A chave de API foi recusada pelo Mistral.", "models": []}
    if error == "rate_limited":
        return {"ok": False, "error": "rate_limited",
                "detail": "O Mistral respondeu com limite de pedidos. Tenta daqui a pouco.",
                "models": []}
    if error:
        return {"ok": False, "error": error,
                "detail": f"Não foi possível contactar o Mistral ({error}).", "models": []}
    if not records:
        return {"ok": False, "error": "no_chat_models",
                "detail": "A chave é válida mas a conta não expõe modelos de chat.",
                "models": []}

    ids = model_ids(records)
    return {
        "ok": True,
        "detail": f"Ligação estabelecida ({len(ids)} modelos disponíveis).",
        "models": ids,
        "records": records,
        "suggested_model": ids[0],
        "latency_ms": latency_ms,
    }


# --------------------------------------------------------------------------
# Request shaping
# --------------------------------------------------------------------------

def tool_call_id(raw: Any, position: int) -> str:
    """A Mistral-legal id for one tool call: nine alphanumeric characters.

    DETERMINISTIC, so the same call maps to the same id every time it is
    re-sent. Failover re-sends the whole history, and an id that changed
    between two requests would detach a tool RESULT from the CALL it answers --
    which is how a model comes to believe an action it already performed still
    needs doing, and the per-turn execution ledger is the only thing standing
    between that belief and a second real effect on the machine.

    ``position`` is folded into the hash so two calls in one assistant turn
    that happen to share an id still separate.
    """
    seed = f"{position}:{raw if raw not in (None, '') else 'anon'}".encode("utf-8")
    digest = hashlib.sha256(seed).digest()
    base = len(_TOOL_ID_ALPHABET)
    return "".join(_TOOL_ID_ALPHABET[byte % base] for byte in digest[:_TOOL_ID_LENGTH])


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def to_messages(messages: list[dict]) -> list[dict]:
    """Nano's OpenAI-style history, made legal for Mistral.

    Three things are adjusted and nothing else -- the wire format is otherwise
    the one Nano already speaks:

    * tool call ids are rewritten to Mistral's nine-character form, and every
      ``tool`` message is re-pointed at its rewritten call (see the module
      docstring: this is what makes cross-provider failover safe);
    * a tool call whose arguments are not valid JSON is DROPPED rather than
      sent with ``{}``, because an empty argument map invites the model to
      re-issue the call with arguments of its own invention, which the per-turn
      execution ledger would not recognise as a repeat;
    * an assistant turn left with neither text nor a surviving call is omitted,
      since a message carrying nothing is rejected.
    """
    out: list[dict] = []
    remap: dict[str, str] = {}

    for message in messages or []:
        role = str(message.get("role") or "")

        if role == "tool":
            original = str(message.get("tool_call_id") or "")
            shaped: dict[str, Any] = {
                "role": "tool",
                "content": _content_text(message.get("content")),
                # An unmapped id means the call it answers is not in this
                # history. Passing the original through unchanged would be a
                # 422; a synthesised id keeps the request legal and leaves the
                # result attached to nothing, which is what it is.
                "tool_call_id": remap.get(original) or tool_call_id(original, len(out)),
            }
            name = str(message.get("name") or "")
            if name:
                shaped["name"] = name
            out.append(shaped)
            continue

        if role == "assistant":
            calls: list[dict] = []
            for index, call in enumerate(message.get("tool_calls") or []):
                function = (call or {}).get("function") or {}
                name = str(function.get("name") or "")
                if not name:
                    continue
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        json.loads(arguments or "{}")
                    except (ValueError, TypeError):
                        logger.warning(
                            "Dropping a tool call for Mistral: arguments are not valid JSON.")
                        continue
                    encoded = arguments or "{}"
                elif isinstance(arguments, dict):
                    encoded = json.dumps(arguments, ensure_ascii=False)
                else:
                    continue
                original = str(call.get("id") or "")
                new_id = tool_call_id(original, index)
                if original:
                    remap[original] = new_id
                calls.append({"id": new_id, "type": "function",
                              "function": {"name": name, "arguments": encoded}})

            text = _content_text(message.get("content"))
            if not text and not calls:
                continue
            shaped = {"role": "assistant", "content": text}
            if calls:
                shaped["tool_calls"] = calls
            out.append(shaped)
            continue

        if role in ("system", "user"):
            text = _content_text(message.get("content"))
            if text:
                out.append({"role": role, "content": text})

    return out


def supports_tool_calling(model: str, *, metadata: dict | None = None) -> bool:
    """Whether tools may be offered to this model.

    Read from the account's capability metadata when there is any. Absent
    evidence the answer is yes: a model that cannot call a tool answers with
    words, whereas a tool-capable model that was never offered tools reports
    actions it did not perform. The second failure is the dangerous one.
    """
    if isinstance(metadata, dict) and isinstance(metadata.get("tool_calling"), bool):
        return metadata["tool_calling"]
    return True


def build_request(model: str, messages: list[dict], tools: list[dict] | None, *,
                  temperature: float = 0.65, max_tokens: int = 1536,
                  task: str | None = None, metadata: dict | None = None) -> dict[str, Any]:
    """The full JSON body for one streaming /v1/chat/completions call.

    ``task`` is accepted and unused, deliberately: it exists so every cloud
    adapter has one signature, and Mistral has no reasoning-budget knob to map
    it onto. Inventing a mapping -- temperature by task class, say -- would be
    a product decision taken inside a transport, and an unmeasured one.
    """
    body: dict[str, Any] = {
        "model": model,
        "messages": to_messages(messages),
        "temperature": temperature,
        "max_tokens": int(max_tokens),
        "stream": True,
    }
    # Only send the tool keys when there are tools. An explicit null
    # tool_choice is rejected outright, which is a 400 on every single message
    # -- the same defect the Groq path carries a comment about.
    if tools and supports_tool_calling(model, metadata=metadata):
        body["tools"] = list(tools)
        body["tool_choice"] = "auto"
    return body


# --------------------------------------------------------------------------
# Response shaping
# --------------------------------------------------------------------------

def _error_message(payload: Any, status: int) -> str:
    """A short, credential-free description of a Mistral error.

    Two shapes come back from the API: ``{"message": ...}`` for most errors and
    FastAPI's ``{"detail": [{"msg": ..., "loc": [...]}]}`` for a body it could
    not validate. Both are reduced to one line.
    """
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()[:300]
        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()[:300]
        if isinstance(detail, list):
            parts: list[str] = []
            for entry in detail:
                if isinstance(entry, dict) and entry.get("msg"):
                    location = ".".join(str(part) for part in (entry.get("loc") or []))
                    parts.append(f"{location}: {entry['msg']}" if location else str(entry["msg"]))
            if parts:
                return "; ".join(parts)[:300]
    return f"HTTP {status}"


def _raise_for_error(status: int, raw: str, headers: Any) -> None:
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, TypeError):
        payload = {}
    raise MistralAPIError(status, _error_message(payload, status),
                          rate_limit_headers(headers))


class MistralChat:
    """The streaming transport. One instance per configured key.

    Holds no conversation state: every call is given its full message list, the
    same way ``AsyncGroq`` and ``GoogleChat`` are. That is what lets the Brain
    fail over between providers mid-turn without either of them carrying a
    stale view.
    """

    def __init__(self, api_key: str, *, base_url: str = MISTRAL_API_BASE,
                 timeout: httpx.Timeout | None = None):
        self._api_key = (api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout or DEFAULT_TIMEOUT

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def stream(self, model: str, body: dict, collector: dict | None = None
                     ) -> AsyncIterator[str]:
        """Yield answer text as it arrives; collect tool calls and usage.

        ``collector`` is filled with the same keys the Groq and Google paths use
        -- ``first_token_at``, ``tool_calls`` (id/name/args) and ``usage`` -- so
        the caller handles all three providers with one branchless body.

        Cancellation is the caller's ``asyncio.CancelledError``: it propagates
        out of the ``async for``, the context manager closes the connection, and
        nothing is left half-read.
        """
        if not self._api_key:
            raise MistralAPIError(401, "no_api_key")

        sink = collector if collector is not None else {}
        # Tool calls arrive fragmented across deltas -- the name in one chunk,
        # the arguments a few characters at a time -- so they are reassembled
        # by index, exactly as the Groq path does.
        acc: dict[int, dict[str, str]] = {}
        url = f"{self.base_url}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=_headers(self._api_key),
                                         json=body) as response:
                    if response.status_code >= 400:
                        raw = (await response.aread()).decode("utf-8", "replace")
                        _raise_for_error(response.status_code, raw, response.headers)
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            payload = json.loads(chunk)
                        except (ValueError, TypeError):
                            continue
                        for text in self._absorb(payload, acc, sink):
                            yield text
        except (MistralAPIError, asyncio.CancelledError):
            raise
        except httpx.TimeoutException as exc:
            raise MistralAPIError(408, f"timeout: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            # The type only. httpx puts the request URL in its message and
            # nothing derived from the request may reach a user-facing string.
            raise MistralAPIError(503, f"transport: {type(exc).__name__}") from exc

        sink["tool_calls"] = [acc[key] for key in sorted(acc) if acc[key].get("name")]

    @staticmethod
    def _absorb(payload: dict, acc: dict[int, dict[str, str]], sink: dict) -> list[str]:
        """Pull text, tool-call fragments and usage out of one SSE frame."""
        texts: list[str] = []
        if not isinstance(payload, dict):
            return texts

        for choice in payload.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            content = delta.get("content")
            # Content may arrive as a list of typed chunks rather than a
            # string. Only text parts are answer text; anything else is not
            # something Nano may print.
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text") or "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") in (None, "text"))
            if isinstance(content, str) and content:
                if sink.get("first_token_at") is None:
                    sink["first_token_at"] = time.monotonic()
                texts.append(content)

            for position, call in enumerate(delta.get("tool_calls") or []):
                if not isinstance(call, dict):
                    continue
                index = call.get("index")
                try:
                    index = int(index)
                except (TypeError, ValueError):
                    index = position
                slot = acc.setdefault(index, {"id": "", "name": "", "args": ""})
                if call.get("id"):
                    slot["id"] = str(call["id"])
                function = call.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        slot["name"] = str(function["name"])
                    arguments = function.get("arguments")
                    if isinstance(arguments, str) and arguments:
                        slot["args"] += arguments
                    elif isinstance(arguments, dict):
                        # Some responses deliver the whole argument map at once
                        # instead of as string fragments. The rest of Nano
                        # stores arguments as a JSON STRING, because that is the
                        # OpenAI wire format the history must stay valid in.
                        slot["args"] = json.dumps(arguments, ensure_ascii=False)

            finish = choice.get("finish_reason")
            if finish:
                sink["finish_reason"] = str(finish)

        usage = payload.get("usage")
        if isinstance(usage, dict):
            sink["usage"] = {
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        return texts


# --------------------------------------------------------------------------
# Status for the UI
# --------------------------------------------------------------------------

def describe_mistral(configured_model: str = "", complex_model: str = "") -> dict[str, Any]:
    """Mistral status for the UI. Never returns or logs the key itself.

    Deliberately the same payload shape as ``providers.describe_groq`` and
    ``google_provider.describe_google``: the Settings page, the pill and the
    router all read providers through one structure, which is what keeps adding
    the next provider a matter of writing one more ``describe_*``.
    """
    from core.providers import ProviderState        # local: avoids a cycle

    fast = str(configured_model or "")
    strong = str(complex_model or fast)
    secret = secret_store.describe(MISTRAL_SECRET_NAME)
    base = {
        "id": "mistral", "name": "Mistral", "kind": "cloud", "role": "cloud",
        "model": fast, "models": [], "records": [], "secret": secret,
        "tiers": {"fast": fast, "complex": strong},
    }

    if not secret["configured"]:
        return {**base, "state": ProviderState.SETUP_REQUIRED.value,
                "detail": "Adiciona uma chave de API do Mistral nas Definições para o usar."}

    records, error = list_mistral_models()
    if error:
        state = ProviderState.ERROR if error == "invalid_api_key" else ProviderState.UNAVAILABLE
        detail = ("A chave de API do Mistral foi recusada. Verifica-a nas Definições."
                  if error == "invalid_api_key"
                  else f"O Mistral não está acessível ({error}).")
        return {**base, "state": state.value, "detail": detail}

    ids = model_ids(records)
    if not fast:
        # Nothing configured yet: report what is available and stay in setup,
        # rather than adopting a model the user never chose.
        return {**base, "state": ProviderState.SETUP_REQUIRED.value,
                "models": ids, "records": records,
                "detail": ("Escolhe um modelo Mistral nas Definições. "
                           f"Disponíveis: {', '.join(ids[:4])}.")}

    fast_ok = fast in ids
    strong_ok = strong in ids
    if fast_ok and strong_ok:
        detail = f"Pronto. Conversa: '{fast}'. Complexo: '{strong}'."
    elif fast_ok:
        detail = (f"Conversa pronta com '{fast}'. O modelo complexo '{strong}' não existe "
                  f"nesta conta; pedidos complexos usam '{fast}'.")
    else:
        detail = (f"O modelo '{fast}' não existe nesta conta. "
                  f"Disponíveis: {', '.join(ids[:4])}.")

    return {
        **base,
        "state": ProviderState.READY.value if fast_ok else ProviderState.MODEL_UNAVAILABLE.value,
        "models": ids, "records": records,
        "tiers": {"fast": fast, "complex": strong if strong_ok else fast},
        "tiers_ok": {"fast": fast_ok, "complex": strong_ok},
        "detail": detail,
    }


__all__ = [
    "MISTRAL_API_BASE",
    "MISTRAL_SECRET_NAME",
    "MistralAPIError",
    "MistralChat",
    "build_request",
    "describe_mistral",
    "is_chat_model",
    "is_configured",
    "list_mistral_models",
    "mistral_api_key",
    "model_ids",
    "rank_model",
    "rate_limit_headers",
    "supports_tool_calling",
    "test_mistral",
    "to_messages",
    "tool_call_id",
]
