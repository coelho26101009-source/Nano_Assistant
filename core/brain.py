"""
H.E.L.I.O.S. Brain — Motor de Decisão
Groq (Llama-3.3-70b) para velocidade + Ollama fallback offline.
Loop de function-calling multi-turno com streaming real.
"""

import asyncio
import json
import logging
from typing import AsyncIterator

import httpx
from groq import AsyncGroq

from core.config import load_config
from core.plugin_loader import get_all_tools, execute_tool
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine

logger = logging.getLogger("helios.brain")

GROQ_MODEL      = "llama-3.1-8b-instant"   # mais rápido, sem rate limit
OLLAMA_MODEL    = "qwen2.5:3b"
OLLAMA_BASE_URL = "http://localhost:11434"
MAX_TOOL_ROUNDS = 8

SYSTEM_PROMPT = """És o H.E.L.I.O.S. (High-Efficiency Local Intelligence & Operating System).
O teu utilizador chama-se Simão. Trata-o sempre pelo nome com tom amigável, empático e descontraído — como o melhor amigo e braço direito dele.

Personalidade:
- Cumprimentos calorosos: "Olá Simão!", "Deixa isso comigo, amigo.", "Grande, vou a isso!"  
- Quando algo falha: "Ups Simão, [problema]. Queres que tente [alternativa]?"
- Quando consegues: "Pronto Simão! [resultado]"
- Nunca és genérico nem robótico. És eficiente, rápido e directo.

Capacidades:
- Automação web: navegar, extrair conteúdo, preços, pesquisar, interagir com páginas
- Controlo do PC: PowerShell, ficheiros, processos, rede, volume, brilho
- Organização: renomear ficheiros com IA, limpar cache, monitorizar recursos
- Memória: lembras-te de conversas passadas e preferências do Simão
- Modos de trabalho: activar ambientes com 1 comando ("modo hacker", "modo foco")

Regras:
- Usa SEMPRE as ferramentas quando pedes informação da web ou do sistema
- Para ações destrutivas pede SEMPRE confirmação
- Responde em Português de Portugal
- Sê conciso — o Simão é produtivo, não gosta de textos longos sem necessidade
- Quando descobrires algo duradouro sobre o Simão (nome, gostos, horários, hábitos,
  ferramentas que usa), grava com a ferramenta 'remember_fact'. Não gravas conversa
  banal nem coisas temporárias."""


