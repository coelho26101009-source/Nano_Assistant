"""Guided local model: look at the PC, recommend one light model, download on click.

No real Ollama, GPU or download: ``local_setup.PROBES`` and ``local_setup.PULL``
are replaced. What is tested is the recommendation for each kind of PC, that
only offered models can be pulled, the progress, and what happens at the end.
"""
from __future__ import annotations

import pytest

from core import local_setup, vision


def _hw(ram, vram=None, disk=100.0):
    return {"ram_gb": ram, "cpu_cores": 4, "free_disk_gb": disk,
            "gpu": {"name": "NVIDIA GeForce RTX", "vram_gb": vram} if vram else None}


@pytest.mark.parametrize("hardware,model", [
    (_hw(4), "qwen3-vl:2b"),
    (_hw(6), "qwen3-vl:2b"),
    (_hw(8), "qwen3-vl:4b"),
    (_hw(16), "qwen3-vl:4b"),           # no GPU: the light one stays fast
    (_hw(6, vram=6), "qwen3-vl:4b"),
    (_hw(16, vram=6), "qwen3-vl:4b"),
    (_hw(16, vram=8), "qwen3-vl:8b"),
    (_hw(32, vram=12), "qwen3-vl:8b"),
])
def test_the_recommendation_fits_the_pc(hardware, model):
    assert local_setup.recommend(hardware)["model"] == model


def test_every_offered_model_writes_sees_and_is_light():
    for model in local_setup.RECOMMENDED:
        assert vision.is_ollama_vision_model(model.name), model.name
        assert model.size_gb <= 7
    sizes = [m.size_gb for m in local_setup.RECOMMENDED]
    assert sizes == sorted(sizes)


def test_weak_pc_and_full_disk_are_warned_about():
    rec = local_setup.recommend(_hw(3, disk=1.0))
    assert len(rec["warnings"]) == 2
    assert local_setup.recommend(_hw(16))["warnings"] == []


def test_hardware_detection_uses_the_probes(monkeypatch):
    monkeypatch.setattr(local_setup, "PROBES", {"ram_gb": lambda: 15.9, "cpu_cores": lambda: 8,
                                                "gpu": lambda: None, "free_disk_gb": lambda: 200.0})
    assert local_setup.detect_hardware() == {"ram_gb": 15.9, "cpu_cores": 8, "gpu": None,
                                             "free_disk_gb": 200.0}


def test_nvidia_smi_absent_means_no_gpu(monkeypatch):
    monkeypatch.setattr(local_setup.shutil, "which", lambda name: None)
    assert local_setup._nvidia_gpu() is None


# ── the download ──────────────────────────────────────────────────────────

def test_only_offered_models_can_be_pulled(monkeypatch):
    pulled = []
    monkeypatch.setattr(local_setup, "PULL", lambda base, model: pulled.append(model) or iter(()))
    downloader = local_setup.Downloader()
    for name in ("llama3:70b", "../../etc", "", "qwen3-vl:4b; rm -rf"):
        assert downloader.start(name, "http://x") == {"ok": False, "error": "model_not_offered"}
    assert pulled == []


def test_progress_then_activation(monkeypatch):
    events = [
        {"status": "pulling manifest"},
        {"status": "pulling a", "digest": "sha256:a", "total": 2_000_000_000, "completed": 500_000_000},
        {"status": "pulling b", "digest": "sha256:b", "total": 1_000_000_000, "completed": 1_000_000_000},
        {"status": "success"},
    ]
    monkeypatch.setattr(local_setup, "PULL", lambda base, model: iter(events))
    activated = []
    downloader = local_setup.Downloader()
    assert downloader.start("qwen3-vl:4b", "http://127.0.0.1:11434", on_done=activated.append)["ok"]
    downloader.wait()
    status = downloader.status()
    assert status["state"] == "done" and status["percent"] == 100
    assert status["totalGb"] == 3.0
    assert activated == ["qwen3-vl:4b"]


def test_a_failed_pull_says_what_to_check_and_activates_nothing(monkeypatch):
    def broken(base, model):
        yield {"status": "pulling manifest"}
        yield {"error": "pull model manifest: file does not exist"}

    monkeypatch.setattr(local_setup, "PULL", broken)
    activated = []
    downloader = local_setup.Downloader()
    downloader.start("qwen3-vl:2b", "http://x", on_done=activated.append)
    downloader.wait()
    status = downloader.status()
    assert status["state"] == "error" and "Ollama" in status["message"]
    assert activated == []


def test_one_download_at_a_time(monkeypatch):
    import threading

    gate = threading.Event()

    def slow(base, model):
        gate.wait(5)
        yield {"status": "success"}

    monkeypatch.setattr(local_setup, "PULL", slow)
    downloader = local_setup.Downloader()
    assert downloader.start("qwen3-vl:2b", "http://x")["ok"]
    assert downloader.start("qwen3-vl:4b", "http://x") == {"ok": False, "error": "already_downloading"}
    gate.set()
    downloader.wait()


# ── the bridge ────────────────────────────────────────────────────────────

def test_the_bridge_refuses_unoffered_models_and_reports_setup(monkeypatch):
    from core import main

    monkeypatch.setattr(main, "_LOCAL_HARDWARE", _hw(16))
    setup = main.get_local_setup()
    assert setup["ok"] and setup["recommended"]["model"] == "qwen3-vl:4b"
    assert {c["model"] for c in setup["choices"]} == local_setup.ALLOWED_MODELS
    assert setup["ollama"]["downloadUrl"].startswith("https://ollama.com/")
    assert main.start_local_download("llama3:70b")["error"] in {"model_not_offered", "local_disabled"}


def test_activation_makes_it_the_local_model(monkeypatch):
    from core import main, user_settings

    saved = {}
    monkeypatch.setattr(user_settings, "set_value", lambda k, v: saved.setdefault(k, v) and {"ok": True})
    monkeypatch.setattr(main.brain, "ollama_model", "old")
    monkeypatch.setattr(main, "CONFIG", {})
    main._activate_downloaded_model("qwen3-vl:4b")
    assert main.brain.ollama_model == "qwen3-vl:4b"
    assert saved == {"local_model": "qwen3-vl:4b"}
