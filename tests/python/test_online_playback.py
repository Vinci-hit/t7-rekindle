import re
from pathlib import Path
import pytest
from Business.scripts import online_playback as playback


def test_arrival_jitter_is_bounded_and_ages_out():
    state = {}
    playback.observe(state, 0, 1000)
    playback.observe(state, 50, 1080)
    assert state['delayMs'] == 130
    for i in range(2, 40):
        playback.observe(state, i * 50, 1030 + i * 50)
    assert state['delayMs'] == 100
    playback.observe(state, 2000, 10000)
    assert state['delayMs'] <= 180
    assert len(state['jitter']) <= 32


def test_clock_advances_without_packets_and_corrects_watermark_gradually():
    state = {'clockOffset': 1000, 'delayMs': 100}
    assert playback.advance(state, 1100, 0) == 0
    assert playback.advance(state, 1150, 0) == 50
    state['delayMs'] = 200
    assert playback.advance(state, 1200, 0) == 95
    state['delayMs'] = 100
    assert playback.advance(state, 1250, 0) == pytest.approx(150)


def test_extrapolation_is_finite_and_rejects_teleports():
    a = {'at': 0, 'pose': {'position': [0, 0, 0]}}
    b = {'at': 50, 'pose': {'position': [.25, 0, .1]}}
    assert playback.extrapolate(a, b, 100)['position'] == [.5, 0, .1]
    assert playback.extrapolate(a, b, 151)['position'] == [.75, 0, .1]
    assert playback.extrapolate(a, b, 1000)['position'] == [.75, 0, .1]
    assert playback.extrapolate(a, b, 151, airborne=True)['position'] != b['pose']['position']
    b['pose']['position'] = [100, 0, 0]
    assert playback.extrapolate(a, b, 100)['position'] == [100, 0, 0]



def test_server_presentation_delay_matches_the_minimum_playback_delay():
    # Rust holds a monster's damage back by this much so it lands when the blade is visible.
    limits = Path(__file__).resolve().parents[2] / 'game-server' / 'src' / 'limits.rs'
    delay = int(re.search(r'PRESENTATION_DELAY_MS: u64 = (\d+);', limits.read_text()).group(1))
    assert delay == playback.BASE_DELAY_MS
