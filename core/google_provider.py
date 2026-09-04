"""Google Gemini as a first-class Nano cloud provider.

WHY DIRECT HTTP AND NOT THE SDK
-------------------------------
The obvious move is ``pip install google-genai``. It was rejected on purpose.
Nano's runtime dependency list is nine packages, and the official SDK pulls
google-auth, googleapis-common-protos, protobuf, pydantic, anyio, websockets
and their transitive tree behind it -- for a REST API that is, on the wire, a
POST with a JSON body and one header. The repository already depends on
``httpx``, already speaks raw HTTP to the Groq /models endpoint and to Ollama's
/api/chat, and the MSI has to stay small. So this module speaks the documented
REST surface directly and owns its own error mapping, which is also what lets
every failure land in Nano's existing taxonomy instead of a vendor exception
hierarchy.

WHAT THIS MODULE IS NOT
-----------------------
It is not an execution path. Gemini produces *tool intentions*; every one of
them still travels MODEL -> REQUEST -> POLICY -> PERMISSION -> ToolExecutor ->
NARROW TOOL. Nothing here calls a tool, and nothing here can.

THE KEY
-------
The API key is sent in the ``x-goog-api-key`` header, never in the query
string. That is deliberate: a ``?key=`` URL ends up in httpx exception text,
in proxy logs and in tracebacks, and this project has already had one key
exposed. Nothing in this module logs the key, formats it into a message, or
returns it to a caller; the only thing that ever leaves is
``secret_store.mask()`` output and booleans.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Iterable

import httpx

from core import secret_store

logger = logging.getLogger("nano.google")

GOOGLE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"

#: The credential identifier in the encrypted store. Deliberately distinct from
#: ``groq_api_key``: one provider's key must never overwrite or satisfy
#: another's, and removing one must not disable the other.
GOOGLE_SECRET_NAME = "google_api_key"

#: Families Nano prefers, best first. Concrete ids are DISCOVERED from the
#: account -- pinning a literal id is what left this project calling a
#: decommissioned Groq model and 404-ing on every message, and Google
#: deprecates aliases on a schedule of its own. These are ranking hints for the
#: model picker, never a claim that a given id exists.
_PREFERRED_FAMILIES = (
    "gemini-flash-lite", "gemini-flash", "gemini-pro",
    "gemini-2.5-flash-lite", "gemini-2.5-flash", "gemini-2.0-flash-lite",
    "gemini-2.0-flash", "gemini-2.5-pro", "gemma",
)

#: Model ids that exist on the account but cannot serve a chat turn.
_NON_CHAT_MARKERS = (
    "embedding", "embed", "aqa", "imagen", "veo", "tts", "text-to-speech",
    "native-audio", "live-", "-live", "learnlm-embed",
)

#: Families that reject a separate system instruction and must have it folded
#: into the first user turn instead.
#:
#: EMPTY, AND MEASURED THAT WAY. Gemma was listed here as a conservative guess
#: while its behaviour was unconfirmed. It is confirmed now: both
#: gemma-4-31b-it and gemma-4-26b-a4b-it accept ``systemInstruction`` with a
#: 200 and demonstrably receive its contents. The mechanism stays because a
#: future family may need it; the guess does not.
_NO_SYSTEM_INSTRUCTION_FAMILIES: tuple[str, ...] = ()

#: Families that do not accept ``tools``/``functionDeclarations``. Sending them
#: is a 400, and a 400 is classified BAD_REQUEST, which never falls back -- so
#: an unsupported family would break the turn outright rather than degrade.
#:
#: ALSO EMPTY, AND THE GUESS HERE WAS COSTLY. Gemma was assumed toolless;
#: asked live, both Gemma 4 models return a correct native functionCall --
#: ``pc_volume_set(level=30)`` for "Poe o volume a 30 por cento" -- with
#: structured arguments, and they correctly call nothing at all for "Qual e a
#: capital de Portugal?". Withholding the tools did not make Nano safer: with
#: no tool to call, Gemma answered "Volume definido para 30%" and reported an
#: action that had never happened. A model that cannot act must say so, and the
#: way to get that is to stop lying to it about what it can do.
_NO_TOOL_CALLING_FAMILIES: tuple[str, ...] = ()

#: Families whose discovery metadata claims ``thinking`` but whose
#: generateContent endpoint rejects a reasoning budget outright.
#:
#: This one is not a guess either, and it is the reason the metadata cannot be
#: trusted alone: ListModels reports ``"thinking": true`` for both Gemma 4
#: models, while sending them ``thinkingConfig.thinkingBudget`` answers
#: 400 "Thinking budget is not supported for this model." Metadata describes
#: what the model does; it does not promise which knobs the endpoint accepts.
_NO_THINKING_FAMILIES = ("gemma",)

#: JSON-Schema keys Google's Schema type accepts. Anything else is dropped
#: rather than forwarded: an unknown key is a 400 on every single request, and
#: BAD_REQUEST is the one failure class that must never fall back.
_SCHEMA_KEYS = frozenset({
    "type", "format", "description", "nullable", "enum", "items", "properties",
    "required", "minimum", "maximum", "minLength", "maxLength", "pattern",
    "anyOf",
})

_JSON_TYPES = {
    "string": "STRING", "number": "NUMBER", "integer": "INTEGER",
    "boolean": "BOOLEAN", "array": "ARRAY", "object": "OBJECT",
}

#: Reasoning budgets, in thinking tokens, by Nano task class. Ordinary
#: conversation must not pay for reasoning it does not need; only an explicit
#: COMPLEX classification buys a real budget. -1 would mean "model decides",
#: which is precisely the unbounded spend this table exists to avoid.
_THINKING_BUDGET = {
    "SMALL_TALK": 0,
    "QUESTION": 0,
    "ACTION": 0,
    "COMPLEX": 2048,
}

DEFAULT_TIMEOUT = httpx.Timeout(90.0, connect=10.0)


class GoogleAPIError(RuntimeError):
    """A Google failure shaped so ``provider_failures.classify`` understands it.

    ``status_code`` and ``response.headers`` are the two things the shared
    classifier reads, so carrying them here means Gemini failures land in the
    same taxonomy as Groq's with no provider-specific branch at the call site.

    A 429 from Google carries its wait in ``error.details[].retryDelay`` rather
    than in a header, so it is normalised into a synthetic ``retry-after``
    header on the way past -- ``providers.parse_rate_limit`` already knows how
    to read "27s".
    """

    def __init__(self, status_code: int, message: str = "", headers: dict | None = None):
        super().__init__(message or f"Google API error ({status_code})")
        self.status_code = int(status_code)
        self.response = type("_R", (), {"status_code": self.status_code,
                                        "headers": headers or {}})()


# --------------------------------------------------------------------------
# Credentials
# --------------------------------------------------------------------------

def google_api_key() -> str:
    """The stored key, or the environment fallback. Never logged, never returned
    to the renderer."""
    return secret_store.get_secret(GOOGLE_SECRET_NAME)


def is_configured() -> bool:
    return bool(google_api_key())


# --------------------------------------------------------------------------
# Model discovery
# --------------------------------------------------------------------------

def is_chat_model(model_id: str) -> bool:
    lowered = model_id.lower()
    return not any(marker in lowered for marker in _NON_CHAT_MARKERS)


def rank_model(model_id: str) -> int:
    """Lower is better. Unknown families sort last but stay usable."""
    lowered = model_id.lower()
    best = len(_PREFERRED_FAMILIES)
    for index, family in enumerate(_PREFERRED_FAMILIES):
        if family in lowered:
            best = min(best, index)
    return best


def _family(model_id: str) -> str:
    return str(model_id or "").split("/")[-1].lower()


def supports_system_instruction(model_id: str) -> bool:
    return not any(f in _family(model_id) for f in _NO_SYSTEM_INSTRUCTION_FAMILIES)


def supports_tool_calling(model_id: str, *, metadata: dict | None = None) -> bool:
    return not any(f in _family(model_id) for f in _NO_TOOL_CALLING_FAMILIES)


def supports_thinking(model_id: str, *, metadata: dict | None = None) -> bool:
    """Whether a reasoning budget may be sent for this model.

    Read from the model's own discovery metadata when the account reports it.
    There is no inference from the id: a guess that a family "should" support
    thinking turns into a 400 on every message, and BAD_REQUEST never falls
    back. Absent evidence, Nano sends no thinkingConfig at all, which every
    generateContent model accepts.
    """
    if any(f in _family(model_id) for f in _NO_THINKING_FAMILIES):
        # The account SAYS these think. The endpoint refuses to be told how
        # much, so Nano must not tell it -- see _NO_THINKING_FAMILIES.
        return False
    if not isinstance(metadata, dict):
        return False
    for key in ("thinking", "supportsThinking", "thinkingSupported"):
        value = metadata.get(key)
        if isinstance(value, bool):
            return value
    return False


def _headers(api_key: str) -> dict[str, str]:
    return {"x-goog-api-key": api_key, "Content-Type": "application/json"}


def list_google_models(api_key: str | None = None, *, timeout: float = 10.0
                       ) -> tuple[list[dict[str, Any]], str | None]:
    """Chat-capable models on the account, best first. Returns (models, error).

    Each entry is safe metadata only: id, display name, token limits and the
    capability flags derived from them. No credential is included.
    """
    key = (api_key or google_api_key()).strip()
    if not key:
        return [], "no_api_key"

    records: list[dict[str, Any]] = []
    page_token = ""
    try:
        with httpx.Client(timeout=timeout) as client:
            for _ in range(10):                     # bounded: never loop forever
                params: dict[str, Any] = {"pageSize": 200}
                if page_token:
                    params["pageToken"] = page_token
                response = client.get(f"{GOOGLE_API_BASE}/models",
                                      headers=_headers(key), params=params)
                if response.status_code in (401, 403):
                    return [], "invalid_api_key"
                if response.status_code == 429:
                    return [], "rate_limited"
                if not response.is_success:
                    return [], f"http_{response.status_code}"
                payload = response.json()
                for item in payload.get("models") or []:
                    record = _model_record(item)
                    if record is not None:
                        records.append(record)
                page_token = str(payload.get("nextPageToken") or "")
                if not page_token:
                    break
    except httpx.HTTPError as exc:
        # Deliberately the exception TYPE, not str(exc): httpx puts the full
        # request URL in its message and this must stay credential-free even
        # though the key travels in a header.
        return [], f"network_error: {type(exc).__name__}"
    except (ValueError, TypeError) as exc:
        return [], f"bad_response: {type(exc).__name__}"

    records.sort(key=lambda r: (rank_model(r["id"]), r["id"]))
    return records, None


def _supports_generate_content(methods: list[str]) -> bool:
    """Whether this model can serve a chat turn at all, streaming included.

    An empty ``supportedGenerationMethods`` means the listing did not say, and
    an unstated capability is not a denied one: the model stays usable and the
    streaming call is what will actually decide.
    """
    return True if not methods else "generateContent" in methods


def _model_record(item: dict) -> dict[str, Any] | None:
    """One ListModels entry, reduced to what Nano needs. None if unusable."""
    raw_name = str(item.get("name") or "")
    model_id = raw_name.split("models/", 1)[-1] if raw_name else ""
    if not model_id:
        return None
    methods = [str(m) for m in (item.get("supportedGenerationMethods") or [])]
    if methods and not any(m in ("generateContent", "streamGenerateContent") for m in methods):
        return None
    if not is_chat_model(model_id):
        return None
    return {
        "id": model_id,
        "display_name": str(item.get("displayName") or model_id),
        "description": str(item.get("description") or "")[:280],
        "input_tokens": item.get("inputTokenLimit"),
        "output_tokens": item.get("outputTokenLimit"),
        # STREAMING IS DERIVED FROM generateContent, NOT FROM ITS OWN ENTRY.
        #
        # This read "streamGenerateContent" in methods, which reported False for
        # every single model on the account -- all 40 of them -- while Nano's
        # only Google code path is GoogleChat.stream, which POSTs to
        # ``:streamGenerateContent`` and has been streaming answers from those
        # same models since the day it shipped. ListModels simply does not
        # enumerate the streaming variant on this API surface; the SSE endpoint
        # is served off generateContent. Measured, not assumed: gemini-3.8-flash
        # advertises only ["generateContent", "countTokens", ...] and answered a
        # live SSE request with text and a native functionCall.
        #
        # This field is what the Settings page is entitled to show about a
        # model, so a False here is Nano stating a limitation it does not have
        # -- the mirror image of claiming a capability it lacks, and wrong for
        # the same reason.
        "streaming": _supports_generate_content(methods),
        "tool_calling": supports_tool_calling(model_id),
        "thinking": supports_thinking(model_id, metadata=item),
        "system_instruction": supports_system_instruction(model_id),
    }


def model_ids(records: Iterable[dict]) -> list[str]:
    return [str(r.get("id")) for r in records if r.get("id")]


def test_google(api_key: str | None = None) -> dict[str, Any]:
    """Validate a key without storing it. Used by the Test connection button."""
    started = time.monotonic()
    records, error = list_google_models(api_key)
    latency_ms = int((time.monotonic() - started) * 1000)

    if error == "no_api_key":
        return {"ok": False, "error": "no_api_key",
                "detail": "Nenhuma chave de API configurada.", "models": []}
    if error == "invalid_api_key":
        return {"ok": False, "error": "invalid_api_key",
                "detail": "A chave de API foi recusada pelo Google.", "models": []}
    if error == "rate_limited":
        return {"ok": False, "error": "rate_limited",
                "detail": "O Google respondeu com limite de pedidos. Tenta daqui a pouco.",
                "models": []}
    if error:
        return {"ok": False, "error": error,
                "detail": f"Não foi possível contactar o Google ({error}).", "models": []}
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
# Request shaping: Nano's OpenAI-style messages -> Google contents
# --------------------------------------------------------------------------

def _sanitize_schema(schema: Any) -> Any:
    """Reduce a JSON Schema to the subset Google's Schema type accepts.

    Nano's tool schemas are written for the OpenAI wire format and carry keys
    Google rejects outright (``default`` is the common one). A rejected key is
    a 400 on every request, and BAD_REQUEST is the failure class that must
    never fall back -- so an unsupported key would take the whole provider
    down rather than degrade.

    ``default`` is not merely dropped: its value is folded into the
    description, so the model keeps the information the schema was carrying.
    """
    if isinstance(schema, list):
        return [_sanitize_schema(entry) for entry in schema]
    if not isinstance(schema, dict):
        return schema

    cleaned: dict[str, Any] = {}
    for key, value in schema.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "type" and isinstance(value, str):
            cleaned[key] = _JSON_TYPES.get(value.lower(), value.upper())
        elif key in ("properties",) and isinstance(value, dict):
            cleaned[key] = {k: _sanitize_schema(v) for k, v in value.items()}
        elif key in ("items", "anyOf"):
            cleaned[key] = _sanitize_schema(value)
        else:
            cleaned[key] = value

    default = schema.get("default")
    if default is not None:
        hint = f"Por omissão: {json.dumps(default, ensure_ascii=False)}."
        existing = str(cleaned.get("description") or "").strip()
        cleaned["description"] = f"{existing} {hint}".strip()
    return cleaned


def to_function_declarations(tools: list[dict] | None) -> list[dict]:
    """OpenAI ``tools`` -> Google ``functionDeclarations``. Names are unchanged.

    The name has to survive verbatim: it is the key the ToolExecutor registry
    is looked up by, and a renamed tool is an unroutable tool.
    """
    declarations: list[dict] = []
    for tool in tools or []:
        function = (tool or {}).get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict) or not function.get("name"):
            continue
        declaration: dict[str, Any] = {
            "name": str(function["name"]),
            "description": str(function.get("description") or "")[:1024],
        }
        parameters = function.get("parameters")
        if isinstance(parameters, dict) and parameters.get("properties"):
            declaration["parameters"] = _sanitize_schema(parameters)
        declarations.append(declaration)
    return declarations


def _tool_response_payload(raw: str) -> dict[str, Any]:
    """Google wants a JSON OBJECT back from a tool, not a string.

    Nano serialises tool results to a string for the OpenAI wire format (and,
    for untrusted external content, appends a fenced block after the JSON). A
    string is rejected here, so anything that is not a decodable object is
    carried under a single ``result`` key -- the fence and its contents reach
    the model intact rather than being dropped.
    """
    text = str(raw or "")
    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return {"result": text}
    if isinstance(parsed, dict):
        return parsed
    return {"result": parsed}


def to_contents(messages: list[dict]) -> tuple[list[dict], str]:
    """Split Nano's message list into Google ``contents`` and a system string.

    Three shapes have to survive the translation, because losing any one of
    them silently changes what the model is answering:

    * ``system``       -> returned separately (the caller decides where it goes)
    * ``tool_calls``   -> a ``model`` turn whose parts are ``functionCall``
    * ``role: tool``   -> a ``user`` turn whose parts are ``functionResponse``

    Tool results are matched back to their call by NAME, taken from the
    preceding assistant turn via ``tool_call_id``. Google identifies a function
    response by name, not by id, and guessing the wrong name would attach a
    result to the wrong call.
    """
    contents: list[dict] = []
    system_parts: list[str] = []
    call_names: dict[str, str] = {}

    for message in messages or []:
        role = str(message.get("role") or "")
        content = message.get("content")

        if role == "system":
            if content:
                system_parts.append(str(content))
            continue

        if role == "tool":
            name = call_names.get(str(message.get("tool_call_id") or ""), "")
            if not name:
                name = str(message.get("name") or "tool")
            contents.append({"role": "user", "parts": [{
                "functionResponse": {
                    "name": name,
                    "response": _tool_response_payload(content),
                },
            }]})
            continue

        if role == "assistant":
            parts: list[dict] = []
            if content:
                parts.append({"text": str(content)})
            for call in message.get("tool_calls") or []:
                function = (call or {}).get("function") or {}
                name = str(function.get("name") or "")
                if not name:
                    continue
                call_names[str(call.get("id") or "")] = name
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments or "{}")
                    except (ValueError, TypeError):
                        # Dropped rather than sent as {}: an empty argument map
                        # invites the model to re-issue the call with arguments
                        # of its own invention, which the per-turn execution
                        # ledger would not recognise as a repeat.
                        logger.warning("Dropping a tool call for Google: arguments are not valid JSON.")
                        continue
                if not isinstance(arguments, dict):
                    continue
                parts.append({"functionCall": {"name": name, "args": arguments}})
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if content:
            contents.append({"role": "user", "parts": [{"text": str(content)}]})

    # Google requires the conversation to open on a user turn.
    while contents and contents[0]["role"] != "user":
        contents.pop(0)

    return contents, "\n".join(part for part in system_parts if part)


def build_request(model: str, messages: list[dict], tools: list[dict] | None, *,
                  temperature: float = 0.65, max_tokens: int = 1536,
                  task: str | None = None, metadata: dict | None = None) -> dict[str, Any]:
    """The full JSON body for one generateContent call.

    Reasoning configuration lives HERE, in provider adaptation, rather than
    scattered through the Brain: a provider that has no such concept simply
    produces a body without the field, and the Brain never learns the
    difference.
    """
    contents, system_text = to_contents(messages)

    body: dict[str, Any] = {
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": int(max_tokens),
        },
    }

    if system_text:
        if supports_system_instruction(model):
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        elif contents:
            # The family takes no separate system instruction, so the rules are
            # prepended to the first user turn instead of being discarded.
            first = contents[0]
            first["parts"] = [{"text": system_text}, *first.get("parts", [])]
        else:
            contents = [{"role": "user", "parts": [{"text": system_text}]}]

    body["contents"] = contents

    declarations = to_function_declarations(tools) if supports_tool_calling(model) else []
    if declarations:
        body["tools"] = [{"functionDeclarations": declarations}]
        body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

    if supports_thinking(model, metadata=metadata):
        budget = _THINKING_BUDGET.get(str(task or "").upper(), 0)
        # A ZERO BUDGET IS NOT A UNIVERSALLY VALID REQUEST, AND ASKING FOR ONE
        # BROKE EVERY TURN.
        #
        # Measured against the live account, not inferred: gemini-3.5-flash-lite
        # answers 400 INVALID_ARGUMENT to thinkingBudget 0 while accepting a
        # positive budget, a thinkingLevel, or no thinkingConfig at all. On that
        # model Nano's ordinary traffic -- SMALL_TALK, QUESTION and ACTION, which
        # is nearly all of it -- asked for exactly the one value it rejects, so
        # every message failed as BAD_REQUEST, the single failure class that is
        # deliberately never retried and never failed over.
        #
        # ListModels cannot separate the two cases: it reports `thinking: true`
        # identically for gemini-2.5-flash (0 accepted) and for
        # gemini-3.5-flash-lite (0 rejected), so there is no evidence to read and
        # guessing from the version number is the mistake this module already
        # refuses to make elsewhere. Omitting the field instead is accepted by
        # every model tested and cannot 400.
        #
        # What that costs, stated plainly: with no thinkingConfig the model
        # applies its own default effort, so on a model that would have honoured
        # 0 Nano no longer forces reasoning off -- it only declines to buy extra.
        # A wrong-but-cheap turn is worse than a slightly costlier one; a turn
        # that cannot happen at all is worse than both.
        if budget > 0:
            body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": int(budget)}

    return body


# --------------------------------------------------------------------------
# Response shaping
# --------------------------------------------------------------------------

def _retry_after_headers(payload: dict) -> dict[str, str]:
    """Turn Google's RetryInfo into the header Nano's rate-limit parser reads."""
    details = ((payload.get("error") or {}).get("details") or [])
    for detail in details:
        if not isinstance(detail, dict):
            continue
        delay = detail.get("retryDelay")
        if isinstance(delay, str) and delay.strip():
            return {"retry-after": delay.strip()}
    return {}


def _error_message(payload: dict, status: int) -> str:
    """A short, credential-free description of a Google error."""
    message = str(((payload.get("error") or {}).get("message")) or "")
    status_name = str(((payload.get("error") or {}).get("status")) or "")
    text = " ".join(part for part in (status_name, message) if part).strip()
    return (text or f"HTTP {status}")[:300]


def _raise_for_error(status: int, raw: str) -> None:
    try:
        payload = json.loads(raw or "{}")
    except (ValueError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raise GoogleAPIError(status, _error_message(payload, status),
                         _retry_after_headers(payload))


class GoogleChat:
    """The streaming transport. One instance per configured key.

    Holds no conversation state: every call is given its full message list, the
    same way ``AsyncGroq`` is. That is what lets the Brain fail over between
    providers mid-turn without either of them carrying a stale view.
    """

    def __init__(self, api_key: str, *, base_url: str = GOOGLE_API_BASE,
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

        ``collector`` is filled with the same keys the Groq path uses --
        ``first_token_at``, ``tool_calls`` (id/name/args) and ``usage`` -- so
        the caller handles both providers with one branchless body.

        Cancellation is the caller's ``asyncio.CancelledError``: it propagates
        out of the ``async for``, the context manager closes the connection,
        and nothing is left half-read.
        """
        if not self._api_key:
            raise GoogleAPIError(401, "no_api_key")

        sink = collector if collector is not None else {}
        calls: list[dict[str, str]] = []
        url = f"{self.base_url}/models/{model}:streamGenerateContent"

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, headers=_headers(self._api_key),
                                         params={"alt": "sse"}, json=body) as response:
                    if response.status_code >= 400:
                        raw = (await response.aread()).decode("utf-8", "replace")
                        _raise_for_error(response.status_code, raw)
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
                        for text in self._absorb(payload, calls, sink):
                            yield text
        except (GoogleAPIError, asyncio.CancelledError):
            raise
        except httpx.TimeoutException as exc:
            raise GoogleAPIError(408, f"timeout: {type(exc).__name__}") from exc
        except httpx.HTTPError as exc:
            # The type only. httpx puts the request URL in its message and
            # nothing derived from the request may reach a user-facing string.
            raise GoogleAPIError(503, f"transport: {type(exc).__name__}") from exc

        sink["tool_calls"] = [call for call in calls if call.get("name")]

    @staticmethod
    def _absorb(payload: dict, calls: list[dict], sink: dict) -> list[str]:
        """Pull text, tool calls and usage out of one SSE frame."""
        texts: list[str] = []
        for candidate in payload.get("candidates") or []:
            content = (candidate or {}).get("content") or {}
            for part in content.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                # A thinking part is the model's private reasoning. It is never
                # shown and never stored: leaking <think> text into the answer
                # is a defect this project has already shipped once.
                if part.get("thought"):
                    continue
                text = part.get("text")
                if isinstance(text, str) and text:
                    if sink.get("first_token_at") is None:
                        sink["first_token_at"] = time.monotonic()
                    texts.append(text)
                call = part.get("functionCall")
                if isinstance(call, dict) and call.get("name"):
                    arguments = call.get("args")
                    if not isinstance(arguments, dict):
                        arguments = {}
                    calls.append({
                        "id": f"call_{len(calls)}",
                        "name": str(call["name"]),
                        # The rest of Nano stores arguments as a JSON STRING,
                        # because that is the OpenAI wire format the history
                        # has to stay valid in. Google hands back a decoded
                        # map, so it is re-encoded here rather than at four
                        # call sites downstream.
                        "args": json.dumps(arguments, ensure_ascii=False),
                    })
            finish = (candidate or {}).get("finishReason")
            if finish:
                sink["finish_reason"] = str(finish)
        usage = payload.get("usageMetadata")
        if isinstance(usage, dict):
            sink["usage"] = {
                "prompt_tokens": usage.get("promptTokenCount"),
                "completion_tokens": usage.get("candidatesTokenCount"),
                "total_tokens": usage.get("totalTokenCount"),
                "thinking_tokens": usage.get("thoughtsTokenCount"),
            }
        return texts


