"""Tests for the speech-accuracy benchmark infrastructure.

The benchmark is a MEASURING INSTRUMENT. If it lies -- by scoring a wrong
transcript as right, by silently letting two models see different audio, or by
losing a whole session because one model failed to load -- the production
decision it feeds is worthless. These tests exist to keep it honest.

They are behavioural: the real modules are imported and exercised, and the real
subprocess worker is run against real WAV files. Nothing here greps source
text, and nothing here needs a microphone.
"""
from __future__ import annotations

import json
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from core import speech_benchmark as sb

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "speech_accuracy_benchmark.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import speech_accuracy_benchmark as bench  # noqa: E402


# --------------------------------------------------------------------------
#  Corpus
# --------------------------------------------------------------------------


def test_corpus_has_between_25_and_30_phrases():
    assert 25 <= len(sb.CORPUS) <= 30


def test_corpus_phrase_ids_are_unique_and_sorted():
    ids = [p.id for p in sb.CORPUS]
    assert len(ids) == len(set(ids))
    assert ids == sorted(ids)


def test_corpus_covers_all_four_categories():
    categories = {p.category for p in sb.CORPUS}
    assert categories == {
        sb.CATEGORY_CONVERSATION,
        sb.CATEGORY_SOFTWARE,
        sb.CATEGORY_PC_CONTROL,
        sb.CATEGORY_NATURAL,
    }


@pytest.mark.parametrize("entity", [
    "Nano", "Spotify", "Discord", "GitHub", "VS Code", "Windows", "Groq", "Ollama", "Claude",
])
def test_every_required_critical_entity_is_in_the_corpus(entity):
    assert entity in {k.canonical for k in sb.corpus_keywords()}


def test_corpus_exercises_the_abrir_apagar_confusion():
    """The pair a PC-control layer must never mix up has to be recorded."""
    joined = " ".join(p.reference for p in sb.CORPUS)
    assert "abrir" in joined and "apagar" in joined


def test_vocabulary_prompt_is_short():
    """A long initial_prompt drags the transcript towards the prompt itself."""
    assert len(sb.VOCABULARY_PROMPT) < 200
    assert sb.VOCABULARY_PROMPT.count(".") <= 2


# --------------------------------------------------------------------------
#  Normalisation
# --------------------------------------------------------------------------


def test_normalization_folds_case_punctuation_and_whitespace():
    assert sb.normalize_for_scoring("  Olá,   NANO!!  Tudo   bem? ") == "olá nano tudo bem"


def test_normalization_preserves_accents():
    """Conservative on purpose: an unaccented guess is a real error."""
    assert sb.normalize_for_scoring("Pronúncia") == "pronúncia"
    assert sb.normalize_for_scoring("Pronuncia") != sb.normalize_for_scoring("Pronúncia")


def test_normalization_splits_hyphenated_words_on_both_sides():
    assert sb.normalize_for_scoring("ouvir-me") == sb.normalize_for_scoring("ouvir me")


def test_normalization_of_empty_and_none_is_empty():
    assert sb.normalize_for_scoring("") == ""
    assert sb.normalize_for_scoring(None) == ""


# --------------------------------------------------------------------------
#  WER / CER
# --------------------------------------------------------------------------


def test_wer_is_zero_for_a_perfect_transcript():
    phrase = sb.Phrase("x", "cat", "Abre o Spotify.")
    score = sb.score_phrase(phrase, "abre o spotify")
    assert score.wer == 0.0
    assert score.cer == 0.0
    assert score.exact is True


def test_wer_counts_one_substitution_out_of_three_words():
    phrase = sb.Phrase("x", "cat", "Abre o Spotify.")
    score = sb.score_phrase(phrase, "Abre o Discord")
    assert score.word_ops.substitutions == 1
    assert score.word_ops.deletions == 0
    assert score.word_ops.insertions == 0
    assert score.wer == pytest.approx(1 / 3)


