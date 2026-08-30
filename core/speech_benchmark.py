"""Corpus and scoring for the local Portuguese speech-accuracy benchmark.

This module is deliberately PURE: it has no microphone, no model, no network
and no filesystem side effects. Everything here can be imported and tested on a
machine with neither PyAudio nor faster-whisper installed, which is exactly how
the test suite exercises it.

The recording and model-running half lives in
``scripts/speech_accuracy_benchmark.py``.

WHY THIS EXISTS
---------------
Speech CAPTURE is solved: Ctrl+Shift+Space records reliably and the adaptive
gate no longer rejects a real voice. What is not solved is TRANSCRIPTION.
faster-whisper ``tiny``, forced to Portuguese, turns "Ola Nano, tudo bem?" into
"Alana no tudo bem" and "tudo bem na no?". Those are decoding errors, not gate
errors, and no threshold change can fix them.

The only honest way to choose a replacement is to measure the models on THIS
user's voice, on THIS microphone, over the SAME recordings. That is what the
corpus and the metrics below are for.

SCORING RULES (chosen on purpose)
---------------------------------
* Normalisation folds case, punctuation and repeated whitespace, and NOTHING
  else. Accents are preserved: "pronuncia" is not "pronuncia" with an accent,
  and pretending otherwise would hide a real Portuguese error.
* No semantic rewriting, no fuzzy matching, no post-correction. The benchmark
  must report what Whisper actually heard.
* Critical-entity scoring is separate from WER because it fails differently.
  "Abre o Spotifai" has a perfectly respectable WER and is still a total
  failure: the entity Nano would have to act on is gone. Only spellings that
  denote the SAME entity are accepted, and every accepted spelling is declared
  in the corpus below, never inferred at scoring time.
"""
from __future__ import annotations

import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

# --------------------------------------------------------------------------
#  Normalisation
# --------------------------------------------------------------------------

# Anything that is not a word character or whitespace becomes a space. \w is
# Unicode-aware, so accented vowels survive and "ouvir-me" becomes "ouvir me"
# on BOTH sides of the comparison.
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def normalize_for_scoring(text: str) -> str:
    """Fold case, punctuation and whitespace. Accents are LEFT ALONE.

    Deliberately the smallest normalisation that still lets a spoken sentence
    match a written one. Stripping accents as well would make an unaccented
    guess score as a perfect hit, which is precisely the class of Portuguese
    error this benchmark exists to detect.
    """
    lowered = unicodedata.normalize("NFC", str(text or "")).strip().lower()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", lowered)).strip()


