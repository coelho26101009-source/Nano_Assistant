"""Shared text normalisation for Nano's memory, retrieval and knowledge graph.

Everything here exists so that the same sentence written two ways is treated as
the same sentence. Portuguese makes that non-trivial: "GTX 1660 Ti" and "gtx
1660 ti" are one fact, "memória" and "memoria" are one word, and the user types
both spellings freely. Deduplication, slug identity and lexical scoring all key
on the values produced here, so they must be stable and boring.

Deliberately no stemmer and no stop-word list beyond a tiny Portuguese core: a
stemmer is a dependency and a source of surprises, and an aggressive stop list
throws away exactly the short words ("Ti", "PC", "RAM") that carry the meaning
in this domain.
"""
from __future__ import annotations

import re
import unicodedata

_WHITESPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]+")

#: Words too common in Portuguese and English to carry retrieval signal. Kept
#: short on purpose — every entry here is a word the search can no longer find.
STOP_WORDS: frozenset[str] = frozenset({
    "a", "à", "as", "ao", "aos", "o", "os", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "por", "para", "pra", "com", "sem", "sobre", "entre", "que", "quem",
    "e", "ou", "mas", "se", "sim", "nao", "não", "ja", "já", "muito",
    "mais", "menos", "meu", "minha", "meus", "minhas", "teu", "tua",
    "isso", "isto", "aquilo", "ele", "ela", "eles", "elas", "eu", "tu",
    "the", "and", "or", "of", "to", "in", "on", "at", "for", "is", "are",
    "was", "were", "be", "it", "this", "that", "with", "from", "my", "you",
})


def strip_accents(text: str) -> str:
    """Fold accents away: 'memória' and 'memoria' must match."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")


def normalize(text: str) -> str:
    """Case-folded, accent-folded, whitespace-collapsed. The dedup key."""
    return _WHITESPACE.sub(" ", strip_accents(str(text or "")).lower()).strip()


def tokens(text: str, *, keep_stop_words: bool = False) -> list[str]:
    """Comparable word tokens, in order, duplicates preserved."""
    found = _TOKEN.findall(normalize(text))
    if keep_stop_words:
        return found
    return [token for token in found if token not in STOP_WORDS and len(token) > 1]


def token_set(text: str) -> set[str]:
    return set(tokens(text))


def overlap_score(query: str, candidate: str) -> float:
    """Fraction of the query's meaningful words present in the candidate.

    Asymmetric on purpose: a long note that happens to contain every word of a
    short question is a good answer to it, and dividing by the candidate's own
    length would punish exactly that.
    """
    wanted = token_set(query)
    if not wanted:
        return 0.0
    present = wanted & token_set(candidate)
    return len(present) / len(wanted)


def slugify(text: str, *, max_length: int = 80) -> str:
    """A stable identity for a knowledge node title."""
    base = "-".join(_TOKEN.findall(normalize(text)))
    return base[:max_length] or "sem-titulo"


def shorten(text: str, limit: int) -> str:
    """Trim to `limit` characters on a word boundary where one is close by."""
    value = _WHITESPACE.sub(" ", str(text or "")).strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip(" ,.;:") + "…"


def estimate_tokens(text: str) -> int:
    """Approximate model-token cost of a string.

    The same ~3.6 characters-per-token estimate the Brain uses for its history
    budget (see Brain._estimate_tokens), repeated here rather than imported to
    keep this module free of Brain imports. Pessimistic by design: erring high
    keeps the composed context inside the provider's window.
    """
    return int(len(str(text or "")) / 3.6) + 2


__all__ = [
    "STOP_WORDS",
    "estimate_tokens",
    "normalize",
    "overlap_score",
    "shorten",
    "slugify",
    "strip_accents",
    "token_set",
    "tokens",
]