# --------------------------------------------------------------------------
# Status for the UI
# --------------------------------------------------------------------------

def describe_google(configured_model: str = "", complex_model: str = "") -> dict[str, Any]:
    """Google status for the UI. Never returns or logs the key itself.

    Deliberately the same payload shape as ``providers.describe_groq``: the
    Settings page, the pill and the router all read providers through one
    structure, which is what keeps adding the next provider a matter of
    writing one more ``describe_*``.
    """
    from core.providers import ProviderState        # local: avoids a cycle

    fast = str(configured_model or "")
    strong = str(complex_model or fast)
    secret = secret_store.describe(GOOGLE_SECRET_NAME)
    base = {
        "id": "google", "name": "Google", "kind": "cloud", "role": "cloud",
        "model": fast, "models": [], "records": [], "secret": secret,
        "tiers": {"fast": fast, "complex": strong},
    }

    if not secret["configured"]:
        return {**base, "state": ProviderState.SETUP_REQUIRED.value,
                "detail": "Adiciona uma chave de API do Google nas Definições para usar o Gemini."}

    records, error = list_google_models()
    if error:
        state = ProviderState.ERROR if error == "invalid_api_key" else ProviderState.UNAVAILABLE
        detail = ("A chave de API do Google foi recusada. Verifica-a nas Definições."
                  if error == "invalid_api_key"
                  else f"O Google não está acessível ({error}).")
        return {**base, "state": state.value, "detail": detail}

    ids = model_ids(records)
    if not fast:
        # Nothing configured yet: report what is available and stay in setup,
        # rather than adopting a model the user never chose.
        return {**base, "state": ProviderState.SETUP_REQUIRED.value,
                "models": ids, "records": records,
                "detail": f"Escolhe um modelo Google nas Definições. Disponíveis: {', '.join(ids[:4])}."}

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
    "GOOGLE_API_BASE",
    "GOOGLE_SECRET_NAME",
    "GoogleAPIError",
    "GoogleChat",
    "build_request",
    "describe_google",
    "google_api_key",
    "is_configured",
    "list_google_models",
    "model_ids",
    "rank_model",
    "supports_system_instruction",
    "supports_thinking",
    "supports_tool_calling",
    "test_google",
    "to_contents",
    "to_function_declarations",
]