def test_wer_counts_deletions_and_insertions():
    phrase = sb.Phrase("x", "cat", "abre o volume")
    deleted = sb.score_phrase(phrase, "abre volume")
    assert deleted.word_ops.deletions == 1
    assert deleted.wer == pytest.approx(1 / 3)

    inserted = sb.score_phrase(phrase, "abre o volume agora")
    assert inserted.word_ops.insertions == 1
    assert inserted.wer == pytest.approx(1 / 3)


def test_empty_transcript_scores_as_total_failure_not_as_perfect():
    phrase = sb.Phrase("x", "cat", "abre o spotify")
    score = sb.score_phrase(phrase, "")
    assert score.wer == 1.0
    assert score.cer == 1.0
    assert score.exact is False


def test_cer_measures_characters_including_the_accent():
    phrase = sb.Phrase("x", "cat", "pronúncia")
    score = sb.score_phrase(phrase, "pronuncia")
    assert score.cer == pytest.approx(1 / 9)
    assert score.exact is False
    # The accent-free rate is reported SEPARATELY and clearly labelled, so the
    # strict number is never quietly replaced by the lenient one.
    assert score.exact_accentless is True


def test_edit_ops_is_symmetric_in_total_distance():
    a, b = "um dois tres".split(), "um quatro tres cinco".split()
    assert sb.edit_ops(a, b).total == sb.edit_ops(b, a).total


def test_real_observed_failure_scores_badly():
    """The transcript that started this whole phase must not look acceptable."""
    phrase = next(p for p in sb.CORPUS if p.id == "001")   # "Olá Nano, tudo bem?"
    score = sb.score_phrase(phrase, "Alana no tudo bem")
    assert score.exact is False
    assert score.wer >= 0.5
    assert dict(score.keywords)["Nano"] is False


# --------------------------------------------------------------------------
#  Critical-entity scoring
# --------------------------------------------------------------------------


def test_keyword_miss_is_reported_even_when_wer_looks_fine():
    """The Spotify/Spotifai case from the brief, verbatim."""
    phrase = sb.Phrase("x", "cat", "Abre o Spotify.", (sb.Keyword("Spotify"),))
    score = sb.score_phrase(phrase, "Abre o Spotifai")
    assert score.wer <= 1 / 3          # WER alone would call this acceptable
    assert dict(score.keywords)["Spotify"] is False


def test_keyword_matching_ignores_case_and_punctuation():
    phrase = sb.Phrase("x", "cat", "Abre o Discord.", (sb.Keyword("Discord"),))
    assert dict(sb.score_phrase(phrase, "Abre o DISCORD!").keywords)["Discord"] is True


def test_keyword_accepts_only_declared_orthographic_variants():
    keyword = sb.Keyword("VS Code", ("VSCode",))
    assert keyword.found_in(sb.normalize_for_scoring("abre o vscode")) is True
    assert keyword.found_in(sb.normalize_for_scoring("abre o vs code")) is True
    assert keyword.found_in(sb.normalize_for_scoring("abre o vez code")) is False


def test_keyword_requires_whole_words_not_substrings():
    keyword = sb.Keyword("Nano")
    assert keyword.found_in("olá nano tudo bem") is True
    assert keyword.found_in("nanotecnologia avancada") is False


def test_no_phonetic_variant_is_ever_declared_for_a_brand():
    """Entity variants are orthographic. Nothing that merely SOUNDS alike."""
    forbidden = {"spotifai", "espotifai", "discorde", "grock", "guitube", "oi lama"}
    for keyword in sb.corpus_keywords():
        assert forbidden.isdisjoint(set(keyword.accepted_forms()))


# --------------------------------------------------------------------------
#  Aggregation
# --------------------------------------------------------------------------


def _summary_with(transcripts, latencies=None):
    phrases = list(sb.CORPUS)[: len(transcripts)]
    summary = sb.ConfigSummary(label="t", model="tiny", device="cpu",
                               compute_type="int8", language="pt", initial_prompt=None)
    for index, (phrase, text) in enumerate(zip(phrases, transcripts)):
        summary.scores.append(sb.score_phrase(
            phrase, text,
            latency_seconds=(latencies[index] if latencies else None),
            audio_seconds=7.0,
        ))
    return summary


