"""Nano Assistant — decision engine, streaming and tool-calling.

This module contains the ``Brain`` class that manages the conversation between
the user and the language model, supporting real-time streaming, autonomous
tool-calling and transparent fallback to local models (Ollama).
"""

from __future__ import annotations
import asyncio
import json
import logging
import time
from typing import AsyncIterator, Any
import httpx
from groq import AsyncGroq
from core.config import load_config
from core.local_runtime import choose_model
from core.plugin_loader import get_all_tools
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine
from core.errors import ToolExecutionError, GuardrailError
from core.model_router import ModelRequest, ModelRouter, PrivacyLevel, TaskType
from core import capabilities, model_selection, provider_failures, provider_status, providers
from core.trust import TRUST_BOUNDARY_SYSTEM_RULES, TrustLevel, wrap_untrusted

logger = logging.getLogger("nano.brain")
GROQ_MODEL = providers.DEFAULT_FAST_MODEL
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MAX_TOOL_ROUNDS = 8

# Provider status costs an HTTP round trip per provider. Routing every message
# through a fresh probe was adding four Ollama /api/tags calls (~400-600 ms) to
# each cloud chat, so the snapshot is cached. That cache now lives in
# core.provider_status and is SHARED with the Settings UI, which used to probe
# the same account independently once per second. See provider_status for the
# TTL and for why the chat path must use the async accessor.


class RateLimited(RuntimeError):
    """Groq returned 429. Carries the real headers so the UI can explain it."""

    def __init__(self, info: dict[str, Any]):
        self.info = info or {}
        self.message = providers.rate_limit_message(self.info)
        super().__init__(self.message)

# The prompt is assembled per turn instead of being one fixed block. Identity
# and language always apply; the tool and trust-boundary rules are only worth
# their ~250 tokens when tools are actually in play. On a plain "Olá" that is
# most of the prompt, and prompt tokens are the scarce resource here (8000 TPM).
NANO_PERSONA = """És o Nano Assistant (ou simplesmente Nano), um assistente virtual executivo, rápido e inteligente.
O utilizador deve ser tratado com simpatia, empatia, precisão e foco.
Responde em Português de Portugal com clareza e estilo direto. Sê conciso: responde
ao que foi perguntado, sem repetir perguntas anteriores nem adicionar secções que
ninguém pediu.
"""

NANO_TOOL_RULES = """
Regras de ferramentas:
- Usa ferramentas sempre que precisares de interagir com o sistema operativo, ficheiros, web ou dispositivos.
- Ações destrutivas, alterações no sistema ou operações externas sensíveis exigem confirmação através dos guardrails.
- Nunca inventes resultados de ferramentas nem fales de segredos de sistema.
- Quando aprenderes preferências ou factos duradouros sobre o utilizador, usa 'remember_fact'.
"""

# Kept as the full composition: it is the documented shape of Nano's system
# prompt and the hardening tests assert the trust boundary is present in it.
SYSTEM_PROMPT = NANO_PERSONA + NANO_TOOL_RULES + TRUST_BOUNDARY_SYSTEM_RULES

