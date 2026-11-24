"""TickClock: each tick's timings once its GPU work is done, read from CUDA events instead of waiting for them. CPU
only: stand-in events on a stand-in GPU timeline, and the pipeline's _tick_stats on what the clock gives."""
import sys
from collections import Counter
from pathlib import Path

from framegen.cadence import Action
from framegen.gputime import TickClock
from framegen.pipeline import Stats, _tick_stats


class _Gpu:
    """A stand-in stream: each event is done at the GPU time it's given, once `now` reaches it."""

    def __init__(self):
        self.now, self.queue, self.waited = 0.0, [], []

    def event(self):
        return _Event(self)


class _Event:
    def __init__(self, gpu):
        self.gpu, self.t = gpu, None

    def record(self):
        self.t = self.gpu.queue.pop(0)  # the GPU time this event will be done at

    def query(self):
        return self.t <= self.gpu.now

    def synchronize(self):
        self.gpu.waited.append(self.t)
        self.gpu.now = max(self.gpu.now, self.t)

    def elapsed_time(self, end):
        assert self.query() and end.query(), "elapsed_time needs both events done"
        return (end.t - self.t) * 1000


def _clock(gpu, t_cpu=(10.0,), depth=2):
    cpu = list(t_cpu)
    return TickClock(True, depth=depth, event=gpu.event, now=lambda: cpu.pop(0))


def test_off_gives_each_tick_at_once_with_cpu_times():
    got = []
    c = TickClock(False, event=lambda: 1 / 0)  # never makes an event
    c.begin()
    assert c.mark() is None and c.last() == -1
    c.done(lambda at: got.append((at(5.0), at(6.0, 0))))
    assert got == [(5.0, 6.0)]


def test_waits_for_events_and_moves_times_to_when_frames_were_done():
    gpu, got = _Gpu(), []
    c = _clock(gpu)
    gpu.queue = [10.0, 10.004, 10.012]  # start, then two frames done 4 ms and 12 ms after it
    c.begin()
    assert c.last() == -1
    assert c.mark() == 0 and c.last() == 0
    assert c.mark() == 1
    c.done(lambda at: got.append(at))
    assert got == []  # not done on the GPU yet: nothing waits
    gpu.now = 10.012
    c.poll()
    at = got[0]
    assert abs(at(10.001) - 10.012) < 1e-9  # the CPU was done queueing first: the last frame's time
    assert abs(at(10.001, 0) - 10.004) < 1e-9  # an early present: its own frame's time
    assert at(10.001, -1) == 10.001  # before any generated frame
    assert at(10.02) == 10.02  # the CPU was later than the GPU
    assert gpu.waited == []


def test_ticks_come_out_in_order_and_the_gpu_is_kept_within_depth():
    gpu, got = _Gpu(), []
    c = _clock(gpu, t_cpu=[1.0, 2.0, 3.0, 4.0], depth=2)
    for i in range(4):
        gpu.queue = [float(i + 1), i + 1.5]
        c.begin()
        c.mark()
        c.done(lambda at, i=i: got.append(i))
    # GPU never catches up: once three ticks wait, the oldest is waited for, so at most two stay queued
    assert got == [0, 1] and gpu.waited == [1.5, 2.5] and c.waits == 2
    c.flush()
    assert got == [0, 1, 2, 3]


def test_a_tick_without_generated_frames_waits_behind_the_one_before():
    gpu, got = _Gpu(), []
    c = _clock(gpu, t_cpu=[1.0, 2.0])
    gpu.queue = [1.0, 1.5]
    c.begin()
    c.mark()
    c.done(lambda at: got.append("interp"))
    gpu.queue = [2.0]
    c.begin()  # a repeat tick: no generated frame
    c.done(lambda at: got.append("repeat"))
    assert got == []
    gpu.now = 1.5
    c.poll()
    assert got == ["interp", "repeat"]


