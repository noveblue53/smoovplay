"""Opt-in trace of the final output of each Clock60 beat. CPU timestamps share perf_counter's clock.

A/B identify the capture arrivals of the game content selected by the scheduler. On a generated
frame the output lies between A and B. A later capture of the same content can supply its real image.
OpenGL queues the draw: its start time and swap return don't measure GPU duration or scanout.
"""
import csv
import time


class WindowTrace:
    def __init__(self, file, origin=None):
        self.origin = time.perf_counter() if origin is None else origin
        self.writer = csv.writer(file)
        self.writer.writerow((
            "beat", "output_hz", "capture_seq", "capture_backend", "source_pts_s", "acquired_ms",
            "capture_ready_ms", "beat_ms", "process_start_ms", "output_ready_ms", "is_new", "action",
            "timed", "content_a_acquired_ms", "content_b_acquired_ms", "fraction",
            "scale", "output_width", "output_height", "draw_start_ms", "swap_start_ms", "swap_return_ms"))
        self.last_new = None
        self.draw = None

    def tick(self, tick, hz, is_new, action, started, ready, phase, placement):
        def stamp(t):
            return "" if t is None else f"{(t - self.origin) * 1000:.3f}"

        arrival = tick.t_arrival
        a, b, fraction = self.last_new, None, None
        if placement is not None:  # Even out / Dip smoothing selects from its own timeline
            a = placement.a.t_arrival
            if placement.b is not None:
                b, fraction = placement.b.t_arrival, placement.t
        elif action == "interp":
            b, fraction = arrival, phase
        elif action == "current" and is_new:
            a = arrival
        if is_new:
            self.last_new = arrival
        seq = tick.extra.get("window_seq")
        scale, w, h, draw, swap, returned = self.draw or (None,) * 6
        self.draw = None  # the next row requires a draw from that beat
        self.writer.writerow((
            tick.index, hz, seq, tick.extra.get("window_backend"), tick.t_source if seq is not None else None,
            stamp(arrival if seq is not None else None), stamp(tick.extra.get("window_ready")), stamp(tick.t_ready),
            stamp(started), stamp(ready), int(is_new), action, int(placement is not None),
            stamp(a), stamp(b), fraction, scale, w, h, stamp(draw), stamp(swap), stamp(returned)))
