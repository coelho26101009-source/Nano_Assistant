"""Deterministic task classification, model tiering and tool scoping.

Three decisions are made here, all of them cheap, local and repeatable — no
extra LLM call is ever made just to decide how to answer a message:

1. **What kind of request is this?**  ``classify`` maps the user's text to a
   :class:`TaskClass` using explicit keyword evidence.
2. **Which model tier should answer?**  Conversation goes to the fast model;
   only genuine reasoning/coding work is promoted to the strong model.
3. **Which tools may the model even see?**  ``select_tools`` sends the smallest
   plausible subset instead of the whole registry.

Why this exists
---------------
Nano was attaching all 36 tool definitions (~10.9 KB of JSON, ~1500 tokens) to
*every* message, so a bare "Olá" cost ~1675 prompt tokens. On a Groq account
with an 8000 tokens-per-minute ceiling that exhausts the budget after roughly
five messages, after which every request is rate-limited. Scoping the payload
is what makes ordinary conversation sustainable.

Security note
-------------
Tool *filtering is not permission*. Narrowing what the model can request only
reduces noise and cost. Everything the model does ask for still travels the
full MODEL -> REQUEST -> POLICY -> PERMISSION -> EXECUTION path, and this
module never executes anything, never resolves a capability and never sees a
secret. Widening the filter can never grant an ability; the executor decides.
"""
from __future__ import annotations

import re
import unicodedata
from enum import Enum
from typing import Any, Iterable

__all__ = [
    "ModelTier",
    "TaskClass",
    "ToolCategory",
    "classify",
    "describe_selection",
    "max_tokens_for",
    "select_tools",
    "tier_for",
]


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    """Fold accents so "memória" and "memoria" match the same keyword."""
    decomposed = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def _normalize(text: str) -> str:
    lowered = _strip_accents(str(text or "").lower())
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", lowered)).strip()


def _has_any(haystack: str, needles: Iterable[str]) -> bool:
    """Whole-word-ish containment: substrings must sit on a word boundary.

    Plain ``in`` matched "ram" inside "programa" and promoted small talk to a
    tool request, so every needle is anchored.
    """
    for needle in needles:
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack):
            return True
    return False


# ---------------------------------------------------------------------------
# Task classes and model tiers
# ---------------------------------------------------------------------------

class TaskClass(str, Enum):
    """What the user is asking for, in terms that change how Nano answers."""

    SMALL_TALK = "SMALL_TALK"   # greetings, thanks, chit-chat
    QUESTION = "QUESTION"       # explain/define something; no side effects
    ACTION = "ACTION"           # do something on the machine or the web
    COMPLEX = "COMPLEX"         # multi-step reasoning, coding, analysis


class ModelTier(str, Enum):
    FAST = "FAST"
    STRONG = "STRONG"


# Evidence that the user wants real reasoning or code, not conversation.
# Deliberately narrow: message length is NOT evidence, because a long rambling
# question is still conversation and does not need the expensive model.
_COMPLEX_KEYWORDS = (
    # pt
    "analisa", "analisar", "analise", "investiga", "investigar", "depura",
    "debug", "corrige", "corrigir", "refactor", "refactoriza", "optimiza",
    "otimiza", "optimizar", "implementa", "implementar", "escreve codigo",
    "escrever codigo", "programa", "algoritmo", "compara em detalhe",
    "planeia", "planear", "arquitetura", "arquitectura", "demonstra", "prova",
    "calcula", "calcular", "resolve", "resolver", "traduz este codigo",
    # en
    "analyse", "analyze", "refactor", "optimize", "implement", "algorithm",
    "architecture", "debug", "prove", "derive",
)

_CODE_MARKERS = ("```", "def ", "class ", "function ", "import ", "select ", "#include")

_SMALL_TALK = (
    "ola", "olaa", "oi", "hey", "hi", "hello", "bom dia", "boa tarde",
    "boa noite", "tudo bem", "como estas", "como esta", "como vai",
    "obrigado", "obrigada", "adeus", "ate logo", "ate amanha", "boas",
    "tchau", "thanks", "thank you", "bye", "piada", "conta me uma piada",
)