def test_tick_stats_from_the_clocks_times():
    gpu, ticks = _Gpu(), []
    c = _clock(gpu, t_cpu=[100.001])
    gpu.queue = [0.0, 0.004, 0.009]  # GPU times: only differences count
    st = Stats()
    st.actions = Counter()
    c.begin()
    c.mark()  # the 1/4 frame, presented early at CPU 100.002, with a real frame reaching the screen
    early = c.last()
    c.mark()  # the 1/2 frame, the tick's last
    shown = [(100.002, 99.990, early), (100.005, 99.980, None)]
    c.done(lambda at: _tick_stats(st, lambda *a: ticks.append(a), "tick", "diff", Action.INTERP, 100.0, 100.003,
                                  100.005, True, shown, at))
    c.flush()
    # processing: t0 100.000 -> the last frame done at 100.001 + 9 ms (the CPU was done queueing at 100.003)
    assert [round(x, 3) for x in st.proc_interp_ms] == [10.0] and st.proc_other_ms == []
    assert [round(x, 3) for x in st.busy_ms] == [10.0]  # the sink gave back (100.005) before the frame was done
    assert [round(x, 3) for x in st.sink_ms] == [0.0]
    # the early real frame: max(100.002, 100.005) - 99.990; the last: max(100.005, 100.010) - 99.980
    assert [round(x, 3) for x in st.real_delay_ms] == [15.0, 30.0]
    assert ticks and ticks[0][:3] == ("tick", "diff", Action.INTERP) and round(ticks[0][3], 3) == 10.0


def test_tick_stats_without_the_clock_are_the_cpus():
    st = Stats()
    c = TickClock(False)
    c.begin()
    c.done(lambda at: _tick_stats(st, None, None, None, Action.CURRENT, 1.0, 1.002, 1.007, False,
                                  [(1.007, 0.99, None)], at))
    assert [round(x, 3) for x in st.proc_other_ms] == [2.0]
    assert [round(x, 3) for x in st.busy_ms] == [7.0] and st.sink_ms == []
    assert [round(x, 3) for x in st.real_delay_ms] == [17.0]


# ---- the pipeline with a sink that takes GPU frames: nothing waits for them, and the stats still come out


class _DoneEvent:
    """A stand-in CUDA event that is done as soon as it's recorded, 3 ms of GPU time after the one before."""
    clock = 0.0

    def record(self):
        _DoneEvent.clock += 0.003
        self.t = _DoneEvent.clock

    def query(self):
        return True

    def synchronize(self):
        raise AssertionError("waited for a done event")

    def elapsed_time(self, end):
        return (end.t - self.t) * 1000


def _run(hz, gpu, monkeypatch):
    sys.path.insert(0, str(Path(__file__).parent))
    import test_120fps as t120
    from framegen import pipeline as pl
    from framegen.settings import Settings

    monkeypatch.setattr(pl, "TickClock", lambda on: TickClock(on, event=_DoneEvent))
    monkeypatch.setattr(pl.torch.cuda, "current_stream", lambda: 1 / 0)  # nothing may wait on the stream
    it, sink = t120._Interp(), t120._Sink()
    sink.gpu = gpu
    ticks = []
    st = t120._pipeline(it).run(t120._game(), sink, label="RIFE", on_tick=lambda *a: ticks.append(a),
                                settings=Settings({"hz": hz, "even_out": False, "overlay": False}))
    return st, sink.shown, ticks


def test_gpu_frames_show_the_same_and_keep_every_stat(monkeypatch):
    for hz in (60, 90, 120):
        st_cpu, shown_cpu, ticks_cpu = _run(hz, False, monkeypatch)
        st_gpu, shown_gpu, ticks_gpu = _run(hz, True, monkeypatch)
        assert shown_gpu == shown_cpu, hz
        n = sum(st_gpu.actions.values())
        assert len(st_gpu.proc_interp_ms) + len(st_gpu.proc_other_ms) == n == len(ticks_gpu) == len(ticks_cpu)
        assert len(st_gpu.busy_ms) == len(st_gpu.sink_ms) == n
        assert len(st_gpu.real_delay_ms) == len(st_cpu.real_delay_ms) > 0
        assert len(st_gpu.proc_interp_ms) == len(st_cpu.proc_interp_ms) > 0
        # each interp tick's processing reaches at least its last generated frame (3 ms of GPU time per event)
        assert min(st_gpu.proc_interp_ms) >= 3.0 - 1e-6
        assert [t[0].index for t in ticks_gpu] == sorted(t[0].index for t in ticks_gpu)  # on_tick in tick order
        assert all(b >= t[3] - 1e-6 for b, t in zip(st_gpu.busy_ms, ticks_gpu))  # busy covers each tick's GPU time
