"""Nano — local Portuguese speech-accuracy benchmark.

    python scripts/speech_accuracy_benchmark.py

Records the user reading a fixed Portuguese corpus ONCE, then replays those
exact recordings through several faster-whisper configurations and reports
which one understands this user, on this microphone, best.

WHAT IT IS FOR
--------------
Capture is solved; transcription is not. ``tiny`` turns "Ola Nano, tudo bem?"
into "Alana no tudo bem". Choosing a replacement from model size alone would be
guessing, so this measures it.

THE RULE THAT MAKES THE COMPARISON FAIR
---------------------------------------
Each phrase is recorded EXACTLY ONCE. Every model and every configuration is
then run over that same WAV file. Nothing is re-recorded per model, so a
difference in the numbers can only come from the model, never from the user
having said it differently the second time.

Recording happens through Nano's own ``AudioInputProvider``, built from the
live merged configuration -- same device index, same 16 kHz mono 16-bit PCM,
same WAV container, same one-shot ``capture()`` call the Ctrl+Shift+Space turn
makes. The benchmark therefore measures the audio Nano actually produces, not
a cleaner laboratory version of it.

PRIVACY
-------
* Recordings stay on this machine, under ``runtime/speech_benchmark/`` which is
  git-ignored. They are never uploaded anywhere.
* No Groq call, no cloud STT, no network at all beyond the one-time
  Hugging Face download of the model WEIGHTS (no audio is sent).
* Nothing in the corpus is executed. "Abre o Spotify." is a transcription test;
  the benchmark has no tool executor and imports none.
* ``--delete-audio`` removes the recordings as soon as the report is written.

ISOLATION
---------
Every configuration runs in its OWN subprocess (``--worker``). That buys three
things at once: a genuinely cold model load to time, an uncontaminated RSS/VRAM
measurement, and crash isolation -- a model that fails to download or fails to
load costs its own row in the report and nothing else.
"""
from __future__ import annotations

import argparse
import ctypes
import datetime as _dt
import json
import math
import os
import platform
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import time
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The Hugging Face cache tries to symlink downloaded blobs. On this Windows
# install that raises WinError 1314 ("a required privilege is not held"), and
# it does so ONLY for the larger models -- `small` failed while `tiny` and
# `base` succeeded, which would have looked like "small is unavailable" in the
# middle of the user's benchmark. Copying instead of symlinking costs disk and
# always works, so it is set before anything imports huggingface_hub.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from core import speech_benchmark as sb  # noqa: E402

OUTPUT_ROOT = REPO_ROOT / "runtime" / "speech_benchmark"

#: Models compared in the baseline pass, smallest first.
DEFAULT_MODELS = ("tiny", "base", "small")


# --------------------------------------------------------------------------
#  Console
# --------------------------------------------------------------------------


def _prepare_console() -> None:
    """Make Portuguese readable in cmd.exe.

    The corpus is the thing the user has to READ ALOUD, so a mangled "Olá"
    is not cosmetic -- it changes what they say and corrupts the recording.
    cmd.exe defaults to codepage 850, which cannot render UTF-8 output.
    """
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def rule(char: str = "=") -> None:
    print(char * 74)


# --------------------------------------------------------------------------
#  Resource sampling
# --------------------------------------------------------------------------


def _gpu_memory_used_mb() -> float | None:
    """GPU memory in use, GPU-WIDE, via nvidia-smi. None when unavailable.

    Per-process accounting was tried first and this driver answers "[N/A]" for
    every PID (a normal WDDM limitation on consumer GeForce cards), so the
    honest thing to report is the whole-GPU figure with that caveat attached --
    see VRAM_NOTE. It is still meaningful here because each configuration runs
    alone in its own subprocess and the baseline is sampled immediately before
    the model loads.
    """
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode != 0:
            return None
        first = out.stdout.strip().splitlines()[0].strip()
        return float(first)
    except Exception:
        return None


