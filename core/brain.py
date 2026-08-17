"""Nano Assistant — motor de decisão, streaming e tool-calling.

Este módulo contém a classe ``Brain`` que gere a conversação entre o utilizador e o modelo
de linguagem, suportando streaming em tempo real, tool-calling autónomo e fallback transparente
para modelos locais (Ollama).
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import AsyncIterator, Any
import httpx
from groq import AsyncGroq
from core.config import load_config
from core.local_runtime import choose_model
from core.plugin_loader import get_all_tools, execute_tool
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine
from core.errors import ToolExecutionError, GuardrailError
from core.model_router import ModelRequest, ModelRouter, PrivacyLevel, TaskType

logger = logging.getLogger("nano.brain")
GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """És o Nano Assistant (ou simplesmente Nano), um assistente virtual executivo, rápido e inteligente.
O utilizador deve ser tratado com simpatia, empatia, precisão e foco.

Regras fundamentais:
- Usa ferramentas sempre que precisares de interagir com o sistema operativo, ficheiros, web ou dispositivos.
- Ações destrutivas, alterações no sistema ou operações externas sensíveis exigem confirmação através dos guardrails.
- Nunca inventes resultados de ferramentas nem fales de segredos de sistema.
- Responde em Português de Portugal com clareza, formatação rica e estilo direto.
- Quando aprenderes preferências ou factos duradouros sobre o utilizador, usa 'remember_fact'."""

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

class Brain:
    def __init__(self, api_key: str, guardrails: GuardrailsEngine, memory: MemoryEngine, config: dict | None = None, permission_manager: Any | None = None):
        cfg = config if config is not None else load_config()
        mem_cfg, local_cfg = cfg.get("memory") or {}, cfg.get("local") or {}
        self.groq_enabled = bool(api_key.strip())
        self.client = AsyncGroq(api_key=api_key) if self.groq_enabled else None
        self.guardrails, self.memory = guardrails, memory
        self.permission_manager = permission_manager
        self.conversation: list[dict] = []
        self.groq_model = str(cfg.get("groq_model") or GROQ_MODEL)
        self.local_enabled = bool(local_cfg.get("enabled", cfg.get("ollama_enabled", True)))
        self.local_profile = choose_model(cfg)
        self.model_router = ModelRouter(cfg, cloud_api_key=api_key)
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
        self.facts_enabled = bool(mem_cfg.get("facts_enabled", True))
        self.rag_enabled = bool(mem_cfg.get("rag_enabled", True))
        self.rag_results = max(1, int(mem_cfg.get("rag_results", 3)))
        self.rag_max_chars = max(200, int(mem_cfg.get("rag_max_chars", 1500)))
        logger.info(
            "Nano Brain: Cloud=%s model=%s | Local=%s model=%s (%s, %.1f GB RAM)",
            self.groq_enabled, self.groq_model, self.local_enabled, self.ollama_model,
            self.local_profile.reason, self.local_profile.ram_gb
        )

    def load_history(self) -> int:
        try:
            self.conversation = list(self.memory.get_context_window(self.history_messages, self.max_history_chars))
        except Exception as exc:
            logger.error("Falha ao carregar histórico: %s", exc)
            return 0
        return len(self.conversation)

    def _trim_conversation(self):
        budget = self.max_history_chars * 2
        total = sum(len(str(m.get("content") or "")) for m in self.conversation)
        while total > budget and len(self.conversation) > 2:
            removed = self.conversation.pop(0)
            total -= len(str(removed.get("content") or ""))
            while self.conversation and self.conversation[0].get("role") == "tool":
                orphan = self.conversation.pop(0)
                total -= len(str(orphan.get("content") or ""))

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

    async def _build_system_prompt(self, user_message: str) -> str:
        return SYSTEM_PROMPT + self._facts_block() + await self._rag_block(user_message)

    def reset_conversation(self):
        self.conversation.clear()

    def _route_model(self, *, task_type: str = "chat", requires_tools: bool = False, requires_vision: bool = False, requires_coding: bool = False, requires_reasoning: bool = False, privacy_level: str | PrivacyLevel = PrivacyLevel.NORMAL, latency_preference: str = "balanced", local_only: bool = False):
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

    async def chat(self, user_message: str, stream: bool = True) -> AsyncIterator[str]:
        self.conversation.append({"role": "user", "content": user_message})
        self._trim_conversation()
        tools, system_prompt = get_all_tools(), await self._build_system_prompt(user_message)
        route = self._route_model(task_type=TaskType.CHAT, requires_tools=bool(tools), privacy_level=PrivacyLevel.NORMAL, latency_preference="balanced")
        selected_model = route.get("model") or self.groq_model

        if not self.groq_enabled:
            async for token in self._ollama_fallback(user_message, f"Groq não configurado — route {route.get('provider')}:{selected_model}", system_prompt):
                yield token
            return

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.chat.completions.create(
                    model=selected_model if route.get("provider") == "cloud" else self.groq_model,
                    messages=[{"role": "system", "content": system_prompt}, *self.conversation],
                    tools=tools or None,
                    tool_choice="auto" if tools else None,
                    temperature=0.65,
                    max_tokens=4096
                )
            except Exception as exc:
                logger.warning("Groq indisponível (round %d): %s", round_num, exc)
                async for token in self._ollama_fallback(user_message, f"Groq indisponível — route {route.get('provider')}:{selected_model}", system_prompt):
                    yield token
                return

            message = response.choices[0].message
            if not message.tool_calls:
                text = message.content or ""
                self.conversation.append({"role": "assistant", "content": text})
                if stream and text:
                    async for chunk in _stream_text_chunks(text):
                        yield chunk
                else:
                    yield text
                return

            self.conversation.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments}
                    }
                    for tc in message.tool_calls
                ]
            })
            yield f"_thinking_:⚙️ {', '.join(tc.function.name for tc in message.tool_calls)}..."
            results = await asyncio.gather(*(self._run_tool(tc) for tc in message.tool_calls), return_exceptions=True)
            for tc, result in zip(message.tool_calls, results):
                if isinstance(result, Exception):
                    logger.error("Tool %s falhou", tc.function.name, exc_info=result)
                    result = {"ok": False, "error": "tool_failed"}
                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False)
                })

        yield "Atingi o limite de operações encadeadas. Podes reformular o pedido?"

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

        capability = name
        if self.permission_manager is not None:
            capability = self.permission_manager.resolve_tool_capability(name, args)
            if self.permission_manager.is_emergency_stopped():
                return {"ok": False, "cancelled": True, "message": "Emergency stop active. Execution blocked."}
            if self.permission_manager.get_decision_for_action(capability, args) == "deny":
                return {"ok": False, "cancelled": True, "message": "Operação bloqueada pela política de segurança."}
            decision = self.permission_manager.evaluate(capability, args)
            if decision.requires_confirmation and not self.permission_manager.ask_for_confirmation(capability, args):
                return {"ok": False, "cancelled": True, "message": "Operação cancelada pelo utilizador."}

        if self.guardrails.requires_confirmation(name, args) and not await self.guardrails.ask_confirmation(name, args):
            return {"ok": False, "cancelled": True, "message": "Operação cancelada pelo utilizador."}

        try:
            return await execute_tool(name, args)
        except Exception:
            logger.exception("Tool %s lançou uma exceção", name)
            return {"ok": False, "error": "tool_exception"}

    async def _ollama_fallback(self, message: str, reason: str = "", system_prompt: str | None = None) -> AsyncIterator[str]:
        if not self.local_enabled:
            yield "O Nano não tem um modelo local ativo e o serviço online não está disponível."
            return
        route = self._route_model(
            task_type=TaskType.GENERAL_REASONING,
            requires_tools=bool(get_all_tools()),
            privacy_level=PrivacyLevel.NORMAL,
            latency_preference="balanced",
            local_only=True,
        )
        selected_model = route.get("model") or self.ollama_model
        yield f"_thinking_:🧠 {reason + ' — ' if reason else ''}a usar {selected_model} local..."
        tools = get_all_tools()
        sys_p = system_prompt or (SYSTEM_PROMPT + self._facts_block())

        local_messages = [{"role": "system", "content": sys_p}]
        for m in self.conversation[-12:]:
            r = m.get("role")
            if r in ("user", "assistant", "tool"):
                msg_obj = {"role": r, "content": m.get("content") or ""}
                if m.get("tool_calls"):
                    msg_obj["tool_calls"] = m["tool_calls"]
                local_messages.append(msg_obj)

        if not local_messages or local_messages[-1].get("content") != message:
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
                        tool_msg = {"role": "tool", "content": json.dumps(result, ensure_ascii=False)}
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
                    try:
                        async with client.stream("POST", self.ollama_url, json=stream_payload) as stream_resp:
                            stream_resp.raise_for_status()
                            async for line in stream_resp.aiter_lines():
                                if not line:
                                    continue
                                s_data = json.loads(line)
                                token = (s_data.get("message") or {}).get("content") or ""
                                if token:
                                    yield token
                                if s_data.get("done"):
                                    return
                    except Exception as err2:
                        logger.error("Modelo local indisponível: %s", err2)
                        yield "O modelo local ainda não está disponível. Abre as definições do Nano para verificar o estado local."
                    return
            yield "Atingi o limite de operações encadeadas offline. Podes reformular o pedido?"