"""Replay the same synthetic desktop through two SmoovPlay checkouts.

python scripts/xbox_cadence_replay.py --repo ../original --out before.json
python scripts/xbox_cadence_replay.py --repo . --out after.json --trace beats.csv

Use --capture dda to simulate the CUDA capture path's rejection of out-of-window
desktop updates. Both fixtures use the same stream and monitor times. There is
no console, GPU, codec, DWM or scanout in these timings. Time advances only when
the clock asks to sleep. Content IDs stand in for RIFE output at the actual
fractions selected by Pipeline.run. See metric definitions in the JSON.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left
import contextlib
import csv
import hashlib
import inspect
import json
import math
import random
import struct
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import numpy as np

SEED = 40
START = 1.0
H, W = 64, 96
# Each stage has a two-second run-in. One extra second at the end drains display lag.
STAGES = (("quality", 0, 1200, 30), ("menu", 1200, 1680, 60), ("return", 1680, 2160, 30))


@dataclass(frozen=True)
class Event:
    refresh: int
    stream: int
    content: int
    redraw: bool
    monitor_hz: int

    @property
    def time(self):
        return START + self.refresh / self.monitor_hz


def fixture(capture, monitor_hz, seed):
    rng = random.Random(seed)
    packets = []
    for n in range(STAGES[-1][2] + 60):
        # Bounded excursions of one desktop period about the ideal stream time.
        # Quantization gives 1-5 refresh intervals between Xbox app presents.
        refresh = round(n * monitor_hz / 60) + (rng.choice((-1, 0, 1)) if n and monitor_hz > 60 else 0)
        refresh = max(refresh, packets[-1].refresh + 1) if packets else refresh
        content = n if 1200 <= n < 1680 else n // 2 * 2
        packets.append(Event(refresh, n, content, False, monitor_hz))
    occupied = {e.refresh for e in packets}
    free = sorted(set(range(packets[-1].refresh)) - occupied)
    recaptures = set(rng.sample(free, min(len(free), round(len(packets) * 0.28 / 0.72)))) if capture == "ddagrab" else set()
    by_refresh = {e.refresh: e for e in packets}
    out = []
    for r in sorted(occupied | recaptures):
        if r in by_refresh:
            app = by_refresh[r]
            out.append(app)
        else:
            out.append(Event(r, app.stream, app.content, True, monitor_hz))
    return out


def set_position(a, value):
    a.reshape(-1)[:4] = np.frombuffer(struct.pack("<f", value), dtype=np.uint8)


def position(a):
    return struct.unpack("<f", a.reshape(-1)[:4].numpy().tobytes() if hasattr(a, "numpy")
                         else a.reshape(-1)[:4].tobytes())[0]


def picture(event):
    # A translated, low-contrast band pattern. Digital redraws are identical,
    # decoded copies of the same game frame have a small two-level residual.
    x = np.arange(W)
    stripe = 55 + ((x + 3 * event.content) % 64) + 2 * (event.stream - event.content)
    a = np.broadcast_to(stripe[None, :, None], (H, W, 3)).astype(np.uint8, order="C")
    set_position(a, event.content)
    return a


class VirtualCapture:
    """Use the real Clock60 reader thread. Before each scheduler beat, wait for its
    for-loop to finish ingesting all captures due by then. No OS sleep jitter."""
    xbox_window = True

    def __init__(self, events, capture):
        from framegen.sources import source_info
        self.events = events
        self.info = source_info("window", name="Xbox app window", hz=float(events[0].monitor_hz))
        self.path = "CPU" if capture == "ddagrab" else "GPU"
        self.cond = threading.Condition()
        self.blocked_at = -math.inf
        self.now = events[0].time

    def perf_counter(self):
        return self.now

    def sleep(self, duration):
        with self.cond:
            self.now += duration
            self.cond.notify_all()
            if not self.cond.wait_for(lambda: self.blocked_at > self.now + 1e-9, timeout=10):
                raise TimeoutError("Clock60 reader did not finish delivering this beat's captures")

    def __iter__(self):
        from framegen.io import Tick
        import torch
        for i, e in enumerate(self.events):
            with self.cond:
                self.blocked_at = e.time
                self.cond.notify_all()
                self.cond.wait_for(lambda: e.time <= self.now + 1e-9)
            a = picture(e)
            yield Tick(torch.from_numpy(a) if self.path == "GPU" else a, e.time, i,
                       content=e.content, extra={"capture_id": i})
        with self.cond:
            self.blocked_at = math.inf
            self.cond.notify_all()


class TokenInterpolator:
    """Carry the actual pair/fraction through Pipeline's CPU sink; no GPU timing model."""
    h, w = H, W

    def __init__(self):
        self.slots = [None, None]
        self.pair = None

    def load(self, frame):
        self.in_u8 = frame

    def prepare(self, slot):
        self.slots[slot] = position(self.in_u8)

    def interpolate(self, slot, t):
        import torch
        a, b = self.slots[1 - slot], self.slots[slot]
        self.pair = (a, b, t)
        marker = np.zeros((H, W, 3), dtype=np.uint8)
        set_position(marker, a + (b - a) * t)
        return torch.from_numpy(marker)