def strip_accents(text: str) -> str:
    """Accent-free form. Used ONLY for a secondary, clearly labelled metric."""
    decomposed = unicodedata.normalize("NFD", str(text or ""))
    return unicodedata.normalize(
        "NFC", "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    )


# --------------------------------------------------------------------------
#  Corpus
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Keyword:
    """One entity whose recognition matters more than its word error rate.

    ``variants`` holds ALTERNATIVE SPELLINGS OF THE SAME ENTITY -- orthographic
    only. "VSCode" is VS Code written without a space; "Spotifai" is not
    Spotify, and nothing like it will ever be listed here. Every variant is
    written down in this file so the report can be audited.
    """

    canonical: str
    variants: tuple[str, ...] = ()

    def accepted_forms(self) -> tuple[str, ...]:
        forms = [normalize_for_scoring(self.canonical)]
        forms.extend(normalize_for_scoring(v) for v in self.variants)
        return tuple(dict.fromkeys(f for f in forms if f))

    def found_in(self, normalized_hypothesis: str) -> bool:
        """True when any accepted spelling appears as whole words."""
        haystack = f" {normalized_hypothesis} "
        return any(f" {form} " in haystack for form in self.accepted_forms())


@dataclass(frozen=True)
class Phrase:
    id: str
    category: str
    text: str
    keywords: tuple[Keyword, ...] = ()

    @property
    def reference(self) -> str:
        return normalize_for_scoring(self.text)


# Categories, so the report can say WHERE a model breaks down rather than only
# that it does.
CATEGORY_CONVERSATION = "A-conversa"
CATEGORY_SOFTWARE = "B-software"
CATEGORY_PC_CONTROL = "C-controlo-pc"
CATEGORY_NATURAL = "D-natural"

_NANO = Keyword("Nano")
_SPOTIFY = Keyword("Spotify")
_DISCORD = Keyword("Discord")
_GITHUB = Keyword("GitHub", ("Git Hub",))
_VSCODE_FULL = Keyword("Visual Studio Code")
_VSCODE_SHORT = Keyword("VS Code", ("VSCode", "V S Code"))
_WINDOWS = Keyword("Windows")
_GROQ = Keyword("Groq")
_OLLAMA = Keyword("Ollama")
_CLAUDE = Keyword("Claude")
_DOWNLOADS = Keyword("Downloads")


CORPUS: tuple[Phrase, ...] = (
    # --- A. Ordinary conversation with Nano -------------------------------
    Phrase("001", CATEGORY_CONVERSATION, "Olá Nano, tudo bem?", (_NANO,)),
    Phrase("002", CATEGORY_CONVERSATION, "Nano, estás a ouvir-me?", (_NANO,)),
    Phrase("003", CATEGORY_CONVERSATION, "Que horas são?"),
    Phrase("004", CATEGORY_CONVERSATION, "Como está o meu computador?"),
    Phrase("005", CATEGORY_CONVERSATION, "Conta-me uma curiosidade."),
    Phrase("006", CATEGORY_CONVERSATION, "O que posso fazer hoje?"),
    Phrase("007", CATEGORY_CONVERSATION, "Explica-me isto de forma simples."),
    Phrase("008", CATEGORY_CONVERSATION, "Nano, podes repetir mais devagar?", (_NANO,)),
    # --- B. Software and brand vocabulary ---------------------------------
    Phrase("009", CATEGORY_SOFTWARE, "Abre o Spotify.", (_SPOTIFY,)),
    Phrase("010", CATEGORY_SOFTWARE, "Abre o Discord.", (_DISCORD,)),
    Phrase("011", CATEGORY_SOFTWARE, "Abre o Visual Studio Code.", (_VSCODE_FULL,)),
    Phrase("012", CATEGORY_SOFTWARE, "Abre o VS Code.", (_VSCODE_SHORT,)),
    Phrase("013", CATEGORY_SOFTWARE, "Vai ao GitHub.", (_GITHUB,)),
    Phrase("014", CATEGORY_SOFTWARE, "O Ollama está a correr?", (_OLLAMA,)),
    Phrase("015", CATEGORY_SOFTWARE, "O Groq está disponível?", (_GROQ,)),
    Phrase("016", CATEGORY_SOFTWARE, "Atualiza o Windows.", (_WINDOWS,)),
    Phrase("017", CATEGORY_SOFTWARE, "Pergunta ao Claude e ao Nano.", (_CLAUDE, _NANO)),
    # --- C. Language a future PC-control layer will have to parse ---------
    #     TRANSCRIPTION ONLY. Nothing here is ever executed by the benchmark.
    Phrase("018", CATEGORY_PC_CONTROL, "Abre a calculadora."),
    Phrase("019", CATEGORY_PC_CONTROL, "Abre o explorador de ficheiros."),
    Phrase("020", CATEGORY_PC_CONTROL, "Aumenta o volume."),
    Phrase("021", CATEGORY_PC_CONTROL, "Baixa o volume."),
    Phrase("022", CATEGORY_PC_CONTROL, "Mostra as janelas abertas."),
    Phrase("023", CATEGORY_PC_CONTROL, "Procura o ficheiro relatório."),
    Phrase("024", CATEGORY_PC_CONTROL, "Abre a pasta Downloads.", (_DOWNLOADS,)),
    Phrase("025", CATEGORY_PC_CONTROL, "Minimiza esta janela."),
    # --- D. Longer, natural Portuguese ------------------------------------
    Phrase("026", CATEGORY_NATURAL,
           "Explica-me, por favor, como funciona a memória do computador."),
    Phrase("027", CATEGORY_NATURAL,
           "Hoje está um dia bonito, mas prefiro ficar a trabalhar no projeto."),
    Phrase("028", CATEGORY_NATURAL,
           "Preciso de organizar os ficheiros antigos antes de instalar o programa novo."),
    Phrase("029", CATEGORY_NATURAL,
           "Quando terminares esta tarefa, avisa-me e desliga o som das notificações."),
    # Chosen on purpose: "abrir" and "apagar" are the pair a PC-control layer
    # must never confuse, so the benchmark records the user saying both.
    Phrase("030", CATEGORY_NATURAL,
           "A diferença entre abrir e apagar é enorme, portanto tem cuidado."),
)


def corpus_keywords() -> tuple[Keyword, ...]:
    """Every distinct critical entity in the corpus, in first-seen order."""
    seen: dict[str, Keyword] = {}
    for phrase in CORPUS:
        for keyword in phrase.keywords:
            seen.setdefault(keyword.canonical, keyword)
    return tuple(seen.values())


#: Short Portuguese hint tested in the vocabulary experiment (Part 7).
#: Kept to one sentence on purpose. faster-whisper feeds ``initial_prompt`` to
#: the decoder as preceding context, so a long prompt both costs tokens and
#: pulls the transcript towards the prompt's own wording.
VOCABULARY_PROMPT = (
    "Vocabulário: Nano, Spotify, Discord, GitHub, Visual Studio Code, "
    "VS Code, Windows, Groq, Ollama, Claude."
)


# --------------------------------------------------------------------------
#  Edit distance and alignment
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EditOps:
    substitutions: int
    deletions: int
    insertions: int

    @property
    def total(self) -> int:
        return self.substitutions + self.deletions + self.insertions


def _distance_matrix(reference: Sequence[str], hypothesis: Sequence[str]) -> list[list[int]]:
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    matrix = [[0] * cols for _ in range(rows)]
    for i in range(1, rows):
        matrix[i][0] = i
    for j in range(1, cols):
        matrix[0][j] = j
    for i in range(1, rows):
        ref_token = reference[i - 1]
        row, prev = matrix[i], matrix[i - 1]
        for j in range(1, cols):
            if ref_token == hypothesis[j - 1]:
                row[j] = prev[j - 1]
            else:
                row[j] = 1 + min(prev[j - 1], prev[j], row[j - 1])
    return matrix


def edit_ops(reference: Sequence[str], hypothesis: Sequence[str]) -> EditOps:
    """Substitutions/deletions/insertions turning ``reference`` into ``hypothesis``."""
    matrix = _distance_matrix(reference, hypothesis)
    i, j = len(reference), len(hypothesis)
    subs = dels = ins = 0
    while i > 0 or j > 0:
        if (i > 0 and j > 0 and reference[i - 1] == hypothesis[j - 1]
                and matrix[i][j] == matrix[i - 1][j - 1]):
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and matrix[i][j] == matrix[i - 1][j - 1] + 1:
            subs += 1
            i, j = i - 1, j - 1
        elif i > 0 and matrix[i][j] == matrix[i - 1][j] + 1:
            dels += 1
            i -= 1
        else:
            ins += 1
            j -= 1
    return EditOps(subs, dels, ins)


def align(reference: Sequence[str], hypothesis: Sequence[str]) -> list[tuple[str, str, str]]:
    """Word-level alignment as (op, reference_token, hypothesis_token).

    ``op`` is one of "equal", "sub", "del", "ins". This is what makes the wrong
    words easy to inspect in the Markdown report: a raw pair of sentences hides
    which word moved and which word was invented.
    """
    matrix = _distance_matrix(reference, hypothesis)
    i, j = len(reference), len(hypothesis)
    out: list[tuple[str, str, str]] = []
    while i > 0 or j > 0:
        if (i > 0 and j > 0 and reference[i - 1] == hypothesis[j - 1]
                and matrix[i][j] == matrix[i - 1][j - 1]):
            out.append(("equal", reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and j > 0 and matrix[i][j] == matrix[i - 1][j - 1] + 1:
            out.append(("sub", reference[i - 1], hypothesis[j - 1]))
            i, j = i - 1, j - 1
        elif i > 0 and matrix[i][j] == matrix[i - 1][j] + 1:
            out.append(("del", reference[i - 1], ""))
            i -= 1
        else:
            out.append(("ins", "", hypothesis[j - 1]))
            j -= 1
    out.reverse()
    return out


def render_diff(reference_text: str, hypothesis_text: str) -> str:
    """One-line, human-readable word diff of two ALREADY NORMALISED strings."""
    ref, hyp = reference_text.split(), hypothesis_text.split()
    parts: list[str] = []
    for op, r, h in align(ref, hyp):
        if op == "equal":
            parts.append(r)
        elif op == "sub":
            parts.append(f"[{r} -> {h}]")
        elif op == "del":
            parts.append(f"[{r} -> _]")
        else:
            parts.append(f"[_ -> {h}]")
    return " ".join(parts) if parts else "(vazio)"


# --------------------------------------------------------------------------
#  Per-phrase scoring
# --------------------------------------------------------------------------


@dataclass
class PhraseScore:
    phrase_id: str
    category: str
    expected: str
    expected_normalized: str
    raw: str
    normalized: str
    ref_words: int
    ref_chars: int
    word_ops: EditOps
    char_ops: EditOps
    exact: bool
    exact_accentless: bool
    keywords: tuple[tuple[str, bool], ...]
    diff: str
    latency_seconds: float | None = None
    audio_seconds: float | None = None
    error: str | None = None

    @property
    def wer(self) -> float:
        return (self.word_ops.total / self.ref_words) if self.ref_words else 0.0

    @property
    def cer(self) -> float:
        return (self.char_ops.total / self.ref_chars) if self.ref_chars else 0.0

    @property
    def real_time_factor(self) -> float | None:
        if not self.latency_seconds or not self.audio_seconds:
            return None
        return self.latency_seconds / self.audio_seconds

    def to_dict(self) -> dict:
        return {
            "phrase_id": self.phrase_id,
            "category": self.category,
            "expected": self.expected,
            "expected_normalized": self.expected_normalized,
            "raw": self.raw,
            "normalized": self.normalized,
            "wer": round(self.wer, 4),
            "cer": round(self.cer, 4),
            "exact": self.exact,
            "exact_accentless": self.exact_accentless,
            "word_ops": {
                "substitutions": self.word_ops.substitutions,
                "deletions": self.word_ops.deletions,
                "insertions": self.word_ops.insertions,
            },
            "ref_words": self.ref_words,
            "ref_chars": self.ref_chars,
            "keywords": [{"keyword": k, "found": found} for k, found in self.keywords],
            "diff": self.diff,
            "latency_seconds": (round(self.latency_seconds, 4)
                                if self.latency_seconds is not None else None),
            "audio_seconds": (round(self.audio_seconds, 3)
                              if self.audio_seconds is not None else None),
            "real_time_factor": (round(self.real_time_factor, 4)
                                 if self.real_time_factor is not None else None),
            "error": self.error,
        }


def score_phrase(
    phrase: Phrase,
    raw_transcript: str,
    *,
    latency_seconds: float | None = None,
    audio_seconds: float | None = None,
    error: str | None = None,
) -> PhraseScore:
    """Score ONE transcript against ONE corpus phrase. No correction, ever.

    A failed transcription is scored as an empty hypothesis rather than being
    dropped: a model that crashes on a phrase must not come out looking better
    than one that merely got it wrong.
    """
    expected_norm = phrase.reference
    hypothesis_norm = normalize_for_scoring(raw_transcript)

    ref_words = expected_norm.split()
    hyp_words = hypothesis_norm.split()
    ref_chars = list(expected_norm)
    hyp_chars = list(hypothesis_norm)

    return PhraseScore(
        phrase_id=phrase.id,
        category=phrase.category,
        expected=phrase.text,
        expected_normalized=expected_norm,
        raw=str(raw_transcript or ""),
        normalized=hypothesis_norm,
        ref_words=len(ref_words),
        ref_chars=len(ref_chars),
        word_ops=edit_ops(ref_words, hyp_words),
        char_ops=edit_ops(ref_chars, hyp_chars),
        exact=hypothesis_norm == expected_norm,
        exact_accentless=(strip_accents(hypothesis_norm) == strip_accents(expected_norm)),
        keywords=tuple((k.canonical, k.found_in(hypothesis_norm)) for k in phrase.keywords),
        diff=render_diff(expected_norm, hypothesis_norm),
        latency_seconds=latency_seconds,
        audio_seconds=audio_seconds,
        error=error,
    )


# --------------------------------------------------------------------------
#  Per-configuration aggregation
# --------------------------------------------------------------------------


@dataclass
class ConfigSummary:
    label: str
    model: str
    device: str
    compute_type: str
    language: str | None
    initial_prompt: str | None
    scores: list[PhraseScore] = field(default_factory=list)
    load_seconds: float | None = None
    first_transcription_seconds: float | None = None
    device_used: str | None = None
    ram_baseline_mb: float | None = None
    ram_after_load_mb: float | None = None
    ram_peak_mb: float | None = None
    vram_baseline_mb: float | None = None
    vram_peak_mb: float | None = None
    vram_note: str | None = None
    available: bool = True
    error: str | None = None

    # ---------------------------------------------------------- accuracy

    @property
    def wer(self) -> float:
        """Micro-averaged: total word edits over total reference words."""
        edits = sum(s.word_ops.total for s in self.scores)
        words = sum(s.ref_words for s in self.scores)
        return (edits / words) if words else 0.0

    @property
    def cer(self) -> float:
        edits = sum(s.char_ops.total for s in self.scores)
        chars = sum(s.ref_chars for s in self.scores)
        return (edits / chars) if chars else 0.0

    @property
    def wer_mean_per_phrase(self) -> float:
        return statistics.fmean([s.wer for s in self.scores]) if self.scores else 0.0

    @property
    def exact_rate(self) -> float:
        return (sum(1 for s in self.scores if s.exact) / len(self.scores)) if self.scores else 0.0

    @property
    def exact_accentless_rate(self) -> float:
        if not self.scores:
            return 0.0
        return sum(1 for s in self.scores if s.exact_accentless) / len(self.scores)

    @property
    def keyword_total(self) -> int:
        return sum(len(s.keywords) for s in self.scores)

    @property
    def keyword_hits(self) -> int:
        return sum(1 for s in self.scores for _, found in s.keywords if found)

    @property
    def keyword_accuracy(self) -> float:
        total = self.keyword_total
        return (self.keyword_hits / total) if total else 0.0

    def category_wer(self) -> dict[str, float]:
        buckets: dict[str, tuple[int, int]] = {}
        for score in self.scores:
            edits, words = buckets.get(score.category, (0, 0))
            buckets[score.category] = (edits + score.word_ops.total, words + score.ref_words)
        return {c: (e / w if w else 0.0) for c, (e, w) in sorted(buckets.items())}

    def keyword_misses(self) -> list[tuple[str, str, str, str]]:
        """(keyword, phrase_id, expected, heard) for every missed entity."""
        out: list[tuple[str, str, str, str]] = []
        for score in self.scores:
            for keyword, found in score.keywords:
                if not found:
                    out.append((keyword, score.phrase_id, score.expected, score.raw))
        return out

    # ------------------------------------------------------------ latency

    def _latencies(self, *, warm_only: bool) -> list[float]:
        values = [s.latency_seconds for s in self.scores if s.latency_seconds is not None]
        return values[1:] if warm_only else values

    @property
    def warm_mean(self) -> float | None:
        values = self._latencies(warm_only=True)
        return statistics.fmean(values) if values else None

    @property
    def warm_median(self) -> float | None:
        values = self._latencies(warm_only=True)
        return statistics.median(values) if values else None

    @property
    def warm_p95(self) -> float | None:
        """p95 of warm latencies. None below 10 samples, where it is noise."""
        values = sorted(self._latencies(warm_only=True))
        if len(values) < 10:
            return None
        index = min(len(values) - 1, int(round(0.95 * (len(values) - 1))))
        return values[index]

    @property
    def mean_real_time_factor(self) -> float | None:
        factors = [s.real_time_factor for s in self.scores if s.real_time_factor is not None]
        return statistics.fmean(factors) if factors else None

    @property
    def failed_phrases(self) -> int:
        return sum(1 for s in self.scores if s.error)

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "language": self.language,
            "initial_prompt": self.initial_prompt,
            "available": self.available,
            "error": self.error,
            "accuracy": {
                "wer": round(self.wer, 4),
                "cer": round(self.cer, 4),
                "wer_mean_per_phrase": round(self.wer_mean_per_phrase, 4),
                "exact_rate": round(self.exact_rate, 4),
                "exact_accentless_rate": round(self.exact_accentless_rate, 4),
                "keyword_accuracy": round(self.keyword_accuracy, 4),
                "keyword_hits": self.keyword_hits,
                "keyword_total": self.keyword_total,
                "category_wer": {c: round(v, 4) for c, v in self.category_wer().items()},
            },
            "performance": {
                "load_seconds": (round(self.load_seconds, 3)
                                 if self.load_seconds is not None else None),
                "first_transcription_seconds": (round(self.first_transcription_seconds, 3)
                                                if self.first_transcription_seconds is not None
                                                else None),
                "warm_mean_seconds": round(self.warm_mean, 3) if self.warm_mean else None,
                "warm_median_seconds": round(self.warm_median, 3) if self.warm_median else None,
                "warm_p95_seconds": round(self.warm_p95, 3) if self.warm_p95 else None,
                "mean_real_time_factor": (round(self.mean_real_time_factor, 3)
                                          if self.mean_real_time_factor else None),
                "device_used": self.device_used,
                "ram_baseline_mb": self.ram_baseline_mb,
                "ram_after_load_mb": self.ram_after_load_mb,
                "ram_peak_mb": self.ram_peak_mb,
                "vram_baseline_mb": self.vram_baseline_mb,
                "vram_peak_mb": self.vram_peak_mb,
                "vram_note": self.vram_note,
                "failed_phrases": self.failed_phrases,
            },
            "phrases": [s.to_dict() for s in self.scores],
        }


# --------------------------------------------------------------------------
#  Session assembly and reporting
# --------------------------------------------------------------------------


def build_results(
    *,
    session_id: str,
    started_at: str,
    finished_at: str,
    environment: dict,
    capture: dict,
    summaries: Iterable[ConfigSummary],
) -> dict:
    ordered = list(summaries)
    return {
        "schema": "nano.speech_benchmark/1",
        "session_id": session_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "environment": environment,
        "capture": capture,
        "corpus": [
            {
                "id": p.id,
                "category": p.category,
                "text": p.text,
                "normalized": p.reference,
                "keywords": [k.canonical for k in p.keywords],
            }
            for p in CORPUS
        ],
        "configs": [s.to_dict() for s in ordered],
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _secs(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}s"


def _mb(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f} MB"


def render_report(results: dict) -> str:
    """Human-readable Markdown for the JSON produced by :func:`build_results`."""
    lines: list[str] = []
    add = lines.append

    add("# Nano — Speech Accuracy Benchmark")
    add("")
    add(f"- Session: `{results.get('session_id')}`")
    add(f"- Started: {results.get('started_at')}")
    add(f"- Finished: {results.get('finished_at')}")

    env = results.get("environment") or {}
    for key in ("platform", "python", "faster_whisper", "ctranslate2", "gpu", "cuda_devices"):
        if env.get(key):
            add(f"- {key}: {env[key]}")

    capture = results.get("capture") or {}
    add("")
    add("## Capture")
    add("")
    add("The audio was recorded ONCE per phrase, with the same `AudioInputProvider`, "
        "the same device and the same format Nano uses in production. "
        "All configurations below were evaluated on exactly the same files.")
    add("")
    for key in ("device_index", "device_name", "sample_rate", "channels",
                "sample_width_bytes", "record_seconds", "phrases_recorded", "audio_dir"):
        if capture.get(key) is not None:
            add(f"- {key}: `{capture[key]}`")

    configs = results.get("configs") or []

    add("")
    add("## Summary")
    add("")
    add("| Model | Config | WER | CER | Exact | Entities | Warm median | Warm p95 | "
        "Cold load | 1st transcription | RAM peak | VRAM peak | Device |")
    add("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for cfg in configs:
        acc = cfg.get("accuracy") or {}
        perf = cfg.get("performance") or {}
        if not cfg.get("available", True):
            add(f"| {cfg.get('model')} | {cfg.get('label')} | UNAVAILABLE | - | - | - | - | - "
                f"| - | - | - | - | {cfg.get('error') or ''} |")
            continue
        add(
            f"| {cfg.get('model')} | {cfg.get('label')} "
            f"| {_pct(acc.get('wer'))} | {_pct(acc.get('cer'))} | {_pct(acc.get('exact_rate'))} "
            f"| {_pct(acc.get('keyword_accuracy'))} "
            f"({acc.get('keyword_hits')}/{acc.get('keyword_total')}) "
            f"| {_secs(perf.get('warm_median_seconds'))} "
            f"| {_secs(perf.get('warm_p95_seconds'))} "
            f"| {_secs(perf.get('load_seconds'))} "
            f"| {_secs(perf.get('first_transcription_seconds'))} "
            f"| {_mb(perf.get('ram_peak_mb'))} | {_mb(perf.get('vram_peak_mb'))} "
            f"| {perf.get('device_used') or '-'} |"
        )

    add("")
    add("## WER by category")
    add("")
    categories = sorted({c for cfg in configs
                         for c in ((cfg.get("accuracy") or {}).get("category_wer") or {})})
    if categories:
        add("| Config | " + " | ".join(categories) + " |")
        add("|---" * (len(categories) + 1) + "|")
        for cfg in configs:
            if not cfg.get("available", True):
                continue
            per = (cfg.get("accuracy") or {}).get("category_wer") or {}
            add(f"| {cfg.get('label')} | "
                + " | ".join(_pct(per.get(c)) for c in categories) + " |")

    add("")
    add("## Failed critical entities")
    add("")
    for cfg in configs:
        if not cfg.get("available", True):
            continue
        misses = [
            (kw["keyword"], p["phrase_id"], p["expected"], p["raw"])
            for p in cfg.get("phrases", [])
            for kw in p.get("keywords", [])
            if not kw.get("found")
        ]
        if not misses:
            add(f"- **{cfg.get('label')}**: no entity misses.")
            continue
        add("")
        add(f"### {cfg.get('label')}")
        add("")
        add("| Entity | Phrase | Expected | Heard |")
        add("|---|---|---|---|")
        for keyword, phrase_id, expected, raw in misses:
            add(f"| **{keyword}** | {phrase_id} | {expected} | `{raw or '(empty)'}` |")

    add("")
    add("## Errors by phrase")
    add("")
    add("Only phrases that were NOT exactly correct appear here. "
        "`[expected -> heard]` marks each swapped word.")
    for cfg in configs:
        if not cfg.get("available", True):
            add("")
            add(f"### {cfg.get('label')} — UNAVAILABLE")
            add("")
            add("```")
            add(str(cfg.get("error")))
            add("```")
            continue
        wrong = [p for p in cfg.get("phrases", []) if not p.get("exact")]
        add("")
        add(f"### {cfg.get('label')} — {len(wrong)} of {len(cfg.get('phrases', []))} with errors")
        add("")
        if not wrong:
            add("All phrases exact.")
            continue
        for p in wrong:
            add(f"- **{p['phrase_id']}** (WER {_pct(p['wer'])})")
            add(f"  - expected: `{p['expected_normalized']}`")
            add(f"  - heard:    `{p['normalized'] or '(empty)'}`")
            add(f"  - raw:      `{p['raw'] or '(empty)'}`")
            add(f"  - diff:     {p['diff']}")
            if p.get("error"):
                add(f"  - ERROR: `{p['error']}`")

    add("")
    add("## Notes")
    add("")
    add("- No audio left this machine. The benchmark does not contact Groq or "
        "any other cloud API, and does not execute any command.")
    add("- No transcript was corrected before being scored: the numbers "
        "above are what Whisper actually heard.")
    add("- Normalization ignores case, punctuation and repeated spaces. "
        "Accents are preserved.")
    add("")
    return "\n".join(lines)


__all__ = [
    "CATEGORY_CONVERSATION",
    "CATEGORY_NATURAL",
    "CATEGORY_PC_CONTROL",
    "CATEGORY_SOFTWARE",
    "CORPUS",
    "ConfigSummary",
    "EditOps",
    "Keyword",
    "Phrase",
    "PhraseScore",
    "VOCABULARY_PROMPT",
    "align",
    "build_results",
    "corpus_keywords",
    "edit_ops",
    "normalize_for_scoring",
    "render_diff",
    "render_report",
    "score_phrase",
    "strip_accents",
]