# Asking Nano to *explain a concept*. These never need a tool: the answer comes
# from the model's own knowledge, so "explica o que e a RAM" must not drag in
# the PC toolset merely because it contains the word "RAM".
_EXPLANATORY_MARKERS = (
    "explica", "explicar", "explique", "o que e", "o que sao", "quem e",
    "quem foi", "porque", "porque e", "porque que", "quais sao", "define",
    "definicao", "significa", "diferenca entre", "para que serve",
    "what is", "who is", "explain", "why", "how does",
)

# Asking about the *state of this machine or this moment*. These do need their
# category tools, or Nano can only guess ("que horas sao" was being answered
# from nothing at all).
_STATE_MARKERS = (
    "quanto", "quanta", "quantos", "quantas", "tenho", "esta", "estao",
    "agora", "actual", "atual", "livre", "disponivel", "restante", "resta",
    "que horas", "quanto tempo", "em uso", "ocupado",
    "how much", "how many", "available", "current",
)

_QUESTION_MARKERS = _EXPLANATORY_MARKERS + ("qual e", "quando", "onde")


# ---------------------------------------------------------------------------
# Tool categories
# ---------------------------------------------------------------------------

class ToolCategory(str, Enum):
    NONE = "NONE"
    PC = "PC"
    FILES = "FILES"
    BROWSER = "BROWSER"
    MEMORY = "MEMORY"
    SCHEDULE = "SCHEDULE"
    IOT = "IOT"


# Which registered tool names belong to which category. Matching is by prefix
# or exact name, so a plugin adding "web_foo" lands in BROWSER automatically.
_CATEGORY_TOOLS: dict[ToolCategory, tuple[str, ...]] = {
    ToolCategory.PC: (
        "system_stats", "system_run_powershell", "system_volume",
        "system_brightness", "system_bluetooth", "system_wifi",
        "context_activate_mode", "context_list_modes",
        "monitor_status", "monitor_start", "monitor_stop",
        "clean_windows_cache",
    ),
    ToolCategory.FILES: (
        "system_files", "organize_downloads", "rename_file_smart",
        "rag_index_pdf", "clean_windows_cache",
    ),
    ToolCategory.BROWSER: (
        "web_navigate_extract", "web_extract_prices", "web_search",
        "web_interact", "web_screenshot",
    ),
    ToolCategory.MEMORY: (
        "remember_fact", "list_facts", "forget_fact",
        "memory_search_history", "rag_search", "rag_list_docs",
    ),
    ToolCategory.SCHEDULE: (
        "calendar_add_event", "calendar_list_events", "calendar_delete_event",
        "calendar_import_ics", "set_reminder", "list_reminders",
        "cancel_reminder",
    ),
    ToolCategory.IOT: ("iot_command", "phone_notify"),
}