def replay(events, capture, enabled):
    import framegen.pipeline as pp
    from framegen.sources import Clock60, make_detector
    source = VirtualCapture(events, capture)
    clock = Clock60(source, clock=source.perf_counter, sleep=source.sleep)
    it = TokenInterpolator()
    pipe = pp.Pipeline(it, make_detector(source.info))
    rows = []
    last_uneven = 0
    fps = pp.GameRate()

    def on_tick(tick, diff, action, proc, now):
        nonlocal last_uneven
        new = diff.is_new if tick.new_hint is None else tick.new_hint
        fps.add(new)
        uneven = pipe.stats.uneven_ticks
        capture_id = tick.extra.get("capture_id")
        rows.append(dict(mode="rife_schedule" if enabled else "fg_off", tick=tick.index,
                         capture_id=capture_id, selected_content=tick.content,
                         capture_ms=None if capture_id is None else round((events[capture_id].time - START) * 1000, 4),
                         beat_ms=round((now - START) * 1000, 4), new=new, estimated_fps=fps.fps(),
                         even_out=uneven > last_uneven, action=action.value))
        last_uneven = uneven

    class Sink:
        def write(self, frame, label):
            pair = it.pair
            rows[-1].update(output=position(frame), a=pair[0] if pair else None, b=pair[1] if pair else None,
                            fraction=pair[2] if pair else None, submit_ms=rows[-1]["beat_ms"])
            it.pair = None

    with patch.object(pp, "time", source), contextlib.redirect_stdout(sys.stderr):
        kwargs = {"live_native_60": True} if "live_native_60" in inspect.signature(pipe.run).parameters else {}
        pipe.run(clock, Sink(), enabled=enabled, hz=60, on_tick=on_tick, **kwargs)
    return rows


def pct(values, q):
    return round(float(np.percentile(values, q)), 3) if len(values) else None


def summarize(rows, first, lo, hi):
    # Margin at interval boundaries: score frames by when they originated;
    # look across boundaries for the first presentation that reached them.
    keys = sorted(i for i in first if lo <= i < hi)
    first_real = {}
    reached = {}
    k = 0
    for r in rows:
        if r["a"] is None and r["output"].is_integer():
            first_real.setdefault(int(r["output"]), r["submit_ms"])
        while k < len(keys) and r["output"] + 0.00001 >= keys[k]:
            reached[keys[k]] = r["submit_ms"]
            k += 1
    delays = [reached[i] - first[i] for i in keys if i in reached]
    real_delays = [first_real[i] - first[i] for i in keys if i in first_real]
    local = [r for r in rows if lo * 1000 / 60 - 0.001 <= r["beat_ms"] < hi * 1000 / 60 - 0.001]
    picked = {r["selected_content"] for r in rows}
    changed = [r["submit_ms"] for prev, r in zip(local, local[1:]) if abs(r["output"] - prev["output"]) > 0.001]
    waits = [r["beat_ms"] - r["capture_ms"] for r in local if r["capture_ms"] is not None]
    beat_times = [r["beat_ms"] for r in rows]
    deferred = []
    for r in local:
        if r["capture_ms"] is not None:
            eligible = bisect_left(beat_times, r["capture_ms"] - 0.0002)
            deferred.append(max(0.0, r["beat_ms"] - beat_times[eligible]))
    deferred_only = [ms for ms in deferred if ms > 0.001]
    rates = [r["estimated_fps"] for r in local if r["estimated_fps"] is not None]
    return dict(content_frames=len(keys), output_events=len(local),
                display_position_delay_p50_ms=pct(delays, 50), display_position_delay_p95_ms=pct(delays, 95),
                real_draw_delay_p50_ms=pct(real_delays, 50), real_draw_delay_p95_ms=pct(real_delays, 95),
                missed_by_clock=sum(i not in picked for i in keys), never_drawn_as_original=len(keys)-len(real_delays),
                submit_interval_p50_ms=pct(np.diff([r["submit_ms"] for r in local]), 50),
                submit_interval_p95_ms=pct(np.diff([r["submit_ms"] for r in local]), 95),
                interval_with_motion_p50_ms=pct(np.diff(changed), 50),
                interval_with_motion_p95_ms=pct(np.diff(changed), 95),
                identical_motion_steps=sum(abs(r["output"] - p["output"]) <= 0.001 for p, r in zip(local, local[1:])),
                backward_steps=sum(r["output"] < p["output"] - 0.001 for p, r in zip(local, local[1:])),
                capture_to_beat_p50_ms=pct(waits, 50), capture_to_beat_p95_ms=pct(waits, 95),
                deferred_captures=len(deferred_only), capture_wait_extra_p95_ms=pct(deferred, 95),
                capture_wait_extra_max_ms=round(max(deferred_only, default=0.0), 3),
                deferred_only_p50_ms=pct(deferred_only, 50), deferred_only_p95_ms=pct(deferred_only, 95),
                even_out_ticks=sum(r["even_out"] for r in local), detector_fps_p50=pct(rates, 50),
                detector_fps_p05=pct(rates, 5), detector_fps_p95=pct(rates, 95),
                interpolated_events=sum(r["a"] is not None for r in local))


