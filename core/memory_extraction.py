"""Deciding what, if anything, in a user message is worth remembering forever.

TWO CLASSES, AND THEY ARE NOT TREATED ALIKE
-------------------------------------------
**Explicit.** The user said so: "lembra-te que...", "guarda isto", "a partir de
agora...", "don't forget...". There is no ambiguity about intent, so the memory
is stored active with high confidence and it survives.

**Inferred.** Nano noticed a sentence that looks like a durable fact about the
user's world — the graphics card they own, the editor they use, the project they
are building. This is a guess, and it is stored as a *candidate*: visible in
Memória, never injected into the model's context, promoted only by the user.

THE BAR IS DELIBERATELY HIGH
----------------------------
The tempting design is to run every message through a classifier and store
whatever scores above a threshold. That produces a memory store full of "hoje
está a chover" and "acho que vou almoçar", and a retrieval layer that returns
it. So inference here is intentionally narrow:

* at most ONE candidate per message, and only from a sentence that matches a
  first-person durable-fact pattern;
* nothing from a question, a hypothetical, a negation or small talk;
* nothing from a message carrying fenced external content;
* nothing that ``core.memory_safety`` rejects.

Extraction is pure: it reads a string and returns candidates. It writes nothing
and knows nothing about the database, which is what makes it testable in
isolation and what keeps the storage decision (and its safety gate) in one
place.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core import memory_safety, text_normalize
from core.trust import UNTRUSTED_BLOCK_CLOSE, UNTRUSTED_BLOCK_OPEN

#: "Remember that X" in both languages. Group 1 is the thing to remember.
_EXPLICIT_TRIGGERS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:lembra-?te|recorda|memoriza)\s+(?:de\s+)?(?:que\s+)?(.+)", re.I | re.S),
    re.compile(r"\b(?:guarda|grava|regista)\s+(?:isto|isso|que|o seguinte)[:,]?\s*(.+)", re.I | re.S),
    re.compile(r"\bn[ãa]o\s+te\s+esque[çc]as\s+(?:de\s+)?(?:que\s+)?(.+)", re.I | re.S),
    re.compile(r"\ba partir de agora[,:]?\s*(.+)", re.I | re.S),
    re.compile(r"\bremember\s+(?:that\s+)?(.+)", re.I | re.S),
    re.compile(r"\b(?:save|store)\s+(?:this|that)[:,]?\s*(.+)", re.I | re.S),
    re.compile(r"\bdon'?t forget\s+(?:that\s+)?(.+)", re.I | re.S),
    re.compile(r"\bfrom now on[,:]?\s*(.+)", re.I | re.S),
)

#: First-person statements about a durable state of the world. The leading
#: anchor matters: "o meu PC tem 16 GB" is a fact, "o PC dele tem 16 GB" is not
#: something Nano should file under the user.
_DURABLE = re.compile(
    r"^\s*(?:o\s+meu|a\s+minha|os\s+meus|as\s+minhas|eu\s+(?:sou|tenho|uso|utilizo|prefiro|trabalho)|"
    r"sou\s+|tenho\s+(?:um|uma|o|a)\s|uso\s+(?:o|a|um|uma)\s|prefiro\s|chamo-?me\s|"
    r"trabalho\s+(?:com|em|na|no)\s|my\s|i\s+(?:am|have|use|prefer|work))", re.I)

#: Anything here disqualifies a sentence from inference: it is a question, a
#: hypothetical, a negation, or something that will not be true tomorrow.
_NOT_DURABLE = re.compile(
    r"\?|\b(?:talvez|se calhar|acho que|penso que|provavelmente|as vezes|às vezes|"
    r"hoje|ontem|amanh[ãa]|agora mesmo|neste momento|por agora|"
    r"n[ãa]o\s+(?:sei|tenho|uso|gosto)|maybe|i think|probably|today|tomorrow|"
    r"right now|for now)\b", re.I)

_SMALL_TALK = re.compile(
    r"^\s*(ol[áa]|oi|bom dia|boa tarde|boa noite|obrigad[oa]|hi|hello|hey|thanks|"
    r"ok|okay|certo|fixe|adeus|bye)\b", re.I)

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;\n])\s+")

#: Keyword -> memory kind. First match wins, most specific first. Used to
#: categorise a memory so the Memória page can filter it and the Second Brain
#: can pick a node type.
_KIND_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hardware", re.compile(
        r"\b(gpu|cpu|placa\s+gr[áa]fica|gr[áa]fica|processador|ram|mem[óo]ria\s+ram|"
        r"disco|ssd|nvme|monitor|ecr[ãa]|teclado|rato|port[áa]til|desktop|"
        r"gtx|rtx|radeon|geforce|ryzen|intel|nvidia|amd|placa-?m[ãa]e|graphics card)\b", re.I)),
    ("software", re.compile(
        r"\b(windows|linux|ubuntu|macos|python|node|docker|git|github|vs\s?code|"
        r"visual studio|spotify|discord|chrome|firefox|edge|steam|ollama|groq|"
        r"photoshop|blender|obs|excel|word|browser|navegador|aplica[çc][ãa]o)\b", re.I)),
    ("project", re.compile(
        r"\b(projet[oc]|projeto|project|reposit[óo]rio|repo|app\s+que|estou a construir|"
        r"estou a fazer|building)\b", re.I)),
    ("person", re.compile(
        r"\b(chamo-?me|o meu nome|my name|a minha (?:mulher|esposa|namorada|m[ãa]e|pai|"
        r"irm[ãa]o?|filha?)|o meu (?:marido|namorado|m[ãa]e|pai|irm[ãa]o|filho))\b", re.I)),
    ("goal", re.compile(
        r"\b(objetivo|meta|quero\s+(?:chegar|conseguir|aprender)|goal|i want to)\b", re.I)),
    ("decision", re.compile(
        r"\b(decidi|decidimos|vamos usar|vou usar|optei|escolhi|we decided|i'll use)\b", re.I)),
    ("preference", re.compile(
        # `pref[ei]r\w*`, not the bare first person. These rules classify the
        # QUESTION as well as the memory, and a user asks "como PREFERES
        # responder?" while their memory says "PREFIRO respostas curtas" --
        # matching one spelling meant a question could never reach its own
        # answer. Portuguese conjugates the stem two ways (prefIRo, prefERes),
        # so the character class covers both.
        r"\b(pref[ei]r\w*|gosto|n[ãa]o gosto|odeio|sempre que|trata-me|fala comigo|"
        r"respond\w*\s+(?:sempre|em|as|às)|i prefer|i like|always)\b", re.I)),
)

MAX_INFERRED_PER_MESSAGE = 1
MAX_EXPLICIT_PER_MESSAGE = 2


@dataclass(frozen=True)
class MemoryCandidate:
    """One thing that could be remembered, and how sure Nano is about it."""

    text: str
    kind: str
    origin: str          # "explicit" | "inferred"
    confidence: float
    importance: int

    def as_dict(self) -> dict:
        return {"text": self.text, "kind": self.kind, "origin": self.origin,
                "confidence": self.confidence, "importance": self.importance}


def classify_kind(text: str) -> str:
    for kind, pattern in _KIND_RULES:
        if pattern.search(str(text or "")):
            return kind
    return "fact"


def is_explicit_request(text: str) -> bool:
    """True when the user asked, in words, for something to be remembered."""
    return any(pattern.search(str(text or "")) for pattern in _EXPLICIT_TRIGGERS)


def _clean(candidate: str) -> str:
    value = " ".join(str(candidate or "").split())
    value = value.strip(" \t\"'`.,;:-—–")
    # A trailing polite clause is noise in a stored fact.
    value = re.sub(r",?\s*(?:por favor|please|se faz favor|obrigad[oa])\.?$", "", value, flags=re.I)
    return value.strip()


def _acceptable(text: str) -> bool:
    if not text or len(text) < memory_safety.MIN_MEMORY_CHARS:
        return False
    if len(text) > memory_safety.MAX_MEMORY_CHARS:
        return False
    return memory_safety.evaluate(text).allowed


def extract(user_text: str) -> list[MemoryCandidate]:
    """Candidates from ONE user message. Explicit first, then at most one guess.

    Returns [] far more often than not, and that is the intended behaviour.
    """
    body = str(user_text or "")
    if not body.strip() or _SMALL_TALK.match(body.strip()):
        return []
    if UNTRUSTED_BLOCK_OPEN in body or UNTRUSTED_BLOCK_CLOSE in body:
        # The message is carrying external content. Nothing inside it is the
        # user speaking, so nothing inside it may become a memory.
        return []

    candidates: list[MemoryCandidate] = []
    seen: set[str] = set()

    def push(text: str, *, origin: str, confidence: float, importance: int) -> None:
        clean = _clean(text)
        key = text_normalize.normalize(clean)[:120]
        if not key or key in seen or not _acceptable(clean):
            return
        seen.add(key)
        candidates.append(MemoryCandidate(
            text=clean, kind=classify_kind(clean), origin=origin,
            confidence=confidence, importance=importance))

    for pattern in _EXPLICIT_TRIGGERS:
        match = pattern.search(body)
        if not match:
            continue
        # Only the first sentence of the remainder: "lembra-te que uso Linux. E
        # abre o Spotify" must remember the fact, not the request that follows.
        remainder = _SENTENCE_SPLIT.split(match.group(1).strip(), 1)[0]
        push(remainder, origin="explicit", confidence=0.95, importance=4)
        if len(candidates) >= MAX_EXPLICIT_PER_MESSAGE:
            break

    if candidates:
        return candidates[:MAX_EXPLICIT_PER_MESSAGE]

    inferred = 0
    for raw in _SENTENCE_SPLIT.split(body):
        sentence = " ".join(raw.split())
        if not sentence or _NOT_DURABLE.search(sentence) or not _DURABLE.match(sentence):
            continue
        before = len(candidates)
        push(sentence, origin="inferred", confidence=0.55, importance=3)
        if len(candidates) > before:
            inferred += 1
        if inferred >= MAX_INFERRED_PER_MESSAGE:
            break

    return candidates


#: Memory kinds that name a CLASS OF THING, and the node type they become. A
#: `hardware` memory is about a device; a `software` one is about a tool.
#:
#: `preference` and `goal` are deliberately absent. They were here first, and
#: the result was nodes called "Simao e prefiro" and "meu nome e": a preference
#: is a statement about behaviour, it rarely names an entity, and forcing one
#: out of it produces exactly the graph clutter this design exists to avoid. A
#: preference stays a memory unless the user makes a node for it by hand.
NODE_TYPE_FOR_KIND: dict[str, str] = {
    "hardware": "device",
    "software": "software",
    "project": "project",
    "person": "person",
    "decision": "decision",
}

#: Proper nouns and product-shaped identifiers inside a memory. Used ONLY to
#: name a node, never to assert anything about it.
#:
#: CASE MATTERS HERE, AND THE re.I FLAG MUST NOT COME BACK. This pattern was
#: written with re.I, which makes [A-Z] match lowercase too and turns a
#: capitalisation test into "any word at all". Every sentence then yielded an
#: entity and the Second Brain filled with fragments of ordinary prose --
#: "meu nome e", "Simao e prefiro", "respostas curtas". The model-number
#: alternatives genuinely need case-insensitivity, so they carry their own
#: inline (?i:...) groups instead of the whole pattern being folded.
#:
#: A leading stop word is stripped by `entities` rather than excluded here: a
#: name can legitimately follow one ("o Ollama"), and a pattern that tries to
#: express that becomes unreadable.
_ENTITY = re.compile(
    r"(?:(?i:GTX|RTX|RX)\s?\d{3,4}(?:\s?(?i:Ti|XT|Super))?"
    r"|(?i:ryzen|core\s?i\d)[\s-]?\d{3,5}[A-Za-z]{0,2}"
    r"|[A-ZÀ-Þ][\wÀ-ÿ]{2,}(?:\s+[A-ZÀ-Þ][\wÀ-ÿ]{2,}){0,2})")

#: TWO DIFFERENT JOBS, AND CONFLATING THEM PRODUCED A REAL BUG.
#:
#: `_ENTITY_FILLER` is trimmed from the EDGES of a matched span: articles,
#: prepositions, pronouns and the first-person verbs that start a Portuguese
#: sentence. A capitalised sentence-initial "Estou" is filler, not a name.
#:
#: `_ENTITY_REJECT` is never a node on its own but is perfectly good INSIDE a
#: name. "Nano" alone is the assistant, not an entity worth a node; "Nano
#: Assistant" is a project. Putting "nano" in the trim set deleted the first
#: half of that name and left a node called "Assistant".
_ENTITY_FILLER = frozenset({
    "eu", "tu", "ele", "ela", "o", "a", "os", "as", "um", "uma",
    "meu", "minha", "meus", "minhas", "de", "do", "da", "em", "no", "na",
    "com", "e", "que", "the", "my", "i", "sim", "nao", "não",
    "sou", "estou", "estamos", "tenho", "tem", "uso", "utilizo", "prefiro",
    "gosto", "odeio", "chamo", "nome", "trabalho", "quero", "quis",
    "vou", "vamos", "decidi", "corro", "fiz", "fui", "usei", "comprei",
})

_ENTITY_REJECT = frozenset({
    "nano", "pc", "computador", "portatil", "portátil", "maquina", "máquina",
    "projeto", "project", "coisa", "coisas",
})


def entities(text: str, *, limit: int = 2) -> list[str]:
    """Candidate node titles inside a memory. Conservative and bounded.

    A match is trimmed word by word from both ends while the edge word is
    filler, so "Uso o Visual Studio Code" yields "Visual Studio Code" rather
    than "Uso o Visual". What survives must be at least three characters and
    must not be a word that is never an entity on its own -- no node at all is
    much better than a node named after a fragment.
    """
    found: list[str] = []
    seen: set[str] = set()
    for match in _ENTITY.findall(str(text or "")):
        name = _trim_entity(" ".join(str(match).split()))
        key = text_normalize.normalize(name)
        if not key or key in seen or len(key) < 3 or key.isdigit():
            continue
        if key in _ENTITY_REJECT or key in _ENTITY_FILLER:
            continue
        seen.add(key)
        found.append(name)
        if len(found) >= max(1, int(limit)):
            break
    return found


def _trim_entity(name: str) -> str:
    """Drop leading and trailing filler words from a matched span."""
    words = [word for word in str(name or "").split() if word]
    while words and text_normalize.normalize(words[0]) in _ENTITY_FILLER:
        words.pop(0)
    while words and text_normalize.normalize(words[-1]) in _ENTITY_FILLER:
        words.pop()
    return " ".join(words)


__all__ = [
    "MAX_EXPLICIT_PER_MESSAGE",
    "MAX_INFERRED_PER_MESSAGE",
    "NODE_TYPE_FOR_KIND",
    "MemoryCandidate",
    "classify_kind",
    "entities",
    "extract",
    "is_explicit_request",
]