VRAM_NOTE = ("Medida com nvidia-smi ao nivel do GPU inteiro: este driver nao "
             "suporta contabilidade por processo (devolve [N/A]). Como cada "
             "configuracao corre sozinha num subprocesso e a baseline e lida "
             "imediatamente antes de carregar o modelo, a diferenca "
             "pico-baseline e uma boa aproximacao do custo do modelo.")


class ResourceSampler:
    """Polls RSS (and VRAM on CUDA runs) in the background. No dependencies."""

    def __init__(self, *, sample_gpu: bool, interval: float = 0.1):
        import psutil

        self._process = psutil.Process()
        self._sample_gpu = sample_gpu
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.ram_peak_mb = self.current_ram_mb()
        self.vram_baseline_mb = _gpu_memory_used_mb() if sample_gpu else None
        self.vram_peak_mb = self.vram_baseline_mb

    def current_ram_mb(self) -> float:
        return self._process.memory_info().rss / (1024 * 1024)

    def sample_now(self) -> None:
        """Take one reading at a KNOWN moment, outside any timing window.

        Background polling alone is not enough for VRAM: nvidia-smi costs about
        50 ms, so it can only run a few times a second, and a short run can end
        with the peak never having been sampled while the model was resident.
        The self-test showed exactly that -- `small` on CUDA reported LESS VRAM
        than `base`, which is impossible and was pure sampling luck. This is
        called at the two moments that matter (straight after the model loads,
        and after each transcription) so the figure is anchored to real events
        rather than to the polling phase.
        """
        try:
            self.ram_peak_mb = max(self.ram_peak_mb, self.current_ram_mb())
        except Exception:
            pass
        if self._sample_gpu:
            value = _gpu_memory_used_mb()
            if value is not None:
                self.vram_peak_mb = max(self.vram_peak_mb or 0.0, value)

    def _run(self) -> None:
        # nvidia-smi costs ~50 ms per call, so it is polled far less often than
        # RSS. Model residency does not change on a 100 ms timescale anyway.
        gpu_every = max(1, int(0.25 / self._interval))
        tick = 0
        while not self._stop.wait(self._interval):
            try:
                self.ram_peak_mb = max(self.ram_peak_mb, self.current_ram_mb())
            except Exception:
                pass
            tick += 1
            if self._sample_gpu and tick % gpu_every == 0:
                value = _gpu_memory_used_mb()
                if value is not None:
                    self.vram_peak_mb = max(self.vram_peak_mb or 0.0, value)

    def start(self) -> "ResourceSampler":
        self._thread = threading.Thread(target=self._run, name="bench-sampler", daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


# --------------------------------------------------------------------------
#  Worker: one configuration, one subprocess
# --------------------------------------------------------------------------


def run_worker(job_path: Path, output_path: Path) -> int:
    """Load one model and transcribe every recording. Never raises."""
    job = json.loads(job_path.read_text(encoding="utf-8"))
    result: dict = {
        "label": job.get("label"),
        "ok": False,
        "error": None,
        "load_seconds": None,
        "device_used": None,
        "transcriptions": [],
    }

    on_gpu = str(job.get("device", "cpu")).lower().startswith("cuda")
    sampler = None
    try:
        sampler = ResourceSampler(sample_gpu=on_gpu)
        result["ram_baseline_mb"] = round(sampler.current_ram_mb(), 1)
        result["vram_baseline_mb"] = (round(sampler.vram_baseline_mb, 1)
                                      if sampler.vram_baseline_mb is not None else None)
        sampler.start()

        from faster_whisper import WhisperModel

        started = time.perf_counter()
        model = WhisperModel(
            job["model"],
            device=job.get("device", "cpu"),
            compute_type=job.get("compute_type", "int8"),
        )
        result["load_seconds"] = time.perf_counter() - started
        # Honest, not assumed: the constructor is what selects the backend, so
        # a device is only reported once it has actually loaded on that device.
        result["device_used"] = job.get("device", "cpu")
        sampler.sample_now()
        result["ram_after_load_mb"] = round(sampler.current_ram_mb(), 1)
        result["vram_after_load_mb"] = (round(sampler.vram_peak_mb, 1)
                                        if sampler.vram_peak_mb is not None else None)

        options: dict = {"vad_filter": job.get("vad_filter", True)}
        if job.get("language"):
            options["language"] = job["language"]
        if job.get("initial_prompt"):
            options["initial_prompt"] = job["initial_prompt"]
        if job.get("beam_size"):
            options["beam_size"] = job["beam_size"]

        for entry in job["audio"]:
            item = {"phrase_id": entry["phrase_id"], "text": "",
                    "seconds": None, "error": None}
            try:
                begin = time.perf_counter()
                segments, _info = model.transcribe(entry["path"], **options)
                text = " ".join(
                    part.text.strip() for part in segments if part.text and part.text.strip()
                ).strip()
                item["seconds"] = time.perf_counter() - begin
                item["text"] = text
            except Exception as exc:
                # One bad phrase must not cost the other twenty-nine.
                item["error"] = f"{type(exc).__name__}: {exc}"
            # Deliberately AFTER the timing window closes, so measuring the
            # model never inflates the latency being measured.
            sampler.sample_now()
            result["transcriptions"].append(item)

        result["ok"] = True
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if sampler is not None:
            sampler.stop()
            result["ram_peak_mb"] = round(sampler.ram_peak_mb, 1)
            result["vram_peak_mb"] = (round(sampler.vram_peak_mb, 1)
                                      if sampler.vram_peak_mb is not None else None)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return 0


# --------------------------------------------------------------------------
#  Recording
# --------------------------------------------------------------------------


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        frames, rate = handle.getnframes(), handle.getframerate()
    return (frames / rate) if rate else 0.0


def synthetic_wav_bytes(seconds: float, sample_rate: int, *, tone_hz: float = 220.0) -> bytes:
    """A deterministic tone, for validating the harness without a microphone.

    Used by ``--synthetic`` and by the tests. It is NOT speech and will score
    terribly; that is the point -- it proves the pipeline runs end to end
    without pretending to be a substitute for the human benchmark.
    """
    total = int(seconds * sample_rate)
    samples = bytearray()
    for n in range(total):
        value = int(8000 * math.sin(2 * math.pi * tone_hz * n / sample_rate))
        samples += struct.pack("<h", value)
    import io

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(samples))
    return buffer.getvalue()