def _ollama_chat_url(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/api/chat") else f"{url}/api/chat"

async def _stream_text_chunks(text: str, chunk_size: int = 4, delay: float = 0.008) -> AsyncIterator[str]:
    """Divide texto em pequenos blocos simulando fluidez no streaming."""
    if not text:
        return
    for i in range(0, len(text), chunk_size):
        yield text[i:i + chunk_size]
        if delay > 0:
            await asyncio.sleep(delay)

def _looks_like_a_leaked_tool_call(text: str) -> bool:
    """True when a local model emitted a tool call as CONTENT instead of a call.

    Observed for real: asked "Como está a RAM?", qwen3:8b answered with the
    literal text `{"name": "pc", "arguments": {}}`. That is a malformed
    attempt at the tool protocol which leaked into the answer, and the user was
    shown raw JSON.

    THIS FUNCTION ONLY DETECTS. It never parses the blob into a call and never
    executes it -- "pc" is not even a real tool, and executing model prose is
    precisely the unsafe shortcut this design refuses. The caller replaces the
    text with a clean sentence and logs the original for diagnostics.
    """
    stripped = str(text or "").strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return False
    if len(stripped) > 600:
        return False
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return False
    if not isinstance(parsed, dict):
        return False
    keys = set(parsed)
    return bool(keys & {"name", "function", "tool", "tool_name"}) and bool(
        keys & {"arguments", "parameters", "args", "name", "function"})


class _SimulatedGroqResponse:
    def __init__(self, status_code: int, headers: dict):
        self.status_code = status_code
        self.headers = headers


class SimulatedGroqFailure(RuntimeError):
    """A Groq failure injected on purpose. DEVELOPMENT AND TESTING ONLY.

    Set NANO_SIMULATE_GROQ_FAILURE to exercise the fallback without waiting to
    organically exhaust the real token budget:

        rate_limit    a 429 carrying realistic headers (the default)
        timeout       a connection timeout
        server_error  a 503
        auth_error    a 401, to check the failure that must NOT fall back
        off / unset   normal operation

    It is raised at the same point a real SDK error would surface, so the whole
    production path downstream -- classification, cooldown, continuation,
    duplicate protection -- is the real one. Nothing about it is reachable
    unless the variable is set.
    """

    def __init__(self, status_code: int, headers: dict | None = None, message: str = ""):
        super().__init__(message or f"simulated Groq failure ({status_code})")
        self.status_code = status_code
        self.response = _SimulatedGroqResponse(status_code, headers or {})


_SIMULATED_FAILURES: dict[str, tuple[int, dict]] = {
    "rate_limit": (429, {"retry-after": "3", "x-ratelimit-reset-tokens": "47s",
                         "x-ratelimit-limit-tokens": "8000",
                         "x-ratelimit-remaining-tokens": "412"}),
    "timeout": (408, {}),
    "server_error": (503, {}),
    "auth_error": (401, {}),
}


def _simulated_groq_failure() -> None:
    import os

    mode = (os.getenv("NANO_SIMULATE_GROQ_FAILURE") or "").strip().lower()
    if not mode or mode in {"off", "0", "false", "none"}:
        return
    status, headers = _SIMULATED_FAILURES.get(mode, _SIMULATED_FAILURES["rate_limit"])
    logger.warning("NANO_SIMULATE_GROQ_FAILURE=%s active: injecting %d", mode, status)
    raise SimulatedGroqFailure(status, headers)


class Brain:
    def __init__(self, api_key: str, guardrails: GuardrailsEngine, memory: MemoryEngine, config: dict | None = None, permission_manager: Any | None = None, tool_executor: Any | None = None):
        cfg = config if config is not None else load_config()
        mem_cfg, local_cfg = cfg.get("memory") or {}, cfg.get("local") or {}
        self.groq_enabled = bool(api_key.strip())
        # max_retries=0 is deliberate. The SDK default of 2 sleeps through a
        # 429 without telling anyone, which is what produced the measured
        # 30-46 second freezes. Nano handles 429 itself and surfaces it.
        self.client = AsyncGroq(api_key=api_key, max_retries=0) if self.groq_enabled else None
        self.guardrails, self.memory = guardrails, memory
        self.permission_manager = permission_manager
        # The Brain never runs a tool itself. Every call is delegated to the
        # central execution authority, which owns policy, permission, scope
        # validation, execution, verification and audit.
        self.tool_executor = tool_executor or self._build_default_executor(permission_manager)
        # AUTO | CLOUD | LOCAL. Read at each turn so a change in Settings takes
        # effect immediately rather than at the next restart.
        self.provider_mode = str(cfg.get("provider_mode") or "AUTO").upper()
        # Which provider actually answered the last turn, so the UI can show a
        # fallback rather than implying the primary responded.
        self.last_provider_used: str | None = None
        # Safe, per-response diagnostics for the UI's technical details panel.
        # Never contains the key, the prompt or any tool argument.
        self.last_metadata: dict[str, Any] = {}
        # Tool calls already executed in the CURRENT turn, keyed by
        # (name, arguments). Provider failover happens inside a turn, so this
        # is what stops a consequential action running twice. See _run_tool.
        self._turn_tool_results: dict[str, dict] = {}
        self.conversation: list[dict] = []
        self.groq_model = str(cfg.get("groq_model") or providers.DEFAULT_FAST_MODEL)
        # Two cloud tiers; the strong one is only reached by an explicit
        # COMPLEX classification, never by message length.
        self.groq_fast_model = str(cfg.get("groq_fast_model") or self.groq_model)
        self.groq_complex_model = str(cfg.get("groq_complex_model") or providers.DEFAULT_COMPLEX_MODEL)
        self.local_enabled = bool(local_cfg.get("enabled", cfg.get("ollama_enabled", True)))
        self.local_profile = choose_model(cfg)
        self.model_router = ModelRouter(cfg, cloud_api_key=api_key)

        # An explicit local.model in the config is an instruction, not a hint.
        # The router used to score every discovered Ollama model and could pick
        # a different one (e.g. qwen2.5-coder:3b instead of the configured
        # qwen3:8b) — a silent substitution that made the UI report READY for a
        # model the user never asked for. The router now only chooses when the
        # config says "auto".
        configured_local = str(local_cfg.get("model") or cfg.get("ollama_model") or "").strip()
        if configured_local and configured_local.lower() not in {"auto", "automatic", "automatico", "automático"}:
            self.ollama_model = configured_local
        else:
            self.ollama_model = self.model_router.select({
                "task_type": TaskType.CHAT,
                "privacy_level": PrivacyLevel.NORMAL,
                "requires_tools": False,
                "requires_reasoning": False,
                "local_only": True,
            }).get("model") or self.local_profile.model
        self.ollama_url = _ollama_chat_url(str(local_cfg.get("url") or cfg.get("ollama_url") or OLLAMA_BASE_URL))
        self.local_context = max(1024, int(local_cfg.get("max_context", 4096)))
        self.history_messages = max(2, int(mem_cfg.get("history_messages", 20)))
        self.max_history_chars = max(1000, int(mem_cfg.get("max_history_chars", 8000)))
        # The real ceiling, in the unit the Groq limit is denominated in.
        # See _trim_conversation for why a character budget was the wrong tool.
        self.max_history_tokens = max(256, int(mem_cfg.get("max_history_tokens", 1200)))
        self.facts_enabled = bool(mem_cfg.get("facts_enabled", True))
        self.rag_enabled = bool(mem_cfg.get("rag_enabled", True))
        self.rag_results = max(1, int(mem_cfg.get("rag_results", 3)))
        self.rag_max_chars = max(200, int(mem_cfg.get("rag_max_chars", 1500)))
        logger.info(
            "Nano Brain: Cloud=%s model=%s | Local=%s model=%s (%s, %.1f GB RAM)",
            self.groq_enabled, self.groq_model, self.local_enabled, self.ollama_model,
            self.local_profile.reason, self.local_profile.ram_gb
        )

    def reload_cloud_credentials(self) -> bool:
        """Re-read the Groq key after the user changes it in Settings.

        Without this the user would have to restart Nano for a newly saved key
        to take effect, which is exactly the friction the Settings flow exists
        to remove.
        """
        try:
            from core import secret_store

            key = secret_store.get_secret("groq_api_key")
        except Exception:
            logger.exception("Could not read the stored Groq credentials")
            return False

        self.groq_enabled = bool(key.strip())
        self.client = AsyncGroq(api_key=key, max_retries=0) if self.groq_enabled else None
        # A new key must be reflected immediately, not after the cache expires.
        self.invalidate_provider_snapshot()
        logger.info("Credenciais cloud recarregadas (configurado=%s)", self.groq_enabled)
        return self.groq_enabled

    def invalidate_provider_snapshot(self) -> None:
        """Drop the cached provider status (key changed, mode changed, ...).

        The snapshot now lives in the shared provider_status cache, so this
        clears it for every reader -- the Brain and the Settings UI alike --
        rather than only for this object.
        """
        provider_status.CACHE.invalidate()

    def load_history(self) -> int:
        """Seed the conversation from the database, already inside budget.

        get_context_window applies its own character window; trimming here as
        well means the very first message of a session pays the same bounded
        prompt as every later one, instead of carrying a boot-time backlog.
        """
        try:
            self.conversation = list(self.memory.get_context_window(self.history_messages, self.max_history_chars))
        except Exception as exc:
            logger.error("Falha ao carregar histórico: %s", exc)
            return 0
        self._trim_conversation()
        return len(self.conversation)

    @staticmethod
    def _estimate_tokens(message: dict) -> int:
        """Approximate the prompt cost of one message, in tokens.

        Deliberately an estimate and deliberately pessimistic. Tokenising
        properly would mean shipping and running a tokenizer on every turn for
        a number that only has to be roughly right; ~3.6 characters per token
        is close for Portuguese (accented words tokenise worse than English's
        ~4) and erring high keeps us inside the budget rather than outside it.

        Tool calls are counted too. A tool_calls payload is real prompt cost --
        it carries the function name and the full JSON arguments -- and
        measuring only ``content`` under-counted exactly the turns that grow
        fastest.
        """
        total = len(str(message.get("content") or ""))
        calls = message.get("tool_calls")
        if calls:
            try:
                total += len(json.dumps(calls, ensure_ascii=False))
            except (TypeError, ValueError):
                total += 200 * len(calls)
        # Per-message envelope: role, delimiters, and the model's own framing.
        return int(total / 3.6) + 4

    def conversation_tokens(self) -> int:
        """Estimated prompt cost of the history as it stands."""
        return sum(self._estimate_tokens(m) for m in self.conversation)

    def _trim_conversation(self):
        """Keep the conversation inside its TOKEN budget, oldest first.

        This was a character budget of ``max_history_chars * 2`` -- 16 000
        chars, roughly 4 000 tokens. On a Groq account with an 8 000
        tokens-per-minute ceiling that let a SINGLE message carry ~4 800 prompt
        tokens once the persona, tool rules and RAG excerpts were added; with
        an ACTION turn's 1 536 reserved completion tokens on top, one message
        could reserve ~6 300 of the 8 000 available in a minute. Two such
        messages inside a minute is a 429, which is how the rate-limit problem
        kept coming back after the tool-scoping work had fixed the tool half.

        The budget is now expressed in tokens directly, because tokens are what
        the ceiling is denominated in, and it is set from
        ``memory.max_history_tokens`` so it can be tuned without arithmetic.

        Trimming drops whole turns from the front. A ``tool`` message whose
        originating assistant turn has been removed is dropped with it: an
        orphaned tool result is both useless to the model and rejected by Groq.
        """
        budget = self.max_history_tokens
        total = self.conversation_tokens()
        while total > budget and len(self.conversation) > 2:
            removed = self.conversation.pop(0)
            total -= self._estimate_tokens(removed)
            while self.conversation and self.conversation[0].get("role") == "tool":
                orphan = self.conversation.pop(0)
                total -= self._estimate_tokens(orphan)

    def _facts_block(self) -> str:
        if not self.facts_enabled:
            return ""
        try:
            facts = self.memory.get_facts()
        except Exception:
            logger.exception("Falha ao ler factos persistentes")
            return ""
        return "\n\nMemória persistente relevante:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()) if facts else ""

    async def _rag_block(self, user_message: str) -> str:
        if not self.rag_enabled or len(user_message.strip()) < 8:
            return ""
        try:
            results = await asyncio.to_thread(self.memory.search_documents, user_message, self.rag_results)
        except Exception as exc:
            logger.debug("RAG indisponível: %s", exc)
            return ""
        chunks, used = [], 0
        for res in results:
            text = (res.get("text") or "").strip()
            remaining = self.rag_max_chars - used
            if not text or remaining <= 0:
                break
            text = text[:remaining]
            source = (res.get("metadata") or {}).get("filename", "documento")
            chunks.append(f"[{source}] {text}")
            used += len(text)
        return "\n\nExcertos relevantes dos documentos:\n" + "\n".join(chunks) if chunks else ""

    def _history_has_external_content(self) -> bool:
        """True if any tool output already sits in this conversation.

        Tool results are untrusted external content, so once any is present the
        trust-boundary rules must stay in the prompt for the rest of the
        conversation even on a turn that offers no tools.
        """
        return any(m.get("role") == "tool" or m.get("tool_calls") for m in self.conversation)

    async def _build_system_prompt(self, user_message: str, *, with_tools: bool = True,
                                   task: str | None = None) -> str:
        """Assemble only the instructions this turn actually needs.

        Security note: the tool and trust-boundary rules are dropped ONLY when
        no tool is offered and no tool output exists in the history — that is,
        when there is no channel for external content to reach the model. They
        are never dropped to save tokens on a turn that can touch a tool.

        The capability-awareness block is the counterweight to NANO_TOOL_RULES.
        Those rules describe the approval pathway, which on its own taught the
        model that anything systemic can be confirmed into existence -- hence
        "Precisamos de confirmar... Pretende prosseguir?" for a PowerShell
        request Nano has no way to serve. core.capabilities names what does not
        exist, and only on turns that ask for it, so an ordinary "abre o
        Spotify" pays nothing for it.
        """
        parts = [NANO_PERSONA]
        if with_tools or self._history_has_external_content():
            parts.append(NANO_TOOL_RULES)
            parts.append(TRUST_BOUNDARY_SYSTEM_RULES)
        parts.append(capabilities.grounding_block(user_message))
        parts.append(self._facts_block())
        # Small talk never needs a document lookup, and the retrieval itself
        # costs a vector search on every "olá".
        if task != model_selection.TaskClass.SMALL_TALK.value:
            parts.append(await self._rag_block(user_message))
        return "".join(part for part in parts if part)

    def _messages_for_request(self, system_prompt: str, tools: list[dict]) -> list[dict]:
        """Build the outgoing message list, consistent with the tools offered.

        When a turn offers no tools, any ``tool_calls`` still sitting in the
        history would make Groq reject the request with "Tool choice is none,
        but model called a tool". The textual answer for those turns is already
        in the history, so the call/result plumbing is dropped and the
        conversation stays readable.
        """
        history: list[dict] = []
        for message in self.conversation:
            role = message.get("role")
            if not tools:
                if role == "tool":
                    continue
                if message.get("tool_calls"):
                    content = message.get("content")
                    if not content:
                        continue
                    history.append({"role": role, "content": content})
                    continue
            history.append(message)
        return [{"role": "system", "content": system_prompt}, *history]

    def reset_conversation(self):
        self.conversation.clear()

    # ------------------------------------------------------------------ routing

    def _provider_query(self, mode: providers.ProviderMode) -> tuple[str, Any]:
        """The cache key and the producer for this Brain's provider snapshot."""
        local_cfg = (load_config().get("local") or {})
        base_url = str(local_cfg.get("url") or OLLAMA_BASE_URL)
        key = provider_status.cache_key(
            mode, self.groq_fast_model, self.groq_complex_model, self.ollama_model)

        def _produce() -> tuple[dict, dict]:
            return provider_status.describe_pair(
                mode,
                groq_fast_model=self.groq_fast_model,
                groq_complex_model=self.groq_complex_model,
                ollama_model=self.ollama_model,
                ollama_base_url=base_url,
                local_enabled=self.local_enabled,
            )

        return key, _produce

    def _describe_providers(self, mode: providers.ProviderMode) -> tuple[dict, dict]:
        """Blocking provider status. Only for callers allowed to block.

        The chat path must use ``_describe_providers_async``; this variant runs
        the synchronous httpx probes on the calling thread.
        """
        key, produce = self._provider_query(mode)
        return provider_status.CACHE.get_fresh(key, produce)

    async def _describe_providers_async(self, mode: providers.ProviderMode) -> tuple[dict, dict]:
        """Provider status without occupying the calling event loop.

        describe_groq/describe_ollama are synchronous httpx calls. Running them
        inline from ``async def chat`` froze the shared loop for the whole round
        trip -- up to 10 seconds -- once per TTL window, which in an active
        conversation is roughly every third message. The probe itself is
        unchanged; only where it runs is.
        """
        key, produce = self._provider_query(mode)
        return await provider_status.CACHE.get_async(key, produce)

    def _finish_route(self, task: model_selection.TaskClass, tier: model_selection.ModelTier,
                      mode: providers.ProviderMode, groq: dict, ollama: dict) -> dict[str, Any]:
        route = providers.resolve_route(mode, groq, ollama, tier=tier.value)
        route["task"] = task.value
        return route

    def route_for(self, user_message: str) -> dict[str, Any]:
        """The single authoritative routing decision for one message.

        Classification is deterministic and local (no extra model call), then
        ``providers.resolve_route`` picks provider and model tier. Nothing else
        in Nano may decide which provider answers.

        Blocking variant, kept for callers off the event loop. ``chat`` uses
        ``route_for_async``.
        """
        task = model_selection.classify(user_message)
        tier = model_selection.tier_for(task)
        mode = providers.ProviderMode.parse(getattr(self, "provider_mode", "AUTO"))
        groq, ollama = self._describe_providers(mode)
        return self._finish_route(task, tier, mode, groq, ollama)

    async def route_for_async(self, user_message: str) -> dict[str, Any]:
        """route_for without blocking the event loop. Same decision, same order."""
        task = model_selection.classify(user_message)
        tier = model_selection.tier_for(task)
        mode = providers.ProviderMode.parse(getattr(self, "provider_mode", "AUTO"))
        groq, ollama = await self._describe_providers_async(mode)
        return self._finish_route(task, tier, mode, groq, ollama)

    def _legacy_route_model(self, *, task_type: str = "chat", requires_tools: bool = False, requires_vision: bool = False, requires_coding: bool = False, requires_reasoning: bool = False, privacy_level: str | PrivacyLevel = PrivacyLevel.NORMAL, latency_preference: str = "balanced", local_only: bool = False):
        if not self.model_router:
            return {"provider": "ollama", "model": self.ollama_model, "reason": "default fallback"}
        request = ModelRequest(
            task_type=task_type,
            requires_tools=requires_tools,
            requires_vision=requires_vision,
            requires_coding=requires_coding,
            requires_reasoning=requires_reasoning,
            privacy_level=privacy_level,
            latency_preference=latency_preference,
            local_only=local_only,
            context_size=self.local_context,
        )
        selection = self.model_router.select(request)
        if selection.get("model"):
            return selection
        return {"provider": "ollama", "model": self.ollama_model, "reason": "fallback to local default"}

    async def _groq_round(self, model: str, messages: list[dict], tools: list[dict],
                          collector: dict, max_tokens: int = 4096) -> AsyncIterator[str]:
        """One streamed Groq turn. Yields text as it arrives, collects the rest.

        Tool calls arrive fragmented across deltas (the name in one chunk, the
        arguments a few characters at a time), so they are reassembled by index
        into ``collector['tool_calls']``.
        """
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.65,
            "max_tokens": max_tokens,
            "stream": True,
        }
        # Only send the tool keys when there are tools. Passing
        # tool_choice=None serialises to JSON null, which Groq rejects with
        # "Only allowed string values for 'tool_choice' are [none, auto,
        # required]" -- a 400 on every single message.
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        _simulated_groq_failure()

        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._as_rate_limit(exc) from exc

        acc: dict[int, dict[str, str]] = {}
        try:
            async for chunk in response:
                choice = chunk.choices[0] if getattr(chunk, "choices", None) else None
                delta = getattr(choice, "delta", None) if choice else None
                if delta is not None:
                    if getattr(delta, "content", None):
                        if collector.get("first_token_at") is None:
                            collector["first_token_at"] = time.monotonic()
                        yield delta.content
                    for tc in (getattr(delta, "tool_calls", None) or []):
                        slot = acc.setdefault(int(getattr(tc, "index", 0) or 0),
                                              {"id": "", "name": "", "args": ""})
                        if getattr(tc, "id", None):
                            slot["id"] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                slot["name"] = fn.name
                            if getattr(fn, "arguments", None):
                                slot["args"] += fn.arguments
                usage = getattr(getattr(chunk, "x_groq", None), "usage", None)
                if usage is not None:
                    collector["usage"] = usage
        except Exception as exc:
            raise self._as_rate_limit(exc) from exc

        collector["tool_calls"] = [acc[key] for key in sorted(acc) if acc[key].get("name")]

    @staticmethod
    def _as_rate_limit(exc: Exception) -> Exception:
        """Convert a Groq 429 into RateLimited, carrying the real headers."""
        status = getattr(exc, "status_code", None)
        response = getattr(exc, "response", None)
        if status is None and response is not None:
            status = getattr(response, "status_code", None)
        if status == 429:
            headers = getattr(response, "headers", {}) or {}
            return RateLimited(providers.parse_rate_limit(headers))
        return exc

    async def chat(self, user_message: str, stream: bool = True) -> AsyncIterator[str]:
        """Answer one message, streaming real provider tokens as they arrive."""
        started = time.monotonic()
        # One ledger per logical user turn. Provider failover happens INSIDE a
        # turn, so this must not be cleared between model steps.
        self._turn_tool_results = {}
        self.conversation.append({"role": "user", "content": user_message})
        self._trim_conversation()

        route = await self.route_for_async(user_message)
        task = route.get("task") or model_selection.TaskClass.SMALL_TALK.value
        mode = route.get("mode") or "AUTO"

        # Only the smallest plausible tool subset is offered to the model. This
        # is a cost/noise decision, never a security one: whatever the model
        # asks for still goes through POLICY -> PERMISSION -> EXECUTION.
        all_tools = get_all_tools()
        tools = model_selection.select_tools(
            user_message, all_tools, task=model_selection.TaskClass(task))
        system_prompt = await self._build_system_prompt(
            user_message, with_tools=bool(tools), task=task)

        max_tokens = model_selection.max_tokens_for(model_selection.TaskClass(task))

        self.last_metadata = {
            "task": task, "tier": route.get("tier"), "mode": mode,
            "provider": route.get("provider"), "model": route.get("model"),
            "tools_offered": len(tools), "tools_available": len(all_tools),
            "max_tokens": max_tokens, "fallback_used": False,
        }

        if route.get("provider") == "ollama":
            self.last_provider_used = "ollama"
            self.last_metadata["fallback_used"] = bool(route.get("fallback"))
            async for token in self._ollama_fallback(
                    user_message, str(route.get("reason") or "Modo Local"), system_prompt):
                yield token
            return

        if not self.groq_enabled or route.get("provider") != "groq":
            if mode == "CLOUD":
                # Asked for cloud-only: say so instead of quietly answering
                # from somewhere the user did not choose.
                self.last_provider_used = None
                yield ("**Modo Cloud activo mas o Groq não está disponível.** "
                       f"{route.get('reason') or ''} "
                       "Verifica a chave em Definições → Inteligência Artificial, "
                       "ou muda para Automático para usar o modelo local.")
                return
            self.last_provider_used = "ollama"
            self.last_metadata["fallback_used"] = True
            async for token in self._ollama_fallback(user_message, "Groq indisponível", system_prompt):
                yield token
            return

        # THE BREAKER, CHECKED BEFORE PAYING FOR A ROUND TRIP.
        #
        # In AUTO, a Groq we already know is rate-limited is not worth asking:
        # the request costs latency, fails, and spends tokens that have not
        # come back yet. CLOUD deliberately still tries -- the user chose
        # cloud-only, and a stale cooldown must not turn into a refusal to work.
        if mode == "AUTO" and provider_failures.GROQ_COOLDOWN.is_cooling_down():
            remaining = provider_failures.GROQ_COOLDOWN.remaining_seconds()
            logger.info("Groq skipped: cooling down for another %.0fs", remaining)
            self.last_provider_used = "ollama"
            self.last_metadata["fallback_used"] = True
            self.last_metadata["fallback_reason"] = "groq_cooldown"
            self.last_metadata["groq_cooldown_seconds"] = round(remaining, 1)
            async for token in self._ollama_fallback(
                    user_message, "Groq temporariamente limitada", system_prompt):
                yield token
            return

        model = str(route.get("model") or self.groq_fast_model)
        self.last_metadata["model"] = model

        for round_num in range(MAX_TOOL_ROUNDS):
            collector: dict[str, Any] = {"first_token_at": None, "tool_calls": [], "usage": None}
            text_parts: list[str] = []
            try:
                async for piece in self._groq_round(
                        model, self._messages_for_request(system_prompt, tools),
                        tools, collector, max_tokens):
                    text_parts.append(piece)
                    yield piece
            except Exception as exc:
                # ONE classification for every provider failure. What follows
                # depends on the TYPE, not on where the exception was raised.
                failure = provider_failures.classify(exc)
                cooldown = provider_failures.GROQ_COOLDOWN.note_failure(failure)
                logger.warning("Groq %s (round %d, cooldown %.0fs): %s",
                               failure.type.value, round_num, cooldown, failure.message[:200])
                # Rate-limit numbers live HERE, in structured diagnostics --
                # never concatenated into the answer. `_ratelimit_:{...}` used
                # to be yielded as a token and was read aloud on voice turns.
                self.last_metadata["error"] = failure.type.value
                self.last_metadata["provider_failure"] = failure.as_dict()
                if failure.rate_limit:
                    self.last_metadata["rate_limited"] = failure.rate_limit
                if cooldown:
                    self.last_metadata["groq_cooldown_seconds"] = round(cooldown, 1)

                async for token in self._handle_provider_failure(
                        failure, mode=mode, user_message=user_message,
                        system_prompt=system_prompt,
                        emitted_text=bool("".join(text_parts).strip()),
                        did_work=self._turn_has_tool_results()):
                    yield token
                return

            self.last_provider_used = "groq"
            provider_failures.GROQ_COOLDOWN.note_success()
            self._record_metrics(started, collector)

            calls = collector.get("tool_calls") or []
            text = "".join(text_parts)

            if not calls:
                self.conversation.append({"role": "assistant", "content": text})
                return

            self.conversation.append({
                "role": "assistant",
                "content": text or None,
                "tool_calls": [
                    {"id": c["id"] or f"call_{i}", "type": "function",
                     "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                    for i, c in enumerate(calls)
                ],
            })
            yield f"_thinking_:⚙️ {', '.join(c['name'] for c in calls)}..."

            shaped = [{"function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                      for c in calls]
            results = await asyncio.gather(*(self._run_tool(c) for c in shaped),
                                           return_exceptions=True)
            for call, result in zip(calls, results):
                if isinstance(result, Exception):
                    logger.error("Tool %s falhou", call["name"], exc_info=result)
                    result = {"ok": False, "error": "tool_failed"}
                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": call["id"] or f"call_{calls.index(call)}",
                    "content": self._tool_result_for_model(call["name"], result),
                })

        yield "Atingi o limite de operações encadeadas. Podes reformular o pedido?"

    def _turn_has_tool_results(self) -> bool:
        """True when this turn already executed a tool and recorded its result.

        That changes everything about how a provider failure must be handled:
        the action HAPPENED. Discarding the turn would throw away a real effect
        on the machine, and replaying the user message would invite a second
        one.
        """
        for entry in reversed(self.conversation):
            role = entry.get("role")
            if role == "tool":
                return True
            if role == "user":
                return False
        return False

    async def _handle_provider_failure(self, failure, *, mode: str, user_message: str,
                                       system_prompt: str, emitted_text: bool,
                                       did_work: bool):
        """Decide what a classified Groq failure means for this turn.

        Four questions, in order, and each one can end the turn:

        1. Did the user ask for cloud-only? CLOUD never falls back. That is the
           contract, and a clean error is the correct output.
        2. Is this failure one we must not hide? A rejected key or our own
           malformed request must reach the user; answering from Ollama would
           bury a problem only the user can fix.
        3. Has visible text already been streamed? Then a second, complete
           answer would appear underneath a half-finished one. See below.
        4. Otherwise: continue the SAME turn on the local model.
        """
        self.last_provider_used = None

        if mode == "CLOUD":
            self.last_metadata["fallback_used"] = False
            self.last_metadata["fallback_reason"] = "cloud_mode_no_fallback"
            if not did_work:
                self._rollback_turn()
            yield (f"**{failure.user_message()}** "
                   "Estás em modo Cloud, por isso o Nano não recorre ao modelo local. "
                   "Muda para Automático em Definições se quiseres o fallback local.")
            return

        if not failure.may_fall_back:
            self.last_metadata["fallback_used"] = False
            self.last_metadata["fallback_reason"] = f"not_eligible:{failure.type.value}"
            if not did_work:
                self._rollback_turn()
            yield f"**{failure.user_message()}**"
            return

        if emitted_text:
            # THE STREAMING POLICY, CHOSEN DELIBERATELY.
            #
            # Groq already sent visible words to the screen. Running the local
            # model now would append a second, complete answer under a
            # half-finished one -- two assistant voices in one bubble. The
            # alternative, buffering every response until it completes, would
            # cost the streaming UX on every healthy turn to improve a rare
            # one. So a partial answer stays partial and says so.
            self.last_metadata["fallback_used"] = False
            self.last_metadata["fallback_reason"] = "partial_stream_not_replaced"
            self.last_metadata["partial_answer"] = True
            self.conversation.append({"role": "assistant", "content": "", "_partial": True})
            self._rollback_turn()
            yield "\n\n_(A resposta foi interrompida. Pede outra vez para continuar.)_"
            return

        self.last_provider_used = "ollama"
        self.last_metadata["fallback_used"] = True
        self.last_metadata["fallback_reason"] = failure.type.value
        # continue_turn: when a tool has already run, the conversation holds the
        # assistant tool_calls entry AND the tool results. The local model must
        # continue from those, not be handed the original request again.
        async for token in self._ollama_fallback(
                user_message, "Groq indisponível", system_prompt,
                continue_turn=did_work):
            yield token

    def _rollback_turn(self) -> None:
        """Undo a turn that produced no answer, back to the last clean state.

        A failed turn used to leave the user's message in the history with no
        assistant reply. The next successful call then answered every backlogged
        message at once -- asking "qual é a capital de Portugal?" produced an
        answer about RAM and small talk, because two rate-limited messages were
        still queued. Removing the unanswered turn keeps the conversation honest.
        """
        while self.conversation and self.conversation[-1].get("role") in ("tool", "assistant"):
            self.conversation.pop()
        if self.conversation and self.conversation[-1].get("role") == "user":
            self.conversation.pop()

    def _record_metrics(self, started: float, collector: dict) -> None:
        """Store safe latency/token metrics for the diagnostics panel."""
        first = collector.get("first_token_at")
        if first is not None and self.last_metadata.get("time_to_first_token_ms") is None:
            self.last_metadata["time_to_first_token_ms"] = int((first - started) * 1000)
        self.last_metadata["total_latency_ms"] = int((time.monotonic() - started) * 1000)
        usage = collector.get("usage")
        if usage is not None:
            self.last_metadata["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
            self.last_metadata["completion_tokens"] = getattr(usage, "completion_tokens", None)

    def _build_default_executor(self, permission_manager: Any | None):
        """Create an execution authority when the caller did not supply one."""
        if permission_manager is None:
            return None
        try:
            from core.tool_execution import ToolExecutor

            executor = ToolExecutor(permission_manager=permission_manager)
            executor.register_plugin_tools()
            return executor
        except Exception:
            logger.exception("Não foi possível construir a autoridade de execução")
            return None

    def _sync_plugin_tools(self, name: str) -> None:
        """Keep the executor registry aligned with plugins loaded after start."""
        if self.tool_executor is None or name in self.tool_executor.registry:
            return
        try:
            self.tool_executor.register_plugin_tools()
        except Exception:
            logger.exception("Falha ao sincronizar ferramentas de plugin")

    #: Arguments that name a place on disk. Windows paths are case-insensitive
    #: and tolerate mixed separators, so two spellings of one file must produce
    #: one ledger key -- otherwise a provider failover could recycle the same
    #: file twice by writing it differently the second time.
    _PATH_ARGUMENTS = frozenset({"path", "source", "destination", "dest", "src",
                                 "cwd", "target_path", "roots"})

    #: Arguments that are enums the handlers lower-case anyway. "Left" and
    #: "left" are one call, and the ledger has to agree.
    _ENUM_ARGUMENTS = frozenset({"key", "hotkey", "action", "section", "position",
                                 "state", "direction", "mode", "engine", "app"})

    @classmethod
    def _canonical_arguments(cls, args: dict) -> dict:
        """Normalise arguments so one EFFECT has exactly one ledger identity.

        `(tool_name, arguments)` was enough for V1, whose arguments were mostly
        window ids and integers. V2 added calls whose arguments can be spelled
        several ways for the same effect -- a path written with forward slashes
        and the same path written with backslashes and a different case are the
        same file, `"Left"` and `"left"` are the same snap -- and the
        duplicate-execution ledger keys on this string. Two
        spellings would be two keys, which is precisely the hole the ledger
        exists to close.

        Deliberately shallow and boring: strip, case-fold the enums, normalise
        the paths, drop the nulls. It must never merge two calls that would do
        different things, so nothing here rewrites a value's meaning.
        """
        import os

        canonical: dict = {}
        for key, value in args.items():
            if value is None:
                # An absent argument and an explicit null reach the handler
                # identically, so they must not be two different calls.
                continue
            if isinstance(value, str):
                value = value.strip()
                if key in cls._ENUM_ARGUMENTS:
                    value = value.casefold()
                elif key in cls._PATH_ARGUMENTS and value:
                    try:
                        value = os.path.normcase(os.path.normpath(value))
                    except (TypeError, ValueError):
                        pass
            canonical[key] = value
        return canonical

    @classmethod
    def _call_fingerprint(cls, name: str, args: dict) -> str:
        """A stable identity for "this exact tool call, with these arguments"."""
        return json.dumps({"n": str(name).strip(), "a": cls._canonical_arguments(args)},
                          sort_keys=True, ensure_ascii=False)

    async def _run_tool(self, tool_call) -> dict:
        if isinstance(tool_call, dict):
            fn = tool_call.get("function") or {}
            name = str(fn.get("name", ""))
            raw_args = fn.get("arguments", {})
        else:
            name = str(getattr(tool_call.function, "name", ""))
            raw_args = getattr(tool_call.function, "arguments", "{}")

        if isinstance(raw_args, dict):
            args = raw_args
        elif isinstance(raw_args, str):
            try:
                args = json.loads(raw_args or "{}")
            except json.JSONDecodeError:
                return {"ok": False, "error": "invalid_tool_arguments"}
        else:
            return {"ok": False, "error": "invalid_tool_arguments"}

        if not isinstance(args, dict):
            return {"ok": False, "error": "invalid_tool_arguments"}

        if self.tool_executor is None:
            logger.error("Sem autoridade de execução; '%s' recusada", name)
            return {"ok": False, "cancelled": True, "message": "Execução indisponível: falta a autoridade central."}

        self._sync_plugin_tools(name)

        # A capability Nano does not have is refused HERE, before guardrails,
        # before the executor and therefore before any confirmation dialog.
        # Order is the whole point: asking "Confirmas?" for a tool that cannot
        # exist tells the person their Yes is what is missing, when in fact
        # nothing they can do would make the call possible. The model gets the
        # explanation back as the tool result, so it relays the truth instead
        # of inventing an approval flow. See core/capabilities.py.
        unsupported = capabilities.for_tool(name)
        if unsupported is not None:
            logger.warning("Tool %s refused: %s is not a Nano capability", name, unsupported.id)
            return capabilities.refusal(unsupported, tool=name)

        # Guardrails stay as the human-readable layer in front of the policy
        # pipeline; the executor below re-checks everything independently.
        if self.guardrails.requires_confirmation(name, args) and not await self.guardrails.ask_confirmation(name, args):
            return {"ok": False, "cancelled": True, "message": "Operação cancelada pelo utilizador."}

        # DUPLICATE-EXECUTION PROTECTION, AND THE REASON IT LIVES HERE.
        #
        # A provider failover mid-turn hands the local model a conversation in
        # which a tool has ALREADY run. A competent model continues from the
        # recorded result -- but "competent" is not a safety property. If it
        # re-issues the identical call, replaying it would open a second
        # Calculator, take a second screenshot, or close a second window.
        #
        # So the first execution of a given (name, arguments) pair in a turn is
        # remembered, and an identical repeat returns THAT RESULT instead of
        # touching Windows again. The model still sees a truthful answer; the
        # machine is only acted on once. The ledger is per turn, so a genuine
        # second request in a later turn is unaffected.
        fingerprint = self._call_fingerprint(name, args)
        cached = self._turn_tool_results.get(fingerprint)
        if cached is not None:
            logger.info("Tool %s replayed from this turn's ledger; not executed again", name)
            replay = dict(cached)
            metadata = dict(replay.get("metadata") or {})
            metadata["replayed"] = True
            replay["metadata"] = metadata
            return replay

        try:
            result = await self.tool_executor.execute_tool_async(name, args)
        except Exception:
            logger.exception("Tool %s lançou uma exceção", name)
            return {"ok": False, "error": "tool_exception"}

        # Only a genuine execution is remembered. A refusal or a validation
        # error must be retryable -- the user may approve on a second ask.
        if result.get("success"):
            self._turn_tool_results[fingerprint] = result

        if not result.get("success") and result.get("status") == "permission_denied":
            return {
                "ok": False,
                "cancelled": True,
                "status": "permission_denied",
                "message": result.get("error") or "Operação bloqueada pela política de segurança.",
            }
        return result

    @staticmethod
    def _tool_result_for_model(name: str, result: dict) -> str:
        """Serialise a tool result, fencing anything that came from outside.

        External content is data. Fencing it keeps a fetched page from reading
        as an instruction once it lands in the conversation.
        """
        metadata = result.get("metadata") or {}
        if metadata.get("trust") != TrustLevel.UNTRUSTED_EXTERNAL.value:
            return json.dumps(result, ensure_ascii=False)

        payload = {
            "success": result.get("success"),
            "status": result.get("status"),
            "error": result.get("error"),
            "trust": TrustLevel.UNTRUSTED_EXTERNAL.value,
            "injection_findings": metadata.get("injection_findings") or [],
        }
        body = wrap_untrusted(Brain._extract_external_text(result), source=name)
        return json.dumps(payload, ensure_ascii=False) + "\n" + body

    @staticmethod
    def _extract_external_text(result: dict) -> str:
        output = result.get("output")
        if isinstance(output, dict):
            for key in ("content", "text", "snippet", "stdout"):
                value = output.get(key)
                if isinstance(value, str) and value.strip():
                    return value
            return json.dumps(output, ensure_ascii=False)
        return str(output or "")

    @staticmethod
    def _ollama_tool_calls(calls) -> list[dict]:
        """Re-shape OpenAI-format tool calls for Ollama's /api/chat.

        THE TWO PROVIDERS DISAGREE ABOUT ONE FIELD. Groq speaks the OpenAI wire
        format, where `function.arguments` is a JSON **string**; Ollama decodes
        that field into a map and rejects a string outright:

            HTTP 400 {"error":"Value looks like object, but can't find
                      closing '}' symbol"}

        Nano's history holds the Groq shape, because that is what has to go
        back to Groq. So the conversion happens HERE, on the way out to the
        local model, and the stored history is left alone.

        The consequence of not doing this was not a visible error. The request
        failed, the caller fell into the no-tools branch, and the local model
        answered without any tools for the rest of the conversation -- so PC
        Control simply stopped working after the first Groq tool call, and the
        model filled the gap with plausible-sounding advice.

        A call whose arguments cannot be parsed is DROPPED rather than sent as
        `{}`: an empty argument map invites the local model to re-issue the call
        with arguments of its own invention, and that is a second, different
        call the per-turn ledger would not recognise as a repeat.
        """
        shaped: list[dict] = []
        for call in calls or []:
            function = (call or {}).get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict) or not function.get("name"):
                continue
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments or "{}")
                except (json.JSONDecodeError, ValueError):
                    logger.warning(
                        "Dropping a tool call for the local model: its arguments "
                        "are not valid JSON.")
                    continue
            if not isinstance(arguments, dict):
                continue
            shaped.append({"function": {"name": str(function["name"]),
                                        "arguments": arguments}})
        return shaped

    async def _ollama_fallback(self, message: str, reason: str = "",
                               system_prompt: str | None = None,
                               *, continue_turn: bool = False) -> AsyncIterator[str]:
        """Answer on the local model.

        ``continue_turn=True`` means this is the SECOND half of a turn Groq
        started: a tool has already executed and its result is in the
        conversation. The user message must NOT be appended again -- doing so
        produced a duplicate user entry and invited the local model to re-issue
        a tool call that had already succeeded.
        """
        if not self.local_enabled:
            yield ("Não consegui obter resposta da cloud nem do modelo local. "
                   "O modelo local não está ativo.")
            return
        # The configured local model is an instruction, not a hint: scoring the
        # installed models here could silently substitute a different one.
        selected_model = self.ollama_model
        yield f"_thinking_:🧠 {reason + ' — ' if reason else ''}a usar {selected_model} local..."
        self.last_metadata["local_model"] = selected_model

        # THE ANSWER IS COMING FROM HERE, SO SAY SO.
        #
        # `provider` and `model` were left naming the CLOUD route that had just
        # failed, so the "Detalhes técnicos" panel reported "groq (fallback)"
        # and "openai/gpt-oss-20b" for a reply that qwen3:8b had written. The
        # fallback was visible, but the thing it fell back TO was not, and the
        # two named fields were both wrong. What was ATTEMPTED is kept under its
        # own keys rather than discarded -- the diagnostics need both halves.
        attempted_provider = self.last_metadata.get("provider")
        attempted_model = self.last_metadata.get("model")
        if attempted_provider and attempted_provider != "ollama":
            self.last_metadata["attempted_provider"] = attempted_provider
            self.last_metadata["attempted_model"] = attempted_model
        self.last_metadata["provider"] = "ollama"
        self.last_metadata["model"] = selected_model
        # Local models are far weaker at ignoring irrelevant tools, and the
        # local context window is small, so the same scoped subset applies.
        tools = model_selection.select_tools(message, get_all_tools())
        sys_p = system_prompt or (SYSTEM_PROMPT + self._facts_block())

        local_messages = [{"role": "system", "content": sys_p}]
        for m in self.conversation[-12:]:
            r = m.get("role")
            if r in ("user", "assistant", "tool"):
                msg_obj = {"role": r, "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    # Converted, not copied: see _ollama_tool_calls. Copying the
                    # Groq shape here is what made every local turn after a
                    # cloud tool call fail with HTTP 400 and silently lose tools.
                    shaped = self._ollama_tool_calls(m["tool_calls"])
                    if shaped:
                        msg_obj["tool_calls"] = shaped
                local_messages.append(msg_obj)

        if not continue_turn and (not local_messages
                                  or local_messages[-1].get("content") != message):
            local_messages.append({"role": "user", "content": message})

        client_timeout = httpx.Timeout(60.0, connect=10.0)
        async with httpx.AsyncClient(timeout=client_timeout) as client:
            for round_num in range(MAX_TOOL_ROUNDS):
                payload: dict = {
                    "model": selected_model,
                    "messages": local_messages,
                    "stream": False,
                    "options": {"temperature": 0.65, "num_ctx": self.local_context},
                }
                if tools:
                    payload["tools"] = tools

                try:
                    resp = await client.post(self.ollama_url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    msg = data.get("message") or {}
                    tool_calls = msg.get("tool_calls")

                    if not tool_calls:
                        text = msg.get("content") or ""
                        if _looks_like_a_leaked_tool_call(text):
                            # Never shown, never executed. See the helper.
                            logger.warning(
                                "Local model leaked a tool call as content (%d chars); "
                                "replaced with a clean message.", len(text))
                            self.last_metadata["local_malformed_tool_call"] = True
                            text = ("Não consegui completar esse pedido com o modelo local. "
                                    "Tenta outra vez daqui a pouco.")
                        self.conversation.append({"role": "assistant", "content": text})
                        if text:
                            async for chunk in _stream_text_chunks(text):
                                yield chunk
                        return

                    tool_names = [tc.get("function", {}).get("name", "tool") for tc in tool_calls]
                    yield f"_thinking_:⚙️ {', '.join(tool_names)}..."
                    local_messages.append(msg)
                    self.conversation.append(msg)

                    results = await asyncio.gather(*(self._run_tool(tc) for tc in tool_calls), return_exceptions=True)
                    for tc, result in zip(tool_calls, results):
                        if isinstance(result, Exception):
                            logger.error("Tool local falhou", exc_info=result)
                            result = {"ok": False, "error": "tool_failed"}
                        tool_name = (tc.get("function") or {}).get("name", "tool") if isinstance(tc, dict) else "tool"
                        tool_msg = {"role": "tool", "content": self._tool_result_for_model(tool_name, result)}
                        local_messages.append(tool_msg)
                        self.conversation.append(tool_msg)

                except Exception as exc:
                    logger.warning("Ollama com tools falhou (round %d): %s. A tentar stream sem tools.", round_num, exc)
                    stream_payload = {
                        "model": selected_model,
                        "messages": [{"role": "system", "content": sys_p}, {"role": "user", "content": message}],
                        "stream": True,
                        "options": {"temperature": 0.65, "num_ctx": self.local_context}
                    }
                    # Whatever this branch produces MUST end up in the history,
                    # exactly like every other answer. It used to stream tokens
                    # and return without appending anything, which left the
                    # user's message in self.conversation with no assistant
                    # reply -- the backlog state _rollback_turn exists to
                    # prevent, reintroduced on the local path. The next
                    # successful turn then answered the whole backlog at once.
                    streamed: list[str] = []
                    try:
                        async with client.stream("POST", self.ollama_url, json=stream_payload) as stream_resp:
                            stream_resp.raise_for_status()
                            async for line in stream_resp.aiter_lines():
                                if not line:
                                    continue
                                s_data = json.loads(line)
                                token = (s_data.get("message") or {}).get("content") or ""
                                if token:
                                    streamed.append(token)
                                    yield token
                                if s_data.get("done"):
                                    break
                    except Exception as err2:
                        logger.error("Modelo local indisponível: %s", err2)
                        # No answer was produced, so the turn is undone rather
                        # than left half-recorded.
                        self._rollback_turn()
                        yield "O modelo local ainda não está disponível. Abre as definições do Nano para verificar o estado local."
                        return

                    text = "".join(streamed).strip()
                    if text:
                        self.conversation.append({"role": "assistant", "content": text})
                    else:
                        self._rollback_turn()
                    return
            yield "Atingi o limite de operações encadeadas offline. Podes reformular o pedido?"