def test_aggregate_wer_is_micro_averaged_over_all_words():
    summary = _summary_with([p.text for p in sb.CORPUS[:5]])
    assert summary.wer == 0.0
    assert summary.exact_rate == 1.0
    assert summary.keyword_accuracy == 1.0


def test_aggregate_counts_entity_hits_and_misses():
    summary = _summary_with(["Alana no tudo bem", "Nano, estás a ouvir-me?"])
    assert summary.keyword_total == 2
    assert summary.keyword_hits == 1
    assert summary.keyword_accuracy == pytest.approx(0.5)
    assert [m[0] for m in summary.keyword_misses()] == ["Nano"]


def test_warm_latency_excludes_the_first_transcription():
    """The first call carries lazy init; folding it in slanders the model."""
    summary = _summary_with([p.text for p in sb.CORPUS[:5]],
                            latencies=[9.0, 1.0, 1.0, 1.0, 1.0])
    assert summary.warm_median == pytest.approx(1.0)
    assert summary.warm_mean == pytest.approx(1.0)


def test_p95_is_withheld_below_ten_warm_samples():
    """Reporting a p95 from four points would be inventing precision."""
    assert _summary_with([p.text for p in sb.CORPUS[:5]],
                         latencies=[1, 2, 3, 4, 5]).warm_p95 is None
    twelve = _summary_with([p.text for p in sb.CORPUS[:12]],
                           latencies=list(range(1, 13)))
    assert twelve.warm_p95 is not None


def test_real_time_factor_uses_audio_duration():
    summary = _summary_with([sb.CORPUS[0].text], latencies=[3.5])
    assert summary.scores[0].real_time_factor == pytest.approx(0.5)


def test_failed_phrase_is_scored_not_skipped():
    """A model that crashes must not outrank one that merely got it wrong."""
    summary = sb.ConfigSummary(label="t", model="tiny", device="cpu",
                               compute_type="int8", language="pt", initial_prompt=None)
    summary.scores.append(sb.score_phrase(sb.CORPUS[0], "", error="RuntimeError: boom"))
    assert summary.failed_phrases == 1
    assert summary.wer == 1.0
    assert summary.exact_rate == 0.0


def test_category_wer_is_reported_per_category():
    summary = _summary_with([p.text for p in sb.CORPUS[:12]])
    per = summary.category_wer()
    assert sb.CATEGORY_CONVERSATION in per and sb.CATEGORY_SOFTWARE in per


# --------------------------------------------------------------------------
#  Output artefacts
# --------------------------------------------------------------------------


def _results_fixture():
    good = _summary_with([p.text for p in sb.CORPUS[:6]], latencies=[2.0, 1.0, 1.0, 1.0, 1.0, 1.0])
    good.label, good.load_seconds, good.first_transcription_seconds = "tiny/cpu-int8", 1.2, 2.0
    good.device_used, good.ram_peak_mb = "cpu", 512.0

    bad = _summary_with(["Alana no tudo bem"] + [p.text for p in sb.CORPUS[1:6]])
    bad.label, bad.model = "base/cpu-int8", "base"

    broken = sb.ConfigSummary(label="small/cuda-float16", model="small", device="cuda",
                              compute_type="float16", language="pt", initial_prompt=None)
    broken.available = False
    broken.error = "RuntimeError: no CUDA driver"

    return sb.build_results(
        session_id="20260823-120000",
        started_at="2026-08-23T12:00:00",
        finished_at="2026-08-23T12:10:00",
        environment={"platform": "Windows 11", "python": "3.12.10"},
        capture={"device_index": 1, "sample_rate": 16000, "channels": 1,
                 "record_seconds": 7, "phrases_recorded": 6},
        summaries=[good, bad, broken],
    )


def test_results_json_is_serialisable_and_carries_every_config():
    payload = _results_fixture()
    round_tripped = json.loads(json.dumps(payload, ensure_ascii=False))
    assert round_tripped["schema"] == "nano.speech_benchmark/1"
    assert [c["label"] for c in round_tripped["configs"]] == [
        "tiny/cpu-int8", "base/cpu-int8", "small/cuda-float16"]
    assert round_tripped["configs"][2]["available"] is False


def test_results_json_keeps_both_raw_and_normalized_transcripts():
    payload = _results_fixture()
    phrase = payload["configs"][1]["phrases"][0]
    assert phrase["raw"] == "Alana no tudo bem"
    assert phrase["normalized"] == "alana no tudo bem"
    assert phrase["expected_normalized"] == "olá nano tudo bem"


def test_report_markdown_has_a_summary_row_per_config():
    report = sb.render_report(_results_fixture())
    assert "| Model | Config |" in report
    assert "tiny/cpu-int8" in report
    assert "base/cpu-int8" in report


def test_report_marks_an_unavailable_model_without_losing_the_session():
    report = sb.render_report(_results_fixture())
    assert "UNAVAILABLE" in report
    assert "no CUDA driver" in report
    # ...and the configurations that DID run are still fully reported.
    assert "tiny/cpu-int8" in report


def test_report_shows_the_wrong_words_and_the_missed_entity():
    report = sb.render_report(_results_fixture())
    assert "[olá -> alana]" in report
    assert "[nano -> no]" in report
    assert "**Nano**" in report          # the entity-miss table


def test_report_states_the_privacy_guarantees():
    report = sb.render_report(_results_fixture())
    assert "Groq" in report
    assert "corrected" in report          # no post-correction before scoring


# --------------------------------------------------------------------------
#  Candidate matrix
# --------------------------------------------------------------------------


PROD_STT = {"provider": "local", "model": "tiny", "device": "cpu",
            "compute_type": "int8", "language": "pt-PT"}


def test_baseline_covers_tiny_base_and_small_on_cpu():
    labels = [c["label"] for c in bench.build_candidates(
        PROD_STT, models=("tiny", "base", "small"),
        use_gpu=False, include_auto_language=False)]
    assert labels == ["tiny/cpu-int8", "base/cpu-int8", "small/cpu-int8"]


def test_every_baseline_candidate_gets_the_same_decoding_settings():
    """No model may be handed a better decoder than another."""
    candidates = bench.build_candidates(
        PROD_STT, models=("tiny", "base", "small"),
        use_gpu=True, include_auto_language=False)
    assert {c["language"] for c in candidates} == {"pt"}
    assert {c["vad_filter"] for c in candidates} == {True}
    assert {c["initial_prompt"] for c in candidates} == {None}


def test_candidates_inherit_the_production_language_and_compute_type():
    candidate = bench.build_candidates(
        PROD_STT, models=("tiny",), use_gpu=False, include_auto_language=False)[0]
    assert candidate["language"] == "pt"          # from "pt-PT", exactly as core.voice does
    assert candidate["compute_type"] == "int8"
    assert candidate["device"] == "cpu"


def test_auto_language_control_is_the_only_language_variation():
    candidates = bench.build_candidates(
        PROD_STT, models=("tiny",), use_gpu=False, include_auto_language=True)
    assert [c["language"] for c in candidates] == ["pt", None]


def test_vocabulary_experiment_targets_the_best_configs_by_entity_accuracy():
    strong = _summary_with([p.text for p in sb.CORPUS[:6]])
    strong.label, strong.model = "small/cpu-int8", "small"
    weak = _summary_with(["Alana no tudo bem"] + [p.text for p in sb.CORPUS[1:6]])
    weak.label, weak.model = "tiny/cpu-int8", "tiny"

    picked = bench.vocabulary_candidates([weak, strong], limit=1)
    assert len(picked) == 1
    assert picked[0]["model"] == "small"
    assert picked[0]["label"].endswith("+vocab")
    assert picked[0]["initial_prompt"] == sb.VOCABULARY_PROMPT


def test_vocabulary_experiment_never_reruns_a_prompted_config():
    prompted = _summary_with([p.text for p in sb.CORPUS[:6]])
    prompted.initial_prompt = sb.VOCABULARY_PROMPT
    assert bench.vocabulary_candidates([prompted]) == []


def test_vocabulary_experiment_skips_unavailable_configs():
    broken = sb.ConfigSummary(label="small/cpu-int8", model="small", device="cpu",
                              compute_type="int8", language="pt", initial_prompt=None)
    broken.available = False
    assert bench.vocabulary_candidates([broken]) == []


# --------------------------------------------------------------------------
#  GPU availability must be MEASURED, not assumed
# --------------------------------------------------------------------------


def test_gpu_probe_returns_a_verdict_and_a_reason():
    """Never raises, and always says WHY -- the reason goes into the report."""
    usable, reason = bench.gpu_is_usable(timeout=240)
    assert isinstance(usable, bool)
    assert isinstance(reason, str) and reason


def test_gpu_is_rejected_when_the_probe_hangs(monkeypatch):
    """The failure mode actually observed here: native code that never returns.

    On this machine ctranslate2 reports a CUDA device and WhisperModel
    constructs fine, because cuBLAS is loaded lazily. The first real inference
    then hangs inside native code instead of raising. Counting devices would
    have put CUDA rows in the matrix and stranded the user mid-benchmark.
    """
    monkeypatch.setattr(bench.subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(
        subprocess.TimeoutExpired(cmd, kw.get("timeout", 1))))
    usable, reason = bench.gpu_is_usable(timeout=1)
    assert usable is False
    assert "bloqueou" in reason


def test_gpu_is_rejected_when_the_probe_cannot_transcribe(monkeypatch, tmp_path):
    def fake_run(cmd, **kwargs):
        Path(cmd[cmd.index("--gpu-probe") + 1]).write_text(
            json.dumps({"ok": False, "error": "RuntimeError: cublas64_12.dll not found"}),
            encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bench.subprocess, "run", fake_run)
    usable, reason = bench.gpu_is_usable()
    assert usable is False
    assert "cublas" in reason


def test_gpu_probe_runs_without_the_vad_that_would_fake_a_pass(tmp_path):
    """A silence filter would drop the probe tone and never reach the encoder.

    Behavioural: run the real probe subprocess and require a definite verdict
    with a real error message when it fails -- which is only possible if the
    encoder was actually reached.
    """
    out = tmp_path / "probe.json"
    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--gpu-probe", str(out)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    assert process.returncode == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["ok"] is True or payload["error"]


def test_no_cuda_row_is_offered_when_the_gpu_is_not_usable():
    labels = [c["label"] for c in bench.build_candidates(
        PROD_STT, models=("tiny", "base", "small"),
        use_gpu=False, include_auto_language=False)]
    assert not any("cuda" in label for label in labels)


# --------------------------------------------------------------------------
#  Identical audio for every candidate
# --------------------------------------------------------------------------


def _write_corpus_audio(directory: Path, count: int, sample_rate: int = 16000) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    recorded = []
    for index, phrase in enumerate(sb.CORPUS[:count]):
        path = directory / f"phrase_{phrase.id}.wav"
        path.write_bytes(bench.synthetic_wav_bytes(0.5, sample_rate,
                                                   tone_hz=200 + 20 * index))
        recorded.append({"phrase_id": phrase.id, "path": str(path),
                         "audio_seconds": 0.5, "rms": 100.0})
    return recorded


def test_every_candidate_receives_byte_identical_audio(tmp_path, monkeypatch):
    """THE fairness invariant: one recording, many models.

    The subprocess is stubbed so the assertion is about what each candidate was
    HANDED, not about what any model made of it. The hashes are taken from the
    files on disk after every candidate has run, so a candidate that rewrote or
    resampled its input would be caught.
    """
    import hashlib

    recorded = _write_corpus_audio(tmp_path / "audio", 4)
    before = {r["phrase_id"]: hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()
              for r in recorded}

    handed: list[list[str]] = []

    def fake_run(cmd, **kwargs):
        job = json.loads(Path(cmd[cmd.index("--worker") + 1]).read_text(encoding="utf-8"))
        handed.append([entry["path"] for entry in job["audio"]])
        Path(cmd[cmd.index("--worker-output") + 1]).write_text(json.dumps({
            "ok": True, "load_seconds": 0.1, "device_used": job["device"],
            "transcriptions": [{"phrase_id": e["phrase_id"], "text": "x",
                                "seconds": 0.05, "error": None} for e in job["audio"]],
        }), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bench.subprocess, "run", fake_run)

    phrases_by_id = {p.id: p for p in sb.CORPUS}
    work = tmp_path / "work"
    work.mkdir()
    for candidate in bench.build_candidates(PROD_STT, models=("tiny", "base", "small"),
                                            use_gpu=False, include_auto_language=True):
        bench.run_candidate(candidate, recorded, work_dir=work, phrases_by_id=phrases_by_id)

    assert len(handed) == 6
    assert all(paths == handed[0] for paths in handed)

    after = {r["phrase_id"]: hashlib.sha256(Path(r["path"]).read_bytes()).hexdigest()
             for r in recorded}
    assert after == before