def record_corpus(provider, phrases, *, seconds: int, audio_dir: Path,
                  synthetic: bool = False) -> list[dict]:
    """Walk the user through the corpus, one phrase at a time, recording once."""
    from core import speech_filter

    audio_dir.mkdir(parents=True, exist_ok=True)
    recorded: list[dict] = []
    total = len(phrases)

    for index, phrase in enumerate(phrases, start=1):
        while True:
            print()
            rule("-")
            print(f"  FRASE {index}/{total}   [{phrase.category}]")
            rule("-")
            print()
            print(f"      >>>  {phrase.text}")
            print()
            print(f"  Vais gravar {seconds} segundos. Le a frase de forma natural,")
            print("  a distancia habitual do microfone.")
            try:
                input("  [Enter] para comecar a gravar... ")
            except (EOFError, KeyboardInterrupt):
                print("\n  Interrompido.")
                raise

            if synthetic:
                audio = synthetic_wav_bytes(seconds, provider.sample_rate)
            else:
                print("  >>> A GRAVAR. Fala agora.")
                audio = provider.capture(seconds)
                print("  ... gravacao terminada.")

            if not audio:
                print("  !! O microfone nao devolveu audio. [r] repetir, [s] saltar.")
                if input("  Escolhe: ").strip().lower() == "s":
                    break
                continue

            path = audio_dir / f"phrase_{phrase.id}.wav"
            path.write_bytes(audio)
            rms = speech_filter.rms_of_wav(audio)
            duration = wav_duration_seconds(path)
            print(f"  Guardado: {path.name}  ({duration:.1f}s, nivel RMS {rms:.0f})")
            if rms < 15:
                print("  !! Nivel muito baixo. Considera repetir mais perto do microfone.")

            choice = input("  [Enter] frase seguinte  |  [r] repetir esta: ").strip().lower()
            if choice == "r":
                continue
            recorded.append({
                "phrase_id": phrase.id,
                "path": str(path),
                "audio_seconds": duration,
                "rms": round(rms, 1),
            })
            break

    return recorded