def _ollama_chat_url(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/api/chat") else f"{url}/api/chat"


class Brain:
    def __init__(self, api_key: str, guardrails: GuardrailsEngine,
                 memory: MemoryEngine, config: dict | None = None):
        cfg = config if config is not None else load_config()
        mem_cfg = cfg.get("memory") or {}

        self.client     = AsyncGroq(api_key=api_key)
        self.guardrails = guardrails
        self.memory     = memory
        self.conversation: list[dict] = []

        self.groq_model     = cfg.get("groq_model") or GROQ_MODEL
        self.ollama_enabled = bool(cfg.get("ollama_enabled", True))
        self.ollama_model   = cfg.get("ollama_model") or OLLAMA_MODEL
        self.ollama_url     = _ollama_chat_url(cfg.get("ollama_url") or OLLAMA_BASE_URL)

        self.history_messages  = int(mem_cfg.get("history_messages", 20))
        self.max_history_chars = int(mem_cfg.get("max_history_chars", 8000))
        self.facts_enabled     = bool(mem_cfg.get("facts_enabled", True))
        self.rag_enabled       = bool(mem_cfg.get("rag_enabled", True))
        self.rag_results       = int(mem_cfg.get("rag_results", 3))
        self.rag_max_chars     = int(mem_cfg.get("rag_max_chars", 1500))

    # ─── Memória ──────────────────────────────────────────────────────────

    def load_history(self) -> int:
        """Carrega as conversas anteriores do SQLite para o contexto activo."""
        try:
            history = self.memory.get_context_window(
                limit     = self.history_messages,
                max_chars = self.max_history_chars,
            )
        except Exception as exc:
            logger.error(f"Falha ao carregar histórico: {exc}")
            return 0

        self.conversation = list(history)
        logger.info(f"Memória: {len(self.conversation)} mensagens recuperadas de sessões anteriores.")
        return len(self.conversation)

    def _trim_conversation(self):
        """Evita que o contexto activo cresça sem limite durante uma sessão longa."""
        budget = self.max_history_chars * 2
        total  = sum(len(str(m.get("content") or "")) for m in self.conversation)
        while total > budget and len(self.conversation) > 2:
            removed = self.conversation.pop(0)
            total  -= len(str(removed.get("content") or ""))
            # Nunca deixar mensagens 'tool' órfãs no início do contexto
            while self.conversation and self.conversation[0].get("role") == "tool":
                orphan = self.conversation.pop(0)
                total -= len(str(orphan.get("content") or ""))

    def _facts_block(self) -> str:
        if not self.facts_enabled:
            return ""
        facts = self.memory.get_facts()
        if not facts:
            return ""
        lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
        return f"\n\nO que já sabes sobre o Simão (memória persistente):\n{lines}"

    async def _rag_block(self, user_message: str) -> str:
        if not self.rag_enabled or len(user_message.strip()) < 8:
            return ""
        try:
            results = await asyncio.to_thread(
                self.memory.search_documents, user_message, self.rag_results
            )
        except Exception as exc:
            logger.debug(f"RAG indisponível: {exc}")
            return ""
        if not results:
            return ""

        chunks, used = [], 0
        for res in results:
            text = (res.get("text") or "").strip()
            if not text:
                continue
            if used + len(text) > self.rag_max_chars:
                text = text[: max(0, self.rag_max_chars - used)]
            if not text:
                break
            source = (res.get("metadata") or {}).get("filename", "documento")
            chunks.append(f"[{source}] {text}")
            used += len(text)

        if not chunks:
            return ""
        return (
            "\n\nExcertos dos documentos do Simão que podem ser relevantes "
            "(usa-os só se ajudarem):\n" + "\n".join(chunks)
        )

    async def _build_system_prompt(self, user_message: str) -> str:
        return SYSTEM_PROMPT + self._facts_block() + await self._rag_block(user_message)

    def reset_conversation(self):
        self.conversation.clear()

    async def chat(self, user_message: str, stream: bool = True) -> AsyncIterator[str]:
        self.conversation.append({"role": "user", "content": user_message})
        self._trim_conversation()
        tools         = get_all_tools()
        system_prompt = await self._build_system_prompt(user_message)

        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.chat.completions.create(
                    model    = self.groq_model,
                    messages = [{"role": "system", "content": system_prompt}, *self.conversation],
                    tools       = tools if tools else None,
                    tool_choice = "auto" if tools else None,
                    temperature = 0.65,
                    max_tokens  = 4096,
                )
            except Exception as exc:
                logger.error(f"Groq error (round {round_num}): {exc}")
                async for token in self._ollama_fallback(user_message):
                    yield token
                return

            choice  = response.choices[0]
            message = choice.message

            # Resposta final de texto
            if choice.finish_reason == "stop" or not message.tool_calls:
                text = message.content or ""
                self.conversation.append({"role": "assistant", "content": text})
                if stream and text:
                    for i in range(0, len(text), 4):
                        yield text[i:i+4]
                        await asyncio.sleep(0.008)
                else:
                    yield text
                return

            # Tool calls
            self.conversation.append({
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in message.tool_calls
                ],
            })

            names = [tc.function.name for tc in message.tool_calls]
            yield f"_thinking_:⚙️ {', '.join(names)}..."

            results = await asyncio.gather(*[
                self._run_tool(tc) for tc in message.tool_calls
            ])

            for tc, result in zip(message.tool_calls, results):
                self.conversation.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result, ensure_ascii=False),
                })

        yield "Simão, atingi o limite de operações encadeadas. Podes reformular o pedido?"

    async def _run_tool(self, tool_call) -> dict:
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except json.JSONDecodeError:
            args = {}

        if self.guardrails.requires_confirmation(name, args):
            confirmed = await self.guardrails.ask_confirmation(name, args)
            if not confirmed:
                return {"cancelled": True, "message": "Operação cancelada pelo Simão."}

        logger.info(f"Tool: {name}({json.dumps(args, ensure_ascii=False)[:120]})")
        return await execute_tool(name, args)

    async def _ollama_fallback(self, message: str) -> AsyncIterator[str]:
        if not self.ollama_enabled:
            yield (
                "Ups Simão, o Groq está inacessível e o modelo local está desactivado "
                "(ollama_enabled: false no settings.yaml). Verifica a ligação ou a chave da API."
            )
            return

        yield "_thinking_:Groq indisponível — a usar modelo local..."
        # O modelo local não faz function-calling: recebe só o diálogo simples
        history = [
            {"role": m["role"], "content": m.get("content") or ""}
            for m in self.conversation[-8:]
            if m.get("role") in ("user", "assistant") and not m.get("tool_calls")
        ]
        if not history or history[-1].get("content") != message:
            history.append({"role": "user", "content": message})

        payload = {
            "model": self.ollama_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + self._facts_block()},
                *history,
            ],
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", self.ollama_url, json=payload) as resp:
                    async for line in resp.aiter_lines():
                        if line:
                            try:
                                data  = json.loads(line)
                                token = data.get("message", {}).get("content", "")
                                if token:
                                    yield token
                            except json.JSONDecodeError:
                                continue
        except httpx.ConnectError:
            yield (
                "Ups Simão, tanto o Groq como o Ollama estão inacessíveis. "
                "Confirma a tua ligação e que o Ollama está a correr com `ollama serve`."
            )