def test_a_failing_candidate_does_not_destroy_the_session(tmp_path, monkeypatch):
    recorded = _write_corpus_audio(tmp_path / "audio", 2)

    def fake_run(cmd, **kwargs):
        job = json.loads(Path(cmd[cmd.index("--worker") + 1]).read_text(encoding="utf-8"))
        if job["model"] == "small":
            # The realistic failure: the worker writes an ok=False payload.
            Path(cmd[cmd.index("--worker-output") + 1]).write_text(json.dumps({
                "ok": False, "error": "RuntimeError: could not load model",
                "transcriptions": []}), encoding="utf-8")
        else:
            Path(cmd[cmd.index("--worker-output") + 1]).write_text(json.dumps({
                "ok": True, "load_seconds": 0.1, "device_used": "cpu",
                "transcriptions": [{"phrase_id": e["phrase_id"],
                                    "text": sb.CORPUS[int(e["phrase_id"]) - 1].text,
                                    "seconds": 0.05, "error": None}
                                   for e in job["audio"]]}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bench.subprocess, "run", fake_run)

    phrases_by_id = {p.id: p for p in sb.CORPUS}
    work = tmp_path / "work"
    work.mkdir()
    summaries = [
        bench.run_candidate(c, recorded, work_dir=work, phrases_by_id=phrases_by_id)
        for c in bench.build_candidates(PROD_STT, models=("tiny", "small"),
                                        use_gpu=False, include_auto_language=False)
    ]
    assert [s.available for s in summaries] == [True, False]
    assert summaries[0].exact_rate == 1.0
    assert "could not load model" in summaries[1].error
    # The report still renders, with the survivor's numbers intact.
    report = sb.render_report(sb.build_results(
        session_id="s", started_at="a", finished_at="b",
        environment={}, capture={}, summaries=summaries))
    assert "tiny/cpu-int8" in report and "UNAVAILABLE" in report


def test_a_hung_candidate_is_timed_out_and_the_session_continues(tmp_path, monkeypatch):
    """A hang must cost one row, not the whole session.

    Real risk, not hypothetical: the first CUDA configuration on this GPU pays
    a very long one-off cuDNN autotune, and an unbounded wait would strand the
    user at a console after they had already read out thirty phrases.
    """
    recorded = _write_corpus_audio(tmp_path / "audio", 1)

    def hang(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 1))

    monkeypatch.setattr(bench.subprocess, "run", hang)
    work = tmp_path / "work"
    work.mkdir()
    summary = bench.run_candidate(
        bench.build_candidates(PROD_STT, models=("small",), use_gpu=False,
                               include_auto_language=False)[0],
        recorded, work_dir=work, phrases_by_id={p.id: p for p in sb.CORPUS},
        timeout=1.0)
    assert summary.available is False
    assert "excedeu" in summary.error
    assert sb.render_report(sb.build_results(
        session_id="s", started_at="a", finished_at="b",
        environment={}, capture={}, summaries=[summary]))