def load_existing_audio(audio_dir: Path, phrases) -> list[dict]:
    """Reuse a previous session's recordings. The same audio, a new comparison."""
    from core import speech_filter

    out: list[dict] = []
    for phrase in phrases:
        path = audio_dir / f"phrase_{phrase.id}.wav"
        if not path.exists():
            continue
        payload = path.read_bytes()
        out.append({
            "phrase_id": phrase.id,
            "path": str(path),
            "audio_seconds": wav_duration_seconds(path),
            "rms": round(speech_filter.rms_of_wav(payload), 1),
        })
    return out


# --------------------------------------------------------------------------
#  Candidate configurations
# --------------------------------------------------------------------------


def build_candidates(stt_config: dict, *, models: tuple[str, ...],
                     use_gpu: bool, include_auto_language: bool) -> list[dict]:
    """The baseline matrix.

    FAIRNESS RULE: every candidate uses Nano's PRODUCTION decoding settings --
    the same forced language, the same vad_filter, the same (default) beam
    size. No model is handed a better decoder than another. The only things
    that vary are the axes under test: model size, execution device, and the
    two deliberate controls (auto-language, vocabulary prompt).
    """
    language = str(stt_config.get("language") or "pt-PT").split("-")[0]
    cpu_compute = str(stt_config.get("compute_type") or "int8")

    candidates: list[dict] = []
    for name in models:
        candidates.append({
            "label": f"{name}/cpu-{cpu_compute}",
            "model": name,
            "device": "cpu",
            "compute_type": cpu_compute,
            "language": language,
            "initial_prompt": None,
            "vad_filter": True,
        })
    if use_gpu:
        for name in models:
            candidates.append({
                "label": f"{name}/cuda-float16",
                "model": name,
                "device": "cuda",
                "compute_type": "float16",
                "language": language,
                "initial_prompt": None,
                "vad_filter": True,
            })
    if include_auto_language:
        # The deliberate language control. Nano ALREADY forces Portuguese, so
        # "current behaviour" and "explicit pt" are the same configuration and
        # running both would just be the same row twice. The question actually
        # worth an answer is whether forcing it helps at all, so the control is
        # auto-detection.
        for name in models:
            candidates.append({
                "label": f"{name}/cpu-{cpu_compute}/auto-lang",
                "model": name,
                "device": "cpu",
                "compute_type": cpu_compute,
                "language": None,
                "initial_prompt": None,
                "vad_filter": True,
            })
    return candidates


def vocabulary_candidates(summaries: list[sb.ConfigSummary], *, limit: int = 2) -> list[dict]:
    """Repeat the best one or two configurations WITH the vocabulary hint.

    Ranked by entity accuracy first and WER second, because the vocabulary
    prompt exists to rescue entities, not to shave a percent off WER.
    """
    usable = [s for s in summaries
              if s.available and s.scores and s.initial_prompt is None and s.language]
    usable.sort(key=lambda s: (-s.keyword_accuracy, s.wer))
    picked: list[dict] = []
    for summary in usable[:limit]:
        picked.append({
            "label": f"{summary.label}+vocab",
            "model": summary.model,
            "device": summary.device,
            "compute_type": summary.compute_type,
            "language": summary.language,
            "initial_prompt": sb.VOCABULARY_PROMPT,
            "vad_filter": True,
        })
    return picked


# --------------------------------------------------------------------------
#  Orchestration
# --------------------------------------------------------------------------


