"""H.E.L.I.O.S. Brain — motor de decisão e tool calling."""
from __future__ import annotations
import asyncio, json, logging
from typing import AsyncIterator
import httpx
from groq import AsyncGroq
from core.config import load_config
from core.local_runtime import choose_model
from core.plugin_loader import get_all_tools, execute_tool
from core.guardrails import GuardrailsEngine
from core.memory import MemoryEngine

logger = logging.getLogger("helios.brain")
GROQ_MODEL = "llama-3.3-70b-versatile"
OLLAMA_BASE_URL = "http://127.0.0.1:11434"
MAX_TOOL_ROUNDS = 8
SYSTEM_PROMPT = """És o H.E.L.I.O.S. (High-Efficiency Local Intelligence & Operating System).
O teu utilizador chama-se Simão. Trata-o com tom amigável, empático e descontraído.

Regras:
- Usa ferramentas quando precisares de informação real da web ou do sistema.
- Ações destrutivas ou com efeitos externos exigem confirmação humana através dos guardrails.
- Nunca contornes uma confirmação nem inventes o resultado de uma ferramenta.
- Responde em Português de Portugal, de forma concisa e útil.
- Se uma ferramenta falhar, explica o erro sem expor segredos ou detalhes internos.
- Quando descobrires algo duradouro sobre o Simão, grava-o apenas através de 'remember_fact'."""

def _ollama_chat_url(url: str) -> str:
    url = url.rstrip("/")
    return url if url.endswith("/api/chat") else f"{url}/api/chat"