def test_a_crashed_worker_that_writes_nothing_is_reported_not_raised(tmp_path, monkeypatch):
    recorded = _write_corpus_audio(tmp_path / "audio", 1)
    monkeypatch.setattr(bench.subprocess, "run",
                        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 3, "", "boom"))
    work = tmp_path / "work"
    work.mkdir()
    summary = bench.run_candidate(
        bench.build_candidates(PROD_STT, models=("tiny",), use_gpu=False,
                               include_auto_language=False)[0],
        recorded, work_dir=work, phrases_by_id={p.id: p for p in sb.CORPUS})
    assert summary.available is False
    assert "boom" in summary.error


# --------------------------------------------------------------------------
#  The real worker subprocess
# --------------------------------------------------------------------------


faster_whisper = pytest.importorskip("faster_whisper",
                                     reason="faster-whisper is an optional dependency")


def test_worker_transcribes_real_audio_and_reports_measured_metrics(tmp_path):
    """Runs the ACTUAL worker against a real model. No stubs anywhere."""
    recorded = _write_corpus_audio(tmp_path / "audio", 2)
    job = {"label": "tiny/cpu-int8", "model": "tiny", "device": "cpu",
           "compute_type": "int8", "language": "pt", "initial_prompt": None,
           "vad_filter": True,
           "audio": [{"phrase_id": r["phrase_id"], "path": r["path"]} for r in recorded]}
    job_path = tmp_path / "job.json"
    out_path = tmp_path / "out.json"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--worker", str(job_path),
         "--worker-output", str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert out_path.exists(), process.stderr

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is True, payload.get("error")
    assert payload["device_used"] == "cpu"
    assert payload["load_seconds"] > 0
    assert len(payload["transcriptions"]) == 2
    assert [t["phrase_id"] for t in payload["transcriptions"]] == ["001", "002"]
    assert all(t["seconds"] is not None for t in payload["transcriptions"])
    assert payload["ram_peak_mb"] >= payload["ram_baseline_mb"]


def test_worker_reports_a_bad_model_name_without_crashing(tmp_path):
    job_path = tmp_path / "job.json"
    out_path = tmp_path / "out.json"
    job_path.write_text(json.dumps({
        "label": "nonsense", "model": "this-model-does-not-exist-nano",
        "device": "cpu", "compute_type": "int8", "language": "pt",
        "initial_prompt": None, "vad_filter": True, "audio": []}), encoding="utf-8")

    process = subprocess.run(
        [sys.executable, str(SCRIPT), "--worker", str(job_path),
         "--worker-output", str(out_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert process.returncode == 0
    payload = json.loads(out_path.read_text(encoding="utf-8"))
    assert payload["ok"] is False
    assert payload["error"]


# --------------------------------------------------------------------------
#  Audio format fidelity
# --------------------------------------------------------------------------


def test_benchmark_records_in_nano_s_own_production_audio_format():
    """The benchmark must not measure a cleaner audio path than Nano's.

    Asserted against the LIVE merged configuration and the REAL provider class,
    so a change to either side breaks this test rather than silently making the
    benchmark unrepresentative.
    """
    pytest.importorskip("pyaudio", reason="PyAudio is an optional dependency")
    from core.config import load_config
    from core.voice import AudioInputProvider

    mic_cfg = (load_config().get("voice") or {}).get("microphone") or {}
    provider = AudioInputProvider(mic_cfg)
    assert provider.sample_rate == 16000
    assert provider.channels == 1
    # Same device the Ctrl+Shift+Space turn would open.
    assert provider.device_index == mic_cfg.get("device_index")


def test_synthetic_audio_matches_the_capture_format():
    payload = bench.synthetic_wav_bytes(0.25, 16000)
    with wave.open(__import__("io").BytesIO(payload)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 16000


# --------------------------------------------------------------------------
#  Privacy and blast radius
# --------------------------------------------------------------------------


def test_benchmark_session_directory_is_ignored_by_git():
    probe = REPO_ROOT / "runtime" / "speech_benchmark" / "gitignore-probe" / "audio"
    probe.mkdir(parents=True, exist_ok=True)
    target = probe / "phrase_001.wav"
    target.write_bytes(bench.synthetic_wav_bytes(0.05, 16000))
    try:
        out = subprocess.run(["git", "check-ignore", "-v", str(target)],
                             cwd=REPO_ROOT, capture_output=True, text=True)
        assert out.returncode == 0, "recordings of the user's voice are NOT git-ignored"
        assert "runtime/speech_benchmark/" in out.stdout
    finally:
        import shutil as _shutil

        _shutil.rmtree(probe.parent, ignore_errors=True)


def test_output_root_lives_under_the_ignored_directory():
    assert bench.OUTPUT_ROOT == REPO_ROOT / "runtime" / "speech_benchmark"


def _import_and_report_modules(module_name: str) -> set[str]:
    """Import one module in a FRESH interpreter and return everything it loaded.

    It has to be a fresh interpreter. Asking the test process's own
    ``sys.modules`` proves nothing: by the time the full suite reaches this
    file, other tests have already imported the Brain and the Groq client, and
    the assertion would fail for reasons that have nothing to do with the
    benchmark. (It did exactly that on the first full-suite run.) A subprocess
    measures what THIS module pulls in, which is the actual contract.
    """
    process = subprocess.run(
        [sys.executable, "-c",
         "import sys, json;"
         f"sys.path[:0] = [r'{REPO_ROOT}', r'{REPO_ROOT / 'scripts'}'];"
         f"import {module_name};"
         "print(json.dumps(sorted(sys.modules)))"],
        capture_output=True, text=True, timeout=180)
    assert process.returncode == 0, process.stderr
    return set(json.loads(process.stdout.strip().splitlines()[-1]))


def test_benchmark_never_reaches_groq_or_any_cloud_provider():
    """Behavioural: import the module for real and inspect what it pulled in.

    A source grep would be defeated by the word "Groq" appearing in a comment
    or in the report's own privacy note -- which it does, twice. This asks the
    interpreter instead.
    """
    forbidden = ("groq", "openai", "anthropic", "core.brain", "httpx", "eel")
    for module_name in ("core.speech_benchmark", "speech_accuracy_benchmark"):
        loaded = _import_and_report_modules(module_name)
        for banned in forbidden:
            offenders = [m for m in loaded if m == banned or m.startswith(banned + ".")]
            assert not offenders, f"{module_name} imported {offenders}"


def test_benchmark_never_imports_a_tool_executor():
    """The corpus contains "Abre o Spotify." It must stay a string, forever."""
    for module_name in ("core.speech_benchmark", "speech_accuracy_benchmark"):
        loaded = _import_and_report_modules(module_name)
        offenders = [m for m in loaded
                     if m.startswith("core.") and ("tool" in m or "execut" in m)]
        assert not offenders, f"{module_name} imported {offenders}"


def test_scoring_library_does_no_io_and_needs_no_optional_dependency():
    """core.speech_benchmark must stay importable with no audio stack at all."""
    process = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, r'%s');"
         "import core.speech_benchmark as m;"
         "assert 'pyaudio' not in sys.modules and 'faster_whisper' not in sys.modules;"
         "print(len(m.CORPUS))" % str(REPO_ROOT)],
        capture_output=True, text=True, timeout=120)
    assert process.returncode == 0, process.stderr
    assert process.stdout.strip() == str(len(sb.CORPUS))


# --------------------------------------------------------------------------
#  CLI surface
# --------------------------------------------------------------------------


def test_cli_parses_the_documented_no_argument_invocation():
    args = bench.build_parser().parse_args([])
    assert args.models == "tiny,base,small"
    assert args.worker is None
    assert args.delete_audio is False
    assert args.synthetic is False


def test_cli_exposes_audio_reuse_and_deletion():
    args = bench.build_parser().parse_args(
        ["--audio-dir", "x", "--delete-audio", "--no-gpu"])
    assert args.audio_dir == "x"
    assert args.delete_audio is True
    assert args.no_gpu is True


def test_load_existing_audio_finds_previous_recordings(tmp_path):
    _write_corpus_audio(tmp_path, 3)
    found = bench.load_existing_audio(tmp_path, list(sb.CORPUS)[:5])
    assert [f["phrase_id"] for f in found] == ["001", "002", "003"]
    assert all(f["audio_seconds"] > 0 for f in found)
