"""A tick's in-between frames asked for together (RifeInterpolator.queue, Pipeline._order / _take): the GPU makes the
next one while the one before waits to be shown. CPU only: a stand-in interpolator that queues like the real one
(each frame made from the slots as they are when it's asked for, as on the interpolator's own CUDA stream), whose
frames blend their two real frames' numbers; and the real interpolator on the CPU, where the weights are there."""
import time

import numpy as np
import pytest
import torch

from framegen.interpolator import DEFAULT_MODEL_DIR, QUEUE_DEPTH, Queued
from framegen.io import Tick
from framegen.settings import Settings
from test_dips import _dipping_game, _Interp, _pipeline, _Sink


class _Event:
    def __init__(self, interp, t):
        self.interp, self.t = interp, t

    def synchronize(self):
        self.interp.log.append(("wait", self.t))
        self.interp.waiting -= 1  # (taken)


class _QInterp(_Interp):
    """_Interp that queues: the frame is worked out when it's asked for, and logged."""

    def __init__(self, log):
        super().__init__()
        self.log, self.waiting, self.most = log, 0, 0

    def prepare(self, slot):
        super().prepare(slot)
        self.log.append(("prepare", slot))
        self.waiting = 0  # (a frame asked for and not taken by now is dropped)

    def queue(self, slot, t=0.5):
        self.log.append(("queue", t))
        self.waiting += 1
        self.most = max(self.most, self.waiting)
        return Queued(super().interpolate(slot, t), _Event(self, t), t)

    def interpolate(self, slot, t=0.5):
        raise AssertionError("the pipeline queues when it can")


class _LogSink(_Sink):
    def __init__(self, log):
        super().__init__()
        self.log = log

    def write(self, frame, text=None):
        super().write(frame, text)
        self.log.append(("show", self.shown[-1]))


def _steady_game(n=160, late=()):
    """A 30 fps game on a 60 Hz stream; ticks in `late` arrive 40 ms before they're taken (the pipeline is behind)."""
    for i in range(n):
        yield Tick(torch.full((8, 8, 3), float(2 * (i // 2))), time.perf_counter() - (0.04 if i in late else 0.0), i,
                   new_hint=(i % 2 == 0))


def _run(interp, sink, game, hz, **settings):
    return _pipeline(interp).run(game, sink, label="RIFE", settings=Settings({"hz": hz, "overlay": False, **settings}))


def _both(game_fn, hz, **settings):
    """The same game through the stand-in that makes frames when taken, and the one that queues them."""
    it, sink = _Interp(), _Sink()
    st = _run(it, sink, game_fn(), hz, **settings)
    log = []
    qit = _QInterp(log)
    qsink = _LogSink(log)
    qst = _run(qit, qsink, game_fn(), hz, **settings)
    return (sink.shown, st), (qsink.shown, qst, log, qit)


@pytest.mark.parametrize("hz", [60, 90, 120])
@pytest.mark.parametrize("game, settings", [
    (lambda: _steady_game(), {"even_out": False}),
    (lambda: _steady_game(late=set(range(41, 160, 7))), {"even_out": False}),  # behind now and then
    (lambda: _dipping_game(300, 0.15, hold=4, steady=40), {}),
    (lambda: _dipping_game(300, 0.15, hold=4, steady=40), {"even_dips": True}),  # timed: Dip smoothing
    (lambda: _dipping_game(300, 0.15, hold=3, steady=40), {"game_fps": "30"}),
])
def test_queued_frames_show_what_frames_made_when_shown_do(hz, game, settings):
    (shown, st), (qshown, qst, _, qit) = _both(game, hz, **settings)
    assert qshown == shown
    assert (qst.quarter_shown, qst.quarter_skipped, qst.behind) == (st.quarter_shown, st.quarter_skipped, st.behind)
    assert dict(qst.actions) == dict(st.actions)
    assert (qst.dip_ticks, qst.uneven_waits) == (st.dip_ticks, st.uneven_waits)
    assert len(qst.real_delay_ms) == len(st.real_delay_ms)
    assert qit.most <= QUEUE_DEPTH


def _ticks(log):
    """The log split at each prepare: a new real frame's tick, and the repeat tick after it."""
    out, cur = [], []
    for e in log:
        if e[0] == "prepare" and cur:
            out.append(cur)
            cur = []
        cur.append(e)
    return out[2:-1]  # (not the first, or the last, cut short)


@pytest.mark.parametrize("hz, ts", [(120, [0.25, 0.5, 0.75]), (90, [1 / 3, 2 / 3])])
def test_a_ticks_frames_are_all_asked_for_before_the_first_is_shown(hz, ts):
    _, (_, st, log, _) = _both(lambda: _steady_game(), hz, even_out=False)
    assert st.quarter_skipped == 0 and st.quarter_shown > 40
    for tick in _ticks(log):
        first_show = next(k for k, e in enumerate(tick) if e[0] in ("wait", "show"))
        assert [e[1] for e in tick[:first_show] if e[0] == "queue"] == ts, tick
        assert not any(e[0] == "queue" for e in tick[first_show:]), tick  # the repeat tick's 3/4 is ready-made


@pytest.mark.parametrize("hz", [60, 90, 120])
def test_each_frame_waits_for_its_own_event_only(hz):
    _, (_, _, log, _) = _both(lambda: _steady_game(), hz, even_out=False)
    shows = [k for k, e in enumerate(log) if e[0] == "show"]
    asked = {}
    for a, b in zip(shows, shows[1:]):
        between = log[a + 1:b + 1]
        waits = [e[1] for e in between if e[0] == "wait"]
        assert len(waits) <= 1, between
        if waits:  # an AI frame: the one waited for is the one shown (its number is the blend at that t)
            asked[waits[0]] = asked.get(waits[0], 0) + 1
            frac = between[-1][1] % 2 / 2
            assert abs(frac - waits[0]) < 1e-4, between  # (float32)
    assert set(asked) == {60: {0.5}, 90: {1 / 3, 2 / 3}, 120: {0.25, 0.5, 0.75}}[hz]


def test_when_behind_the_3_4_frame_is_not_asked_for_ahead():
    late = set(range(40, 160, 4))  # every other new frame's tick
    _, (_, st, log, _) = _both(lambda: _steady_game(late=late), 120, even_out=False)
    assert st.quarter_skipped > 10 and st.quarter_shown > 10
    for tick in _ticks(log):
        queued = [e[1] for e in tick if e[0] == "queue"]
        assert queued in ([0.25, 0.5, 0.75], [0.5], [0.5, 0.75]), tick  # behind: 1/2 now, 3/4 when it's due


# ---- the real interpolator, on the CPU
needs_weights = pytest.mark.skipif(not (DEFAULT_MODEL_DIR / "flownet_v4.22.lite.pkl").exists(),
                                   reason="RIFE weights not downloaded")


def _scene(i, w=64, h=64):
    img = np.tile(np.linspace(20, 120, w, dtype=np.float32)[None, :, None], (h, 1, 3))
    img[20:40, 8 + 4 * i:24 + 4 * i] = (60, 160, 250)
    img[2:6, 2:30] = 230  # a still HUD bar, for Steady HUD
    return img.astype(np.uint8)


@needs_weights
@pytest.mark.parametrize("hdr", [False, True])
@pytest.mark.parametrize("steady", [0.0, 0.03])
def test_rife_queue_keeps_each_timestep_in_its_own_buffer(hdr, steady):
    from framegen.interpolator import RifeInterpolator
    r = RifeInterpolator(64, 64, "4.22.lite", 1.0, fp16=False, cuda_graph=False, device="cpu", hdr=hdr)
    for slot, i in ((0, 0), (1, 2)):
        f = torch.from_numpy(_scene(i))
        r.load(f.float().div(255).half() if hdr else f)
        r.prepare(slot)
    r.set_steady(steady)
    ts = (0.25, 0.5, 0.75)
    queued = [r.queue(1, t) for t in ts]
    assert len({id(q.frame) for q in queued}) == QUEUE_DEPTH == 3
    for q, t in zip(queued, ts):
        assert q.t == t and q.frame.dtype == r.frame_dtype
        assert torch.equal(q.frame, r.interpolate(1, t).clone())  # each kept its own t, after the ones asked later
    assert not torch.equal(queued[0].frame, queued[2].frame)
    again = r.queue(1, 0.5)
    assert again.frame is queued[0].frame  # the ring: reused QUEUE_DEPTH frames later


def test_score_judges_the_queued_frame_shown_not_the_interpolators_last():
    """--score and test mode: at 120 Hz the 3/4 frame is asked for after the 1/2 one, so out_u8 (or the newest
    buffer) holds the 3/4 frame by the time the 1/2 one is scored."""
    from types import SimpleNamespace

    from framegen.pipeline import Stats
    it = _Interp()
    p = _pipeline(it)
    a, b = torch.zeros(8, 8, 3, dtype=torch.uint8), torch.full((8, 8, 3), 20, dtype=torch.uint8)
    it.in_u8 = b
    it.out_u8 = torch.full((8, 8, 3), 15, dtype=torch.uint8)  # (the 3/4 frame)
    shown = torch.full((8, 8, 3), 10, dtype=torch.uint8)  # the 1/2 frame, as the real one in between
    tick = SimpleNamespace(content=4, index=4, frame=b.numpy())
    st = Stats()
    p._score(st, tick, a, a.numpy(), 2, lambda c: shown.numpy(), keep_worst=True, out=shown)
    assert st.psnr_interp == [float("inf")] or st.psnr_interp[0] > 90
    assert np.array_equal(st.worst[3], shown.numpy())