def check(before, after):
    if before["fixture"] != after["fixture"]:
        raise ValueError("Baseline and candidate used different fixtures")
    old, new = (r["results"]["rife_schedule"] for r in (before, after))
    for name in ("quality", "return"):
        a, b = old[name], new[name]
        assert 29 <= b["detector_fps_p50"] <= 31 and b["even_out_ticks"] == 0, (name, "30 fps classification")
        assert b["never_drawn_as_original"] == 0 and b["backward_steps"] == 0, (name, "originals in order")
        assert b["display_position_delay_p95_ms"] <= a["display_position_delay_p95_ms"] + 0.001, (name, "p95 delay")
        assert b["interval_with_motion_p95_ms"] <= a["interval_with_motion_p95_ms"] + 0.5, (name, "motion interval")
    assert new["menu"]["interpolated_events"] == 0, "Generated frames in the steady native menu"
    assert new["menu"]["missed_by_clock"] <= old["menu"]["missed_by_clock"], "Missed native menu frames"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="import production code from this exact checkout")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--baseline", type=Path, help="check 30 fps timing/latency and 60 fps menu against earlier JSON")
    ap.add_argument("--trace", type=Path, help="optional CSV of all beat and presentation IDs/times")
    ap.add_argument("--capture", choices=["ddagrab", "dda"], default="ddagrab")
    ap.add_argument("--monitor-hz", type=int, choices=(60, 120, 144, 175), default=175,
                    help="simulated desktop refresh; 60 gives regular input, higher rates add quantized jitter")
    ap.add_argument("--seed", type=int, default=SEED, help="seed for arrival jitter and desktop captures")
    args = ap.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    import torch
    torch.set_num_threads(1)
    events = fixture(args.capture, args.monitor_hz, args.seed)
    first = {}
    for e in events:
        first.setdefault(e.content, (e.time - START) * 1000)
    control = [dict(tick=None, selected_content=c, output=float(c), a=None, b=None, new=True,
                    estimated_fps=None, even_out=False, capture_ms=ms, beat_ms=ms, submit_ms=ms)
               for c, ms in first.items()]
    modes = {"xbox_fixture": control}
    for enabled, mode in [(False, "fg_off"), (True, "rife_schedule")]:
        modes[mode] = replay(events, args.capture, enabled)
    digest = hashlib.sha256(repr(events).encode()).hexdigest()
    app_gaps = np.diff([e.time for e in events if not e.redraw]) * 1000
    output = dict(fixture=dict(seed=args.seed, event_sha256=digest, events=len(events), capture=args.capture,
                               exact_desktop_redraws=sum(e.redraw for e in events), simulated_monitor_hz=args.monitor_hz,
                               logical_output_size=[2560, 1440], timing_fixture_pixels=[W, H],
                               app_interval_min_ms=round(float(min(app_gaps)), 3),
                               app_interval_max_ms=round(float(max(app_gaps)), 3),
                               stock_window_received_mbps=None, processing_and_display_cost_ms=0),
                  definitions=dict(display_position_delay="First output reaching or passing a source content ID "
                                   "minus first app desktop arrival; virtual sink submit. Includes skipped originals.",
                                   real_draw_delay="First exact, non-generated presentation of each source content ID "
                                   "minus first app desktop arrival. Excludes originals never drawn.",
                                   missed_by_clock="Source content IDs never selected on a scheduler beat.",
                                   interval_with_motion="Virtual time between output content position changes, "
                                   "including interpolated advances. Unchanged positions lengthen the interval.",
                                   capture_to_beat="Scheduler waiting from each fresh selected capture to handoff.",
                                   capture_wait_extra="Time a fresh capture was deferred beyond its first eligible "
                                   "scheduler beat; zero when latest-only; excludes captures never selected.",
                                   xbox_fixture="Unprocessed synthetic app arrivals, zero delay by construction.",
                                   stages="Exclude 2 s run-in per phase; transition windows score their first second."),
                  results={})
    for mode, rows in modes.items():
        result = output["results"][mode] = {}
        for name, start, end, hz in STAGES:
            result[name] = summarize(rows, first, start + 120, end)
            if start:
                result[name + "_first_second"] = summarize(rows, first, start, start + 60)
    smooth = modes["rife_schedule"]
    i = next(i for i, r in enumerate(smooth) if r["tick"] >= 240 and r["action"] == "interp" and
             r["a"] != r["b"] and r["capture_id"] is not None)
    output["example"] = smooth[i - 1:i + 3]
    args.out.write_text(json.dumps(output, indent=2, allow_nan=False) + "\n")
    if args.baseline:
        check(json.loads(args.baseline.read_text()), output)
    if args.trace:
        rows = modes["fg_off"] + smooth
        with args.trace.open("w", newline="") as f:
            log = csv.DictWriter(f, fieldnames=rows[0])
            log.writeheader()
            log.writerows(rows)


if __name__ == "__main__":
    main()
