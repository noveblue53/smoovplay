"""Window trace provenance when a generated midpoint precedes the real image by one beat."""
import csv
import io
from types import SimpleNamespace

from framegen.window_trace import WindowTrace


def test_window_trace_tracks_real_content_across_clock_repeats_and_native_menu():
    file = io.StringIO()
    trace = WindowTrace(file, origin=0.0)
    frame = SimpleNamespace(extra={}, t_source=None)
    for i, (new, action) in enumerate(((True, "current"), (False, "current"), (True, "interp"),
                                       (False, "current"), (True, "interp"), (True, "pending"))):
        at = i / 60
        frame.index, frame.t_arrival, frame.t_ready = i, at, at + .002
        frame.extra = dict(window_seq=i, window_ready=at + .001, window_backend="GPU") if new else {}
        trace.draw = ("FSR 1", 2560, 1440, at + .007, at + .008, at + .009)
        trace.tick(frame, 60, new, action, at + .003, at + .006, .5, None)
    rows = list(csv.DictReader(io.StringIO(file.getvalue())))
    midpoint, real = rows[2:4]
    assert midpoint["content_a_acquired_ms"] == "0.000"
    assert midpoint["content_b_acquired_ms"] == real["content_a_acquired_ms"] == "33.333"
    assert real["acquired_ms"] == real["capture_ready_ms"] == ""  # no fresh capture on that beat
    assert float(real["swap_return_ms"]) - float(midpoint["acquired_ms"]) > 16.666
    assert rows[-1]["content_a_acquired_ms"] == rows[-2]["content_b_acquired_ms"]
    assert rows[-1]["content_b_acquired_ms"] == ""


def test_timed_content_uses_selected_pair_and_doesnt_reuse_previous_swap():
    file = io.StringIO()
    trace = WindowTrace(file, origin=0)
    tick = SimpleNamespace(index=4, extra={}, t_arrival=.08, t_ready=.08)
    placed = SimpleNamespace(a=SimpleNamespace(t_arrival=.040), b=SimpleNamespace(t_arrival=.070), t=.75)
    trace.draw = ("no upscale", 2560, 1440, .082, .083, .084)
    trace.tick(tick, 60, True, "interp", .080, .081, .5, placed)
    trace.tick(tick, 60, False, "current", .095, .096, .5, None)
    timed, missing = csv.DictReader(io.StringIO(file.getvalue()))
    assert (timed["content_a_acquired_ms"], timed["content_b_acquired_ms"], timed["fraction"]) == (
        "40.000", "70.000", "0.75")
    assert missing["swap_return_ms"] == ""