class Brain:
    def __init__(self, api_key: str, guardrails: GuardrailsEngine, memory: MemoryEngine, config: dict | None = None):
        cfg = config if config is not None else load_config()
        mem_cfg, local_cfg = cfg.get("memory") or {}, cfg.get("local") or {}
        self.groq_enabled = bool(api_key.strip())
        self.client = AsyncGroq(api_key=api_key) if self.groq_enabled else None
        self.guardrails, self.memory = guardrails, memory
        self.conversation: list[dict] = []
        self.groq_model = str(cfg.get("groq_model") or GROQ_MODEL)
        self.local_enabled = bool(local_cfg.get("enabled", cfg.get("ollama_enabled", True)))
        self.local_profile = choose_model(cfg)
        self.ollama_model = self.local_profile.model
        self.ollama_url = _ollama_chat_url(str(local_cfg.get("url") or cfg.get("ollama_url") or OLLAMA_BASE_URL))
        self.local_context = max(1024, int(local_cfg.get("max_context", 4096)))
        self.history_messages = max(2, int(mem_cfg.get("history_messages", 20)))
        self.max_history_chars = max(1000, int(mem_cfg.get("max_history_chars", 8000)))
        self.facts_enabled = bool(mem_cfg.get("facts_enabled", True))
        self.rag_enabled = bool(mem_cfg.get("rag_enabled", True))
        self.rag_results = max(1, int(mem_cfg.get("rag_results", 3)))
        self.rag_max_chars = max(200, int(mem_cfg.get("rag_max_chars", 1500)))
        logger.info("Cloud=%s model=%s | Local=%s model=%s (%s, %.1f GB RAM)", self.groq_enabled, self.groq_model, self.local_enabled, self.ollama_model, self.local_profile.reason, self.local_profile.ram_gb)

    def load_history(self) -> int:
        try: self.conversation = list(self.memory.get_context_window(self.history_messages, self.max_history_chars))
        except Exception as exc: logger.error("Falha ao carregar histórico: %s", exc); return 0
        return len(self.conversation)

    def _trim_conversation(self):
        budget = self.max_history_chars * 2
        total = sum(len(str(m.get("content") or "")) for m in self.conversation)
        while total > budget and len(self.conversation) > 2:
            removed = self.conversation.pop(0); total -= len(str(removed.get("content") or ""))
            while self.conversation and self.conversation[0].get("role") == "tool":
                orphan = self.conversation.pop(0); total -= len(str(orphan.get("content") or ""))

    def _facts_block(self) -> str:
        if not self.facts_enabled: return ""
        try: facts = self.memory.get_facts()
        except Exception: logger.exception("Falha ao ler factos"); return ""
        return "\n\nMemória persistente relevante:\n" + "\n".join(f"- {k}: {v}" for k, v in facts.items()) if facts else ""

    async def _rag_block(self, user_message: str) -> str:
        if not self.rag_enabled or len(user_message.strip()) < 8: return ""
        try: results = await asyncio.to_thread(self.memory.search_documents, user_message, self.rag_results)
        except Exception as exc: logger.debug("RAG indisponível: %s", exc); return ""
        chunks, used = [], 0
        for res in results:
            text = (res.get("text") or "").strip(); remaining = self.rag_max_chars - used
            if not text or remaining <= 0: break
            text = text[:remaining]; source = (res.get("metadata") or {}).get("filename", "documento")
            chunks.append(f"[{source}] {text}"); used += len(text)
        return "\n\nExcertos relevantes dos documentos:\n" + "\n".join(chunks) if chunks else ""

    async def _build_system_prompt(self, user_message: str) -> str:
        return SYSTEM_PROMPT + self._facts_block() + await self._rag_block(user_message)

    def reset_conversation(self): self.conversation.clear()

    async def chat(self, user_message: str, stream: bool = True) -> AsyncIterator[str]:
        self.conversation.append({"role": "user", "content": user_message}); self._trim_conversation()
        tools, system_prompt = get_all_tools(), await self._build_system_prompt(user_message)
        if not self.groq_enabled:
            async for token in self._ollama_fallback(user_message, "Groq não configurado"): yield token
            return
        for round_num in range(MAX_TOOL_ROUNDS):
            try:
                response = await self.client.chat.completions.create(model=self.groq_model, messages=[{"role": "system", "content": system_prompt}, *self.conversation], tools=tools or None, tool_choice="auto" if tools else None, temperature=0.65, max_tokens=4096)
            except Exception as exc:
                logger.warning("Groq indisponível (round %d): %s", round_num, exc)
                async for token in self._ollama_fallback(user_message, "Groq indisponível"): yield token
                return
            message = response.choices[0].message
            if not message.tool_calls:
                text = message.content or ""; self.conversation.append({"role": "assistant", "content": text})
                if stream and text:
                    for i in range(0, len(text), 4): yield text[i:i+4]; await asyncio.sleep(0.008)
                else: yield text
                return
            self.conversation.append({"role": "assistant", "content": message.content, "tool_calls": [{"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}} for tc in message.tool_calls]})
            yield f"_thinking_:⚙️ {', '.join(tc.function.name for tc in message.tool_calls)}..."
            results = await asyncio.gather(*(self._run_tool(tc) for tc in message.tool_calls), return_exceptions=True)
            for tc, result in zip(message.tool_calls, results):
                if isinstance(result, Exception):
                    logger.exception("Tool %s falhou", tc.function.name, exc_info=result); result = {"ok": False, "error": "tool_failed"}
                self.conversation.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})
        yield "Atingi o limite de operações encadeadas. Podes reformular o pedido?"

    async def _run_tool(self, tool_call) -> dict:
        name = tool_call.function.name
        try: args = json.loads(tool_call.function.arguments or "{}")
        except json.JSONDecodeError: return {"ok": False, "error": "invalid_tool_arguments"}
        if not isinstance(args, dict): return {"ok": False, "error": "invalid_tool_arguments"}
        if self.guardrails.requires_confirmation(name, args) and not await self.guardrails.ask_confirmation(name, args): return {"ok": False, "cancelled": True, "message": "Operação cancelada pelo utilizador."}
        try: return await execute_tool(name, args)
        except Exception: logger.exception("Tool %s lançou uma exceção", name); return {"ok": False, "error": "tool_exception"}

    async def _ollama_fallback(self, message: str, reason: str = "") -> AsyncIterator[str]:
        if not self.local_enabled:
            yield "O HELIOS não tem um modelo local ativo e o serviço online não está disponível."; return
        yield f"_thinking_:🧠 {reason + ' — ' if reason else ''}a usar {self.ollama_model} local..."
        history = [{"role": m["role"], "content": m.get("content") or ""} for m in self.conversation[-8:] if m.get("role") in ("user", "assistant") and not m.get("tool_calls")]
        if not history or history[-1].get("content") != message: history.append({"role": "user", "content": message})
        payload = {"model": self.ollama_model, "messages": [{"role": "system", "content": SYSTEM_PROMPT + self._facts_block()}, *history], "stream": True, "options": {"temperature": 0.65, "num_ctx": self.local_context}}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream("POST", self.ollama_url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line: continue
                        data = json.loads(line); token = (data.get("message") or {}).get("content") or ""
                        if token: yield token
                        if data.get("done"): return
        except Exception as exc:
            logger.error("Modelo local indisponível: %s", exc)
            yield "O modelo local ainda não está disponível. Abre as definições do HELIOS para ativar o modo local."