#: A single configuration may not hold the whole session hostage. Chosen from
#: measurement: the slowest CPU configuration here transcribes 30 phrases in
#: about two minutes, while the first CUDA run pays a large one-off cuDNN
#: autotune on top. Ten minutes is generous for the former and still bounds a
#: configuration that has genuinely hung.
DEFAULT_WORKER_TIMEOUT_SECONDS = 900


def run_candidate(candidate: dict, recorded: list[dict], *, work_dir: Path,
                  phrases_by_id: dict,
                  timeout: float = DEFAULT_WORKER_TIMEOUT_SECONDS) -> sb.ConfigSummary:
    """Run one configuration in a clean subprocess and score what comes back."""
    summary = sb.ConfigSummary(
        label=candidate["label"],
        model=candidate["model"],
        device=candidate["device"],
        compute_type=candidate["compute_type"],
        language=candidate["language"],
        initial_prompt=candidate["initial_prompt"],
    )

    safe = candidate["label"].replace("/", "_").replace("+", "_")
    job_path = work_dir / f"job_{safe}.json"
    out_path = work_dir / f"out_{safe}.json"
    job = dict(candidate)
    job["audio"] = [{"phrase_id": r["phrase_id"], "path": r["path"]} for r in recorded]
    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()),
             "--worker", str(job_path), "--worker-output", str(out_path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        # A hang is a failure mode too, and an unbounded one would strand the
        # user in front of a console after they have already read out thirty
        # phrases. The recordings survive; only this row is lost.
        summary.available = False
        summary.error = (f"a configuracao excedeu {timeout:.0f}s e foi terminada; "
                         f"as restantes continuaram")
        return summary

    if not out_path.exists():
        summary.available = False
        summary.error = (f"o subprocesso terminou com codigo {process.returncode} "
                         f"sem escrever resultados: "
                         f"{(process.stderr or '').strip()[-600:]}")
        return summary

    payload = json.loads(out_path.read_text(encoding="utf-8"))
    summary.ram_baseline_mb = payload.get("ram_baseline_mb")
    summary.ram_after_load_mb = payload.get("ram_after_load_mb")
    summary.ram_peak_mb = payload.get("ram_peak_mb")
    summary.vram_baseline_mb = payload.get("vram_baseline_mb")
    summary.vram_peak_mb = payload.get("vram_peak_mb")
    if summary.vram_peak_mb is not None:
        summary.vram_note = VRAM_NOTE

    if not payload.get("ok"):
        summary.available = False
        summary.error = payload.get("error") or "falha desconhecida no subprocesso"
        return summary

    summary.load_seconds = payload.get("load_seconds")
    summary.device_used = payload.get("device_used")

    by_id = {r["phrase_id"]: r for r in recorded}
    for index, item in enumerate(payload.get("transcriptions", [])):
        phrase = phrases_by_id.get(item["phrase_id"])
        if phrase is None:
            continue
        score = sb.score_phrase(
            phrase,
            item.get("text") or "",
            latency_seconds=item.get("seconds"),
            audio_seconds=(by_id.get(item["phrase_id"]) or {}).get("audio_seconds"),
            error=item.get("error"),
        )
        if index == 0:
            summary.first_transcription_seconds = item.get("seconds")
        summary.scores.append(score)
    return summary


def describe_environment() -> dict:
    env: dict = {
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "python": platform.python_version(),
    }
    try:
        import faster_whisper

        env["faster_whisper"] = getattr(faster_whisper, "__version__", "?")
    except Exception:
        env["faster_whisper"] = None
    try:
        import ctranslate2

        env["ctranslate2"] = ctranslate2.__version__
        env["cuda_devices"] = ctranslate2.get_cuda_device_count()
    except Exception:
        env["ctranslate2"] = None
        env["cuda_devices"] = 0
    exe = shutil.which("nvidia-smi")
    if exe:
        try:
            out = subprocess.run(
                [exe, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                env["gpu"] = out.stdout.strip().splitlines()[0].strip()
        except Exception:
            pass
    return env


def run_gpu_probe(output_path: Path) -> int:
    """Actually transcribe something on CUDA. Written to be run as a subprocess.

    ``vad_filter`` is FALSE here on purpose. With it on, the silence detector
    removes the whole probe tone, ``encode()`` is never reached, and the probe
    "succeeds" without CUDA having executed a single kernel -- which is exactly
    the false positive this function exists to avoid.
    """
    payload = {"ok": False, "error": None}
    try:
        from faster_whisper import WhisperModel

        model = WhisperModel("tiny", device="cuda", compute_type="float16")
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            handle.write(synthetic_wav_bytes(1.0, 16000))
            probe_path = handle.name
        try:
            segments, _info = model.transcribe(probe_path, language="pt", vad_filter=False)
            list(segments)           # force the encoder to actually run
            payload["ok"] = True
        finally:
            try:
                os.unlink(probe_path)
            except OSError:
                pass
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


def gpu_is_usable(*, timeout: float = 180.0) -> tuple[bool, str]:
    """Whether CUDA can REALLY transcribe here. Returns (usable, reason).

    Counting CUDA devices is not enough, and trusting it caused a real failure
    on this machine. ``ctranslate2.get_cuda_device_count()`` returns 1 and
    ``WhisperModel(device="cuda")`` constructs happily -- because cuBLAS is
    loaded lazily, at the first inference. That first inference then dies with
    "Library cublas64_12.dll is not found".

    Worse, it does not die cleanly. In a single-threaded script it raises; in
    the worker, where a resource sampler and tqdm's monitor threads are alive,
    the same call HANGS inside native code and never returns. A benchmark that
    offered CUDA on a device count would therefore strand the user after they
    had already read thirty phrases aloud.

    So the probe is a real transcription, in a throwaway subprocess, under a
    timeout. GPU rows appear in the report only when CUDA has demonstrably
    produced output on this machine.
    """
    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() <= 0:
            return False, "nenhum dispositivo CUDA detectado"
    except Exception as exc:
        return False, f"ctranslate2 indisponivel: {exc}"

    with tempfile.TemporaryDirectory(prefix="nano-gpu-probe-") as tmp:
        out_path = Path(tmp) / "probe.json"
        try:
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve()),
                 "--gpu-probe", str(out_path)],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return False, (f"a sonda CUDA bloqueou durante mais de {timeout:.0f}s "
                           f"(inferencia nativa presa)")
        if not out_path.exists():
            return False, "a sonda CUDA terminou sem resultado"
        payload = json.loads(out_path.read_text(encoding="utf-8"))
        if payload.get("ok"):
            return True, "CUDA verificado com uma transcricao real"
        return False, f"CUDA nao consegue transcrever: {payload.get('error')}"


def prepare_models(models: tuple[str, ...]) -> int:
    """Download the model weights ahead of time so the human run never stalls."""
    from faster_whisper import WhisperModel

    failures = 0
    for name in models:
        print(f"  A preparar '{name}' ...", flush=True)
        started = time.perf_counter()
        try:
            WhisperModel(name, device="cpu", compute_type="int8")
            print(f"    OK em {time.perf_counter() - started:.1f}s")
        except Exception as exc:
            failures += 1
            print(f"    FALHOU: {type(exc).__name__}: {exc}")
    return failures


# --------------------------------------------------------------------------
#  Entry point
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="speech_accuracy_benchmark",
        description="Benchmark local de precisao de fala do Nano (portugues).",
    )
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS),
                        help="modelos faster-whisper a comparar (default: tiny,base,small)")
    parser.add_argument("--record-seconds", type=int, default=None,
                        help="duracao de cada gravacao (default: a janela de comando do Nano)")
    parser.add_argument("--audio-dir", default=None,
                        help="reaproveita gravacoes de uma sessao anterior em vez de gravar")
    parser.add_argument("--session-dir", default=None,
                        help="onde escrever os resultados (default: runtime/speech_benchmark/<ts>)")
    parser.add_argument("--no-gpu", action="store_true",
                        help="nao testar configuracoes CUDA")
    parser.add_argument("--no-auto-language", action="store_true",
                        help="nao correr o controlo de deteccao automatica de lingua")
    parser.add_argument("--no-vocabulary", action="store_true",
                        help="nao correr a segunda experiencia com o initial_prompt")
    parser.add_argument("--delete-audio", action="store_true",
                        help="apagar as gravacoes assim que o relatorio for escrito")
    parser.add_argument("--phrases", type=int, default=None,
                        help="usar apenas as N primeiras frases (para testar o proprio benchmark)")
    parser.add_argument("--synthetic", action="store_true",
                        help="gerar um tom em vez de gravar: valida o benchmark sem microfone")
    parser.add_argument("--prepare", action="store_true",
                        help="apenas descarregar os modelos e sair")
    parser.add_argument("--worker", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--gpu-probe", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-output", default=None, help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.gpu_probe:
        return run_gpu_probe(Path(args.gpu_probe))

    if args.worker:
        if not args.worker_output:
            print("--worker requer --worker-output", file=sys.stderr)
            return 2
        return run_worker(Path(args.worker), Path(args.worker_output))

    _prepare_console()
    models = tuple(m.strip() for m in args.models.split(",") if m.strip())

    if args.prepare:
        rule()
        print("  NANO — preparacao dos modelos de fala")
        rule()
        return 1 if prepare_models(models) else 0

    from core.config import load_config

    config = load_config()
    voice_cfg = config.get("voice") or {}
    stt_cfg = dict(voice_cfg.get("stt") or {})
    mic_cfg = dict(voice_cfg.get("microphone") or {})
    record_seconds = int(args.record_seconds
                         or voice_cfg.get("wake_command_timeout_seconds") or 7)

    phrases = list(sb.CORPUS)[: args.phrases] if args.phrases else list(sb.CORPUS)
    phrases_by_id = {p.id: p for p in phrases}

    session_id = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    session_dir = Path(args.session_dir) if args.session_dir else (OUTPUT_ROOT / session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    work_dir = session_dir / "work"
    work_dir.mkdir(exist_ok=True)
    audio_dir = Path(args.audio_dir) if args.audio_dir else (session_dir / "audio")

    rule()
    print("  NANO — BENCHMARK DE PRECISAO DE FALA")
    rule()
    print()
    print(f"  Frases:            {len(phrases)}")
    print(f"  Gravacao por frase: {record_seconds}s")
    print(f"  Sessao:            {session_dir}")
    print()
    print("  As gravacoes ficam SO nesta maquina, numa pasta ignorada pelo git.")
    print("  Nada e enviado para o Groq nem para nenhuma API na nuvem, e")
    print("  nenhum comando da lista e executado: isto e so transcricao.")
    print()

    started_at = _dt.datetime.now().isoformat(timespec="seconds")
    device_name = None

    if args.audio_dir:
        recorded = load_existing_audio(audio_dir, phrases)
        print(f"  A reaproveitar {len(recorded)} gravacoes de {audio_dir}")
        if not recorded:
            print("  Nenhum ficheiro phrase_XXX.wav encontrado. Nada a fazer.")
            return 1
    else:
        from core.voice import AudioInputProvider

        provider = AudioInputProvider(mic_cfg)
        if not args.synthetic:
            devices = provider.list_devices()
            match = next((d for d in devices if d["index"] == provider.device_index), None)
            device_name = match["name"] if match else None
            print(f"  Microfone:         indice {provider.device_index} "
                  f"({device_name or 'nome desconhecido'})")
            print(f"  Formato:           {provider.sample_rate} Hz, "
                  f"{provider.channels} canal, 16-bit PCM  (igual ao Nano)")
            print()
            print("  Fecha o Nano Desktop antes de continuar, para que so este")
            print("  programa esteja a usar o microfone.")
            try:
                input("  [Enter] quando estiveres pronto... ")
            except (EOFError, KeyboardInterrupt):
                print("\n  Cancelado.")
                return 1
        try:
            recorded = record_corpus(provider, phrases, seconds=record_seconds,
                                     audio_dir=audio_dir, synthetic=args.synthetic)
        except (EOFError, KeyboardInterrupt):
            return 1

    if not recorded:
        print("  Nenhuma frase gravada. Nada a comparar.")
        return 1

    if args.no_gpu:
        use_gpu, gpu_reason = False, "desactivado por --no-gpu"
    else:
        print("  A verificar se o CUDA consegue mesmo transcrever...", flush=True)
        use_gpu, gpu_reason = gpu_is_usable()
    print(f"  GPU: {'SIM' if use_gpu else 'NAO'} — {gpu_reason}")

    candidates = build_candidates(
        stt_cfg, models=models, use_gpu=use_gpu,
        include_auto_language=not args.no_auto_language,
    )

    print()
    rule()
    print(f"  A avaliar {len(candidates)} configuracoes sobre as MESMAS "
          f"{len(recorded)} gravacoes")
    rule()

    summaries: list[sb.ConfigSummary] = []
    for candidate in candidates:
        print(f"  -> {candidate['label']} ...", end="", flush=True)
        summary = run_candidate(candidate, recorded, work_dir=work_dir,
                                phrases_by_id=phrases_by_id)
        summaries.append(summary)
        if summary.available:
            print(f" WER {summary.wer * 100:.1f}%  "
                  f"entidades {summary.keyword_hits}/{summary.keyword_total}  "
                  f"mediana {summary.warm_median or 0:.2f}s")
        else:
            print(f" INDISPONIVEL ({(summary.error or '')[:90]})")

    if not args.no_vocabulary:
        extra = vocabulary_candidates(summaries)
        if extra:
            print()
            rule()
            print("  Experiencia de vocabulario (initial_prompt curto), "
                  "sobre o MESMO audio")
            rule()
            for candidate in extra:
                print(f"  -> {candidate['label']} ...", end="", flush=True)
                summary = run_candidate(candidate, recorded, work_dir=work_dir,
                                        phrases_by_id=phrases_by_id)
                summaries.append(summary)
                if summary.available:
                    print(f" WER {summary.wer * 100:.1f}%  "
                          f"entidades {summary.keyword_hits}/{summary.keyword_total}")
                else:
                    print(f" INDISPONIVEL ({(summary.error or '')[:90]})")

    results = sb.build_results(
        session_id=session_id,
        started_at=started_at,
        finished_at=_dt.datetime.now().isoformat(timespec="seconds"),
        environment=describe_environment(),
        capture={
            "device_index": mic_cfg.get("device_index"),
            "device_name": device_name,
            "sample_rate": mic_cfg.get("sample_rate", 16000),
            "channels": mic_cfg.get("channels", 1),
            "sample_width_bytes": 2,
            "record_seconds": record_seconds,
            "phrases_recorded": len(recorded),
            "audio_dir": str(audio_dir),
            "synthetic": bool(args.synthetic),
            "production_stt": stt_cfg,
            "gpu_used": use_gpu,
            "gpu_reason": gpu_reason,
        },
        summaries=summaries,
    )

    results_path = session_dir / "results.json"
    report_path = session_dir / "report.md"
    results_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    report_path.write_text(sb.render_report(results), encoding="utf-8")

    if args.delete_audio:
        shutil.rmtree(audio_dir, ignore_errors=True)

    print()
    rule()
    print("  RESULTADOS")
    rule()
    print(f"  {report_path}")
    print(f"  {results_path}")
    if args.delete_audio:
        print("  As gravacoes foram apagadas (--delete-audio).")
    else:
        print(f"  Gravacoes (locais, ignoradas pelo git): {audio_dir}")
        print("  Para apagar tudo mais tarde:")
        print(f"    Remove-Item -Recurse -Force \"{OUTPUT_ROOT}\"")
    print()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
