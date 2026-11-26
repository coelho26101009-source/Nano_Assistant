"""The butler style (core/voice_style.py): what it does to a voice, and what it never does.

* It shapes the sound: low boom down, body (500 Hz - 2 kHz) up, a faint room.
* It keeps the loudness of the voice that came in, and never clips: a piece
  whose peaks would pass PEAK is turned down to it.
* It is pure: samples in, a new list out; silence and empty input stay harmless.
"""
from __future__ import annotations

import math

import pytest

from core import voice_style as vs

RATE = 24000


def _tone(freq: float, seconds: float = 0.5, amplitude: float = 0.3) -> list[float]:
    return [amplitude * math.sin(2 * math.pi * freq * i / RATE) for i in range(int(RATE * seconds))]


def _gain_db(freq: float) -> float:
    """Steady-state gain of the equaliser alone at ``freq``."""
    signal = _tone(freq)
    shaped = signal
    for coefficients in vs.butler_filters(RATE):
        shaped = vs._filter(shaped, coefficients)
    half = len(signal) // 2  # skip the filters' start-up

    def rms(values):
        return math.sqrt(sum(v * v for v in values) / len(values))

    return 20 * math.log10(rms(shaped[half:]) / rms(signal[half:]))


def test_the_equaliser_takes_boom_out_and_puts_body_in():
    assert _gain_db(40) < -10          # rumble under the high-pass
    assert _gain_db(180) < -1.5        # less boom
    assert 2.5 < _gain_db(1200) < 5.5  # more body
    assert _gain_db(1200) > _gain_db(180) + 4
    assert _gain_db(11000) < 0         # softer top


def test_the_result_never_clips_and_keeps_a_room_tail():
    loud = [min(1.0, max(-1.0, 3 * x)) for x in _tone(300, amplitude=1.0)]
    out = vs.butler(loud, RATE)
    assert max(abs(x) for x in out) == pytest.approx(vs.PEAK)
    assert len(out) == len(loud) + int(RATE * vs.TAIL_SECONDS)
    # The reflections ring on after the voice ends: a room, not a cut.
    assert max(abs(x) for x in out[len(loud):len(loud) + int(RATE * 0.035)]) > 0.01


def _rms(values):
    return math.sqrt(sum(v * v for v in values) / len(values))


def test_the_loudness_stays_the_same_as_the_voice_that_came_in():
    for freq, amplitude in ((150, 0.3), (1200, 0.05), (3000, 0.2)):
        signal = _tone(freq, amplitude=amplitude)
        out = vs.butler(signal, RATE)
        assert _rms(out[:len(signal)]) == pytest.approx(_rms(signal), rel=0.12)


def test_nan_never_reaches_the_speakers():
    assert set(vs.butler([0.1, float("nan"), 0.1], RATE)) == {0.0}


def test_one_click_comes_back_with_its_three_reflections():
    impulse = [0.0] * 4000
    impulse[100] = 1.0
    out = vs.butler(impulse, RATE)
    for delay_ms, _gain in vs.REFLECTIONS:
        at = 100 + int(RATE * delay_ms / 1000)
        assert max(abs(x) for x in out[at - 2:at + 3]) > 0.01


def test_silence_empty_and_bad_rates_are_harmless():
    assert vs.butler([], RATE) == []
    assert set(vs.butler([0.0] * 100, RATE)) == {0.0}
    assert vs.butler([0.1, -0.1], 0) == [0.1, -0.1]
    samples = [0.1, 0.2]
    vs.butler(samples, RATE)
    assert samples == [0.1, 0.2], "the input list is never changed"


def test_low_sample_rates_skip_the_top_shelf():
    # Piper medium voices run at 22 050 Hz, low ones at 16 000: both stay stable.
    for rate in (16000, 22050, 24000):
        out = vs.butler([0.2 * math.sin(i / 3) for i in range(rate // 4)], rate)
        assert all(math.isfinite(x) for x in out)
    assert len(vs.butler_filters(16000)) == 4 and len(vs.butler_filters(24000)) == 5
