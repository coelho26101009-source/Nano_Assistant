"""Progressive compaction of a conversation into a summary that keeps working.

THE PROBLEM COMPACTION SOLVES
-----------------------------
A long thread cannot be sent to the model in full — the provider window is
finite and, on Groq's 8 000 tokens-per-minute tier, one oversized prompt is a
rate limit. But dropping the oldest turns loses the sentence that mattered:
"a minha placa gráfica é uma GTX 1660 Ti", said forty messages ago.

Two mechanisms answer that, and this module is one of them. Retrieval (see
``core.retrieval``) can go and fetch the exact old turn when the new message
refers to it. The summary here is the standing, always-present digest of what
the thread established, so Nano knows the shape of the conversation even when no
query matches.

WHY IT IS EXTRACTIVE AND NOT A MODEL CALL
-----------------------------------------
Every alternative was a model call, and a model call here is a bad trade:

* It costs tokens on a budget the chat is already competing for, on the same
  account, at the same moment.
* It can fail — a rate limit, an offline Ollama — which would make the
  conversation's memory depend on the provider being up.
* It is not reproducible: the summary could not be rebuilt identically from the
  source messages, which is the property that makes it safe to treat the
  messages as the authority and the summary as a cache.
* It can hallucinate. A summary that invents a fact is worse than no summary,
  because the invention then travels into every later prompt as established
  truth.

Extraction takes sentences the user actually wrote, verbatim. It cannot invent
anything, it costs no tokens, it cannot fail, and running it twice on the same
messages produces the same text. When compaction *does* fail, the caller keeps
the previous summary and the chat is unaffected.

WHAT IS DELIBERATELY LEFT OUT
-----------------------------
* Assistant prose. The summary records what the USER established; feeding the
  model's own paraphrases back as facts is how a small error becomes permanent.
* Anything ``core.memory_safety`` flags as credential material.
* Anything inside an untrusted-content fence. External text is data for one
  turn; a summary is context for every future turn, and promoting one to the
  other is the injection path this whole design refuses.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from core import memory_safety, text_normalize
from core.trust import UNTRUSTED_BLOCK_CLOSE, UNTRUSTED_BLOCK_OPEN

#: Compaction runs when the thread has grown by this much since the last one.
#: Not after every message: re-summarising on each turn would be work nobody
#: asked for, and the summary would change under the user for no reason.
COMPACT_AFTER_MESSAGES = 12
COMPACT_AFTER_CHARS = 6000

#: How many messages stay OUT of the summary because they are still recent
#: enough to be sent verbatim. Compaction never touches the live tail.
KEEP_RECENT_MESSAGES = 8

#: Ceilings. A summary that grows without bound is just the conversation again.
MAX_ITEMS_PER_SECTION = 6
MAX_SUMMARY_CHARS = 1400
MAX_TOPICS = 10
MIN_SENTENCE_CHARS = 12
MAX_SENTENCE_CHARS = 220

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;\n])\s+")

# Sentence classes, Portuguese first because that is what the user writes.
_FACT = re.compile(
    r"\b(sou|tenho|uso|utilizo|chamo-?me|moro|vivo|trabalho|prefiro|gosto|odeio|"
    r"o meu|a minha|os meus|as minhas|estou a usar|corro|instalei|comprei|"
    r"i am|i'm|i have|i use|i prefer|i like|my |we use)\b", re.I)
_DECISION = re.compile(
    r"\b(vamos|vou|decidi|decidimos|optei|optamos|escolhi|escolhemos|ficamos com|"
    r"fica assim|a partir de agora|passamos a|let's|we will|i'll|i will|"
    r"we decided|we're going with)\b", re.I)
_GOAL = re.compile(
    r"\b(quero|queria|preciso|pretendo|objetivo|meta|estou a tentar|"
    r"i want|i need|i'd like|the goal|trying to)\b", re.I)

# Words that mark a sentence as throwaway even when it matches a class above.
_SMALL_TALK = re.compile(
    r"^\s*(ola|olá|oi|bom dia|boa tarde|boa noite|obrigado|obrigada|adeus|"
    r"tudo bem|hi|hello|hey|thanks|thank you|ok|okay|certo|fixe)\b[\s!.?]*$", re.I)

# Capitalised runs and known-shaped identifiers: "GTX 1660 Ti", "Ollama",
# "Visual Studio Code". Used only for the topic line, never as a claim.
_ENTITY = re.compile(r"\b(?:[A-ZÀ-Þ][\wÀ-ÿ]{2,}(?:\s+[A-ZÀ-Þ0-9][\wÀ-ÿ]*){0,3}|[A-Z]{2,}\s?\d{3,4}\s?\w{0,3})")

_SECTIONS = (
    ("facts", "Factos estabelecidos"),
    ("decisions", "Decisões"),
    ("goals", "Objetivos"),
    ("questions", "Perguntas em aberto"),
)
_TOPIC_LABEL = "Temas"
_HEADER = "Resumo da conversa até agora"

_STOP_ENTITIES = frozenset({
    "nano", "olá", "ola", "bom", "boa", "sim", "não", "nao", "obrigado",
    "the", "and", "que", "para", "com", "uma", "por", "mas", "isso",
})


@dataclass
class SummaryResult:
    text: str
    covered_through: int
    covered_messages: int
    facts: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    goals: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return not (self.facts or self.decisions or self.goals or self.questions or self.topics)


def should_compact(pending: list[dict]) -> bool:
    """Whether enough has happened since the last summary to justify redoing it."""
    if len(pending) <= KEEP_RECENT_MESSAGES:
        return False
    compactable = pending[:-KEEP_RECENT_MESSAGES]
    if len(compactable) >= COMPACT_AFTER_MESSAGES:
        return True
    return sum(len(str(m.get("content") or "")) for m in compactable) >= COMPACT_AFTER_CHARS


def _sentences(text: str) -> list[str]:
    body = str(text or "")
    if UNTRUSTED_BLOCK_OPEN in body or UNTRUSTED_BLOCK_CLOSE in body:
        # A message carrying fenced external content contributes nothing: the
        # fence is exactly the marker saying "this is not the user speaking".
        return []
    out: list[str] = []
    for raw in _SENTENCE_SPLIT.split(body):
        sentence = " ".join(raw.split())
        if len(sentence) < MIN_SENTENCE_CHARS or _SMALL_TALK.match(sentence):
            continue
        out.append(text_normalize.shorten(sentence, MAX_SENTENCE_CHARS))
    return out


def _add(bucket: list[str], sentence: str, seen: set[str]) -> None:
    key = text_normalize.normalize(sentence)[:120]
    if not key or key in seen or len(bucket) >= MAX_ITEMS_PER_SECTION:
        return
    seen.add(key)
    bucket.append(sentence)


def _topics(messages: list[dict], existing: list[str]) -> list[str]:
    found: list[str] = list(existing)
    seen = {text_normalize.normalize(topic) for topic in found}
    for message in messages:
        for match in _ENTITY.findall(str(message.get("content") or "")):
            topic = " ".join(str(match).split())
            key = text_normalize.normalize(topic)
            if not key or key in seen or key in _STOP_ENTITIES or len(key) < 3:
                continue
            seen.add(key)
            found.append(topic)
            if len(found) >= MAX_TOPICS:
                return found
    return found


def summarize(messages: list[dict], *, previous: str = "") -> SummaryResult:
    """Fold `messages` into (or onto) a summary. Deterministic and additive.

    `previous` is parsed back into its sections so an existing summary grows
    rather than being recomputed from a window that no longer contains the older
    turns. Passing "" and the full history rebuilds a summary from scratch,
    which is exactly what ``rebuild`` does.
    """
    carried = parse(previous)
    facts, decisions, goals = list(carried["facts"]), list(carried["decisions"]), list(carried["goals"])
    questions = list(carried["questions"])
    seen = {text_normalize.normalize(item)[:120]
            for group in (facts, decisions, goals, questions) for item in group}

    covered_through = 0
    covered = 0
    for message in messages:
        covered_through = max(covered_through, int(message.get("id") or 0))
        covered += 1
        # Only what the USER established. See the module docstring.
        if str(message.get("role")) != "user":
            continue
        for sentence in _sentences(message.get("content")):
            if memory_safety.secret_reason(sentence):
                continue
            if sentence.rstrip().endswith("?"):
                _add(questions, sentence, seen)
            elif _DECISION.search(sentence):
                _add(decisions, sentence, seen)
            elif _GOAL.search(sentence):
                _add(goals, sentence, seen)
            elif _FACT.search(sentence):
                _add(facts, sentence, seen)

    topics = _topics(messages, carried["topics"])
    result = SummaryResult(
        text="", covered_through=covered_through, covered_messages=covered,
        facts=facts, decisions=decisions, goals=goals, questions=questions,
        topics=topics)
    result.text = render(result)
    return result


def rebuild(messages: list[dict]) -> SummaryResult:
    """Recompute a summary from source messages alone. The authority path.

    The stored summary is a cache. If it is ever lost, corrupted or distrusted,
    this reproduces it from the messages, which are never destroyed by
    compaction.
    """
    return summarize(messages, previous="")


def render(result: SummaryResult) -> str:
    """The stored text form. Parsed back by ``parse``; shown to the model as-is."""
    lines: list[str] = []
    for key, label in _SECTIONS:
        items = getattr(result, key)
        if not items:
            continue
        lines.append(f"{label}:")
        lines.extend(f"- {item}" for item in items[:MAX_ITEMS_PER_SECTION])
    if result.topics:
        lines.append(f"{_TOPIC_LABEL}: {', '.join(result.topics[:MAX_TOPICS])}")
    if not lines:
        return ""
    body = "\n".join(lines)
    if len(body) > MAX_SUMMARY_CHARS:
        body = body[:MAX_SUMMARY_CHARS].rsplit("\n", 1)[0]
    return f"{_HEADER}:\n{body}"


def parse(text: str) -> dict[str, list[str]]:
    """Read a rendered summary back into its sections. The inverse of ``render``."""
    out: dict[str, list[str]] = {key: [] for key, _ in _SECTIONS}
    out["topics"] = []
    current: str | None = None
    labels = {label: key for key, label in _SECTIONS}
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        heading = line[:-1] if line.endswith(":") else line
        if heading in labels:
            current = labels[heading]
            continue
        if line.startswith(f"{_TOPIC_LABEL}:"):
            out["topics"] = [part.strip() for part
                             in line.split(":", 1)[1].split(",") if part.strip()]
            current = None
            continue
        if line.startswith("- ") and current:
            out[current].append(line[2:].strip())
    return out


__all__ = [
    "COMPACT_AFTER_CHARS",
    "COMPACT_AFTER_MESSAGES",
    "KEEP_RECENT_MESSAGES",
    "MAX_SUMMARY_CHARS",
    "SummaryResult",
    "parse",
    "rebuild",
    "render",
    "should_compact",
    "summarize",
]