# Keyword evidence for each category, Portuguese first.
_CATEGORY_KEYWORDS: dict[ToolCategory, tuple[str, ...]] = {
    ToolCategory.PC: (
        "abre", "abrir", "fecha", "fechar", "executa", "executar", "corre",
        "lanca", "lancar", "inicia", "iniciar", "desliga", "desligar", "liga",
        "ligar", "reinicia", "reiniciar", "volume", "som", "brilho",
        "bluetooth", "wifi", "wi fi", "rede", "cpu", "ram", "memoria ram",
        "disco", "bateria", "processos", "processo", "aplicacao", "aplicacoes",
        "app", "apps", "programa", "programas", "spotify", "chrome", "browser",
        "powershell", "comando", "terminal", "sistema", "pc", "computador",
        "temperatura", "desempenho", "limpa", "limpar", "cache", "modo",
        "open", "close", "run", "launch", "kill", "shutdown", "restart",
        # The clock lives on the machine: without a tool the model can only
        # guess the time, which is exactly what produced wrong answers before.
        "horas", "hora", "data", "dia de hoje", "que dia", "time", "date",
    ),
    ToolCategory.FILES: (
        "ficheiro", "ficheiros", "pasta", "pastas", "diretorio", "directorio",
        "documento", "documentos", "guarda", "guardar", "grava", "gravar",
        "cria", "criar", "apaga", "apagar", "elimina", "eliminar", "renomeia",
        "renomear", "move", "mover", "copia", "copiar", "organiza", "organizar",
        "downloads", "ambiente de trabalho", "desktop", "pdf", "txt", "csv",
        "file", "files", "folder", "directory", "save", "delete", "rename",
    ),
    ToolCategory.BROWSER: (
        "pesquisa", "pesquisar", "procura", "procurar", "busca", "buscar",
        "google", "internet", "web", "site", "website", "url", "link",
        "noticia", "noticias", "preco", "precos", "artigo", "pagina",
        "navega", "navegar", "screenshot", "captura de ecra", "online",
        "search", "browse", "news", "price",
        # Live-world facts Nano cannot know from the model alone. Without these
        # "que tempo faz hoje em Lisboa?" was answered from nothing at all.
        "tempo faz", "meteorologia", "previsao", "clima", "temperatura em",
        "cotacao", "bolsa", "resultado do", "weather", "forecast",
    ),
    ToolCategory.MEMORY: (
        "lembra", "lembrar", "lembras", "recorda", "recordar", "memoriza",
        "memorizar", "guarda que", "sabes sobre mim", "o que sabes",
        "esquece", "esquecer", "conversa anterior",
        "conversas antigas", "historico", "documento indexado",
        "remember", "forget", "recall",
        # "facto" alone matched "diz-me um facto curioso", which is small talk,
        # so the memory tools are keyed on phrases that really mean stored facts.
        "facto sobre mim", "factos sobre mim", "o que guardaste",
        "factos guardados", "lembras te de",
    ),
    ToolCategory.SCHEDULE: (
        "lembrete", "lembretes", "alarme", "alarmes", "agenda", "agendar",
        "calendario", "evento", "eventos", "reuniao", "reunioes", "marca",
        "marcar", "compromisso", "amanha", "hoje as", "proxima semana",
        "reminder", "calendar", "schedule", "meeting", "event",
    ),
    ToolCategory.IOT: (
        "luz", "luzes", "lampada", "tomada", "iot", "smart", "domotica",
        "telemovel", "telefone", "notificacao", "notifica",
        "light", "lights", "plug", "notify", "phone",
    ),
}

# When the request clearly wants an action but no category matches, send this
# bounded general set rather than the whole registry.
_AMBIGUOUS_FALLBACK = (
    "system_stats", "system_run_powershell", "system_files", "web_search",
)

# Verbs that mean "do something", used to tell an ACTION from a QUESTION.
_ACTION_VERBS = (
    "abre", "abrir", "fecha", "fechar", "executa", "executar", "corre",
    "cria", "criar", "apaga", "apagar", "elimina", "eliminar", "guarda",
    "guardar", "grava", "gravar", "move", "mover", "copia", "copiar",
    "renomeia", "renomear", "organiza", "organizar", "define", "definir",
    "poe", "por", "coloca", "colocar", "muda", "mudar", "altera", "alterar",
    "liga", "ligar", "desliga", "desligar", "reinicia", "reiniciar",
    "pesquisa", "pesquisar", "procura", "procurar", "busca", "buscar",
    "marca", "marcar", "agenda", "agendar", "lembra me", "lembrar",
    "manda", "mandar", "envia", "enviar", "mostra", "mostrar", "lista",
    "listar", "limpa", "limpar", "instala", "instalar", "descarrega",
    "open", "close", "run", "create", "delete", "save", "search", "send",
    "show", "list", "set", "turn",
)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _categories_for(normalized: str) -> list[ToolCategory]:
    return [cat for cat, words in _CATEGORY_KEYWORDS.items() if _has_any(normalized, words)]


def _is_imperative(normalized: str) -> bool:
    """True when an action verb leads the sentence, as Portuguese commands do.

    Matching an action verb anywhere in the text misread ordinary conversation:
    "estou a pensar em mudar de cidade" contains "mudar" and was being treated
    as a command. A real instruction ("abre o Spotify", "cria um ficheiro")
    puts the verb at the front, so only the opening words count.
    """
    head = " ".join(normalized.split()[:3])
    return _has_any(head, _ACTION_VERBS)


