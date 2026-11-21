"""Stock Xbox window regression for issue #40. No Windows, GPU or console required."""
import threading
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from framegen.io import Tick
from framegen.sources import Clock60, WindowBeatPhase, source_info
from scripts.xbox_cadence_replay import STAGES, START, fixture, replay, summarize


@pytest.mark.parametrize("capture", ["ddagrab", "dda"])
def test_game_menu_game_on_high_refresh_desktop(capture):
    # Exercise the real Clock60 reader and Pipeline on seeded desktop arrivals.
    # ddagrab includes the extra exact redraws. DDA supplies only dirty-window events.
    events = fixture(capture, monitor_hz=175, seed=40)
    threads = torch.get_num_threads()
    try:
        torch.set_num_threads(1)
        rows = replay(events, capture, enabled=True)
    finally:
        torch.set_num_threads(threads)
    first = {}
    for event in events:
        first.setdefault(event.content, (event.time - START) * 1000)
    for name, start, stop, rate in STAGES:
        result = summarize(rows, first, start + 120, stop)
        assert result["detector_fps_p50"] == rate, name
        assert result["even_out_ticks"] == result["missed_by_clock"] == 0, name
        assert result["backward_steps"] == result["never_drawn_as_original"] == 0, name
        assert result["interval_with_motion_p95_ms"] < 17.0, name
        if rate == 60:
            assert result["interpolated_events"] == 0


def test_exact_redraw_keeps_first_timestamp_and_one_pixel_change():
    # Make the tiny HUD update between all sampled pixels; only a full comparison can confirm a copy.
    frame = np.zeros((32, 32, 3), np.uint8)
    changed = frame.copy()
    changed[7, 9, 0] = 1
    ingested = threading.Event()

    class Source:
        xbox_window = True
        path = "CPU"
        info = source_info("window", hz=175.0)

        def __iter__(self):
            for i, f in enumerate([frame, frame.copy(), changed]):
                yield Tick(f, 1 + i * 0.001, i, content=i, t_source=10 + i)
            ingested.set()

    def clock():
        assert ingested.wait(2)
        return 1.003

    c = Clock60(Source(), clock=clock, sleep=lambda _: None)
    ticks = list(c)
    assert [(t.content, t.t_arrival, t.t_source) for t in ticks] == [(0, 1.0, 10), (2, 1.002, 12)]
    assert c.desktop_copies == 1 and c.overtaken == 0


def test_burst_after_stall_drops_stale_frames_and_drains():
    finished = threading.Event()

    class Source:
        xbox_window = True
        path = "GPU"
        info = source_info("window", hz=175.0)

        def __iter__(self):
            for i in range(20):
                yield Tick(None, i * 0.01, i, content=i)
            finished.set()

    def clock():
        assert finished.wait(2)
        return 1.0  # far more than 18.7 ms since any of the captures
    c = Clock60(Source(), clock=clock, sleep=lambda _: None)
    assert [t.content for t in c] == [19]
    assert c.overtaken == 19


def test_monitor_change_relearns_and_drops_spare():
    source = SimpleNamespace(xbox_window=True, info=source_info("window", hz=175.0))
    c = Clock60(source)
    c._taken = 0
    for i in range(2):
        c._offer(Tick(None, i / 60, i))
    assert c._phase is not None and c._spare is not None
    source.info = source_info("window", hz=60.0)
    c._offer(Tick(None, 2 / 60, 2))
    assert c._phase is c._spare is None and c.overtaken == 2
    source.info = source_info("window", hz=144.0)
    c._offer(Tick(None, 3 / 60, 3))
    assert c._phase is not None and c._spare is None and c.overtaken == 3


def test_phase_tracks_only_small_continuous_corrections_and_resets_after_pause():
    p = 1 / 60
    phase = WindowBeatPhase(p)
    beat = 100.0
    for i in range(120):
        phase.observe(100 + i * p + 0.003)
        following = phase.next(beat)
        assert abs(following - beat - p) <= 0.000251
        beat = following
    phase.observe(105.0)
    assert phase.next(105.0) == 105.0 + p
