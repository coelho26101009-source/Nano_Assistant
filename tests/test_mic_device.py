"""The chosen microphone: one entry per device, and a broken one is not used.

Simão's PC, 2026-11-24: Definições listed the HyperX headset four times (one
per Windows audio system). He picked the DirectSound copy, which returned
seconds of junk instantly: "Ei Nano" and Ctrl+Shift+Espaço "heard" loud noise
and never speech, and the island opened and closed at once.
"""
from __future__ import annotations

import importlib.machinery
import sys
import time
import types

import pytest

from core import voice
from core.voice import AudioInputProvider


@pytest.fixture(autouse=True)
def _clean_class_state(monkeypatch):
    monkeypatch.setattr(AudioInputProvider, "_device_verdicts", {})
    monkeypatch.setattr(AudioInputProvider, "_default_host_api", None)
    monkeypatch.setattr(AudioInputProvider, "_device_cache", None)


class _Stream:
    def __init__(self, delay: float):
        self.delay = delay
        self.closed = False

    def read(self, frames, exception_on_overflow=False):
        if self.delay:
            time.sleep(self.delay)
        return b"\x00\x00" * frames

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True


class _PyAudio:
    """Device 9 hands back audio instantly (broken); the rest in real time."""

    def __init__(self):
        self.opened: list = []

    def open(self, **kwargs):
        index = kwargs.get("input_device_index")
        stream = _Stream(0.0 if index == 9 else 1024 / kwargs["rate"])
        self.opened.append((index, stream))
        return stream


@pytest.fixture
def fake_pyaudio(monkeypatch):
    module = types.ModuleType("pyaudio")
    module.__spec__ = importlib.machinery.ModuleSpec("pyaudio", None)
    module.paInt16 = 8
    monkeypatch.setitem(sys.modules, "pyaudio", module)
    return module


def test_a_device_that_returns_audio_instantly_is_replaced_by_the_default(fake_pyaudio):
    provider = AudioInputProvider({"device_index": 9})
    pa = _PyAudio()
    stream = provider._open_input(pa, 16000)
    assert [index for index, _ in pa.opened] == [9, None]
    assert pa.opened[0][1].closed is True
    assert stream is pa.opened[1][1]
    assert provider.device_problem == "not_recording"


def test_a_device_that_records_in_real_time_is_kept(fake_pyaudio):
    provider = AudioInputProvider({"device_index": 1})
    pa = _PyAudio()
    stream = provider._open_input(pa, 16000)
    assert [index for index, _ in pa.opened] == [1]
    assert stream is pa.opened[0][1]
    assert provider.device_problem is None


def test_the_verdict_is_remembered_so_later_turns_lose_no_audio(fake_pyaudio):
    provider = AudioInputProvider({"device_index": 1})
    provider._open_input(_PyAudio(), 16000)
    calls = []
    provider._flows_in_real_time = lambda *a: calls.append(a) or True  # type: ignore[method-assign]
    provider._open_input(_PyAudio(), 16000)
    assert calls == []


def test_the_system_default_is_never_checked(fake_pyaudio):
    provider = AudioInputProvider({})
    provider._flows_in_real_time = lambda *a: pytest.fail("checked the default")  # type: ignore[method-assign]
    pa = _PyAudio()
    provider._open_input(pa, 16000)
    assert [index for index, _ in pa.opened] == [None]


def test_definicoes_lists_each_microphone_once(monkeypatch):
    devices = [
        {"index": 0, "name": "Mapeador de sons", "maxInputChannels": 2, "hostApi": 0},
        {"index": 1, "name": "Microfone (HyperX Cloud III S W", "maxInputChannels": 1, "hostApi": 0},
        {"index": 4, "name": "Colunas", "maxInputChannels": 0, "hostApi": 0},
        {"index": 9, "name": "Microfone (HyperX Cloud III S Wireless)", "maxInputChannels": 1, "hostApi": 1},
        {"index": 20, "name": "Microfone (HyperX Cloud III S Wireless)", "maxInputChannels": 2, "hostApi": 2},
    ]
    monkeypatch.setattr(AudioInputProvider, "_default_host_api", 0)
    provider = AudioInputProvider({})
    monkeypatch.setattr(provider, "list_devices", lambda: devices)
    assert [d["index"] for d in provider.input_devices()] == [0, 1]
    # The one already chosen stays visible, so the setting is not shown blank.
    provider.device_index = 20
    assert [d["index"] for d in provider.input_devices()] == [0, 1, 20]


def test_without_host_api_information_every_input_is_offered(monkeypatch):
    devices = [{"index": 0, "name": "Mic", "maxInputChannels": 1},
               {"index": 3, "name": "Out", "maxInputChannels": 0}]
    provider = AudioInputProvider({})
    monkeypatch.setattr(provider, "list_devices", lambda: devices)
    assert [d["index"] for d in provider.input_devices()] == [0]


def test_voice_module_still_exports_the_provider():
    assert voice.AudioInputProvider is AudioInputProvider