def classify(text: str) -> TaskClass:
    """Classify a user message. Deterministic, no model call, no I/O."""
    normalized = _normalize(text)
    if not normalized:
        return TaskClass.SMALL_TALK

    raw = str(text or "")
    imperative = _is_imperative(normalized)
    asks_state = _has_any(normalized, _STATE_MARKERS)

    # Code in the message is unambiguous evidence of real work.
    if any(marker in raw.lower() for marker in _CODE_MARKERS):
        return TaskClass.COMPLEX

    # "O que e um algoritmo de ordenacao?" is a definition request, not a
    # request to write one. An explanatory question is answered from knowledge,
    # so it stays on the fast model with no tools even when it happens to
    # contain a word like "algoritmo" that otherwise signals real work.
    if _has_any(normalized, _EXPLANATORY_MARKERS) and not imperative and not asks_state:
        return TaskClass.QUESTION

    if _has_any(normalized, _COMPLEX_KEYWORDS):
        return TaskClass.COMPLEX

    # A short message that is only a greeting stays small talk. The length
    # guard stops "ola, abre o spotify" from being treated as a greeting.
    words = normalized.split()
    if len(words) <= 4 and _has_any(normalized, _SMALL_TALK):
        return TaskClass.SMALL_TALK

    categories = _categories_for(normalized)

    if not imperative:
        # "que horas sao", "quanta RAM tenho livre" ask about this machine and
        # need their category tools to be answerable at all.
        if asks_state and categories:
            return TaskClass.ACTION
        if _has_any(normalized, _QUESTION_MARKERS) and not categories:
            return TaskClass.QUESTION

    if imperative or categories:
        return TaskClass.ACTION

    if _has_any(normalized, _QUESTION_MARKERS):
        return TaskClass.QUESTION

    return TaskClass.SMALL_TALK


def tier_for(task: TaskClass) -> ModelTier:
    """Only genuine reasoning/coding earns the expensive model."""
    return ModelTier.STRONG if task == TaskClass.COMPLEX else ModelTier.FAST


# Groq charges the *reserved* budget against tokens-per-minute, not the tokens
# actually generated: a request declaring max_tokens=4096 consumes ~4096 of the
# 8000 TPM ceiling even when the reply is "Olá!". Asking for 4096 on every
# message is therefore what limited Nano to roughly two messages a minute.
# These ceilings are generous for their task and still leave real headroom.
_MAX_TOKENS = {
    TaskClass.SMALL_TALK: 512,
    TaskClass.QUESTION: 1024,
    TaskClass.ACTION: 1536,
    TaskClass.COMPLEX: 4096,
}


def max_tokens_for(task: TaskClass) -> int:
    """Completion ceiling for a task class."""
    return _MAX_TOKENS.get(task, 1024)


# ---------------------------------------------------------------------------
# Tool scoping
# ---------------------------------------------------------------------------

def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        fn = tool.get("function") or {}
        return str(fn.get("name") or tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def select_tools(text: str, all_tools: list[dict] | None, *, task: TaskClass | None = None) -> list[dict]:
    """Return the smallest plausible tool subset for this message.

    Conversation gets an empty list, which is what removes ~1500 prompt tokens
    from the common case. Requests with clear category evidence get only that
    category. A request that clearly wants an action but names no category gets
    a small bounded general set — never the whole registry.
    """
    tools = list(all_tools or [])
    if not tools:
        return []

    task = task or classify(text)
    if task in (TaskClass.SMALL_TALK, TaskClass.QUESTION):
        return []

    normalized = _normalize(text)
    categories = _categories_for(normalized)

    wanted: set[str] = set()
    for category in categories:
        wanted.update(_CATEGORY_TOOLS.get(category, ()))

    if not wanted:
        # COMPLEX work with no category evidence is usually reasoning about
        # something the user pasted; give it the bounded general set so it can
        # still look something up, but nothing more.
        wanted.update(_AMBIGUOUS_FALLBACK)

    selected = [tool for tool in tools if _tool_name(tool) in wanted]
    # Never silently return nothing for a real action request: fall back to the
    # bounded general set if the registry uses names we do not recognise.
    if not selected:
        selected = [tool for tool in tools if _tool_name(tool) in set(_AMBIGUOUS_FALLBACK)]
    return selected


def describe_selection(text: str, all_tools: list[dict] | None) -> dict[str, Any]:
    """Safe, loggable explanation of the decision. Contains no secrets."""
    task = classify(text)
    tools = select_tools(text, all_tools, task=task)
    return {
        "task": task.value,
        "tier": tier_for(task).value,
        "categories": [c.value for c in _categories_for(_normalize(text))],
        "tool_count": len(tools),
        "tools": [_tool_name(t) for t in tools],
        "available": len(all_tools or []),
    }
