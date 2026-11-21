"""The Remote Play stream check, on made-up streams (CPU only)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from framegen.chiaki_tap import SizeRepeatDetector  # noqa: E402
from framegen.streamcheck import StreamCheck  # noqa: E402

TICK = 1 / 60


def play(pattern, seconds=300, fps_packets=60, lost_at=(), missing_at=(), md_new=5.0, md_rep=0.0, seed=0,
         size_says=None):
    """A stream through the real size check. pattern(i) -> True if packet i is a new frame. size_says(i, new) may
    override the size check's decision (a misread)."""
    rng = np.random.default_rng(seed)
    sc, det = StreamCheck(), SizeRepeatDetector()
    seq = 0
    for i in range(int(seconds * fps_packets)):
        seq += 1 + (i in missing_at)
        new = pattern(i)
        size = int(rng.normal(50_000, 8_000)) if new else int(rng.normal(2_000, 300))
        dec = det.update(size, False)
        if size_says is not None:
            dec = size_says(i, new, dec)
        md = md_new * rng.uniform(0.5, 1.5) if new else md_rep
        mx = md * 20 if new else md_rep * 5
        sc.add(i / fps_packets + rng.normal(0, 0.0005), seq, size, det.ref, dec, det.big, i in lost_at, md, mx)
    return sc.report()


def verdict(lines):
    return next(line for line in lines if line.startswith("Verdict:"))


def test_locked_30_reads_correctly():
    lines = play(lambda i: i % 2 == 0)
    assert "read this 30 fps game correctly" in verdict(lines), lines
    assert any(line.startswith("Rhythm") and "100.0%" in line for line in lines), lines
    assert any("30 fps (median)" in line for line in lines), lines
    assert any("pixel-identical to the frame before: 100.0% of \"repeats\"" in line for line in lines), lines


def test_game_hitches_are_the_games_not_misreads():
    # Every 10 s the game takes 66 ms over a frame (new, repeat x3): the picture agrees, so it isn't a misread.
    def pattern(i):
        k = i % 600
        return k % 2 == 0 and not 589 < k < 592
    lines = play(pattern)
    assert "read this 30 fps game correctly" in verdict(lines), lines
    breaks = next(line for line in lines if line.strip().startswith("breaks:"))
    causes = dict(part.rsplit(" ", 1) for part in breaks.split(":", 1)[1].strip().split(", "))
    game = int(causes.get("the game itself (the picture agrees)", 0))
    assert game >= 0.9 * sum(map(int, causes.values())), breaks  # the rest: new frames drawn unusually small
    assert any("held frames:" in line and "longest" in line for line in lines), lines


def test_the_size_check_recovers_after_a_long_hold():
    # A long hold (the game pausing for a sixth of a second) used to leave the size check's window full of repeats,
    # so new frames looked far too big and went undecided for the rest of the session. Now many big frames recently
    # are the new normal (test_chiaki_tap's test_size_detector_recovers_after_a_still_stretch).
    def pattern(i):
        k = i % 600
        return k % 2 == 0 if k < 590 else k == 594
    lines = play(pattern)
    assert "read this 30 fps game correctly" in verdict(lines), lines
    assert "undecided" not in verdict(lines), lines


def test_misreads_are_caught_by_the_picture():
    # One repeat in 20 called new by size, while the picture didn't change at all: 2.5% of frames.
    def says(i, new, dec):
        return True if (not new and i % 40 == 1) else dec
    lines = play(lambda i: i % 2 == 0, size_says=says)
    assert "misread this 30 fps game too often" in verdict(lines), lines
    breaks = next(line for line in lines if line.strip().startswith("breaks:"))
    assert "picture says misread" in breaks, breaks


def test_a_few_misreads_is_mostly_right():
    def says(i, new, dec):
        return False if (new and i % 120 == 0) else dec  # 0.8% of frames
    lines = play(lambda i: i % 2 == 0, size_says=says)
    assert "mostly read this 30 fps game correctly" in verdict(lines), lines


def test_packet_loss_explains_breaks():
    lost = set(range(1000, 18000, 900))

    def says(i, new, dec):
        return (not new) if i in lost else dec  # the damaged frame is misread...
    lines = play(lambda i: i % 2 == 0, lost_at=lost, size_says=says, md_rep=0.0)
    breaks = next(line for line in lines if line.strip().startswith("breaks:"))
    assert "packet loss" in breaks, breaks  # ...but put down to the loss


def test_missing_frames_explain_breaks():
    missing = set(range(500, 18000, 1000))
    lines = play(lambda i: i % 2 == 0, missing_at=missing)
    assert any("never did" in line for line in lines), lines
    assert "read this 30 fps game correctly" in verdict(lines), lines


def test_60_fps_game():
    lines = play(lambda i: True)
    assert "about 60 fps" in verdict(lines), lines


def test_varying_frame_rate():
    rng = np.random.default_rng(1)
    pattern = rng.random(18000) < 0.75  # about 45 fps
    lines = play(lambda i: bool(pattern[i]))
    assert "frame rate varied" in verdict(lines), lines


def test_30_fps_stream():
    lines = play(lambda i: True, fps_packets=30)
    assert "stream itself is 30 fps" in verdict(lines), lines


def test_too_short():
    lines = play(lambda i: i % 2 == 0, seconds=30)
    assert "too short" in verdict(lines), lines


def test_still_scene_cant_be_confirmed():
    lines = play(lambda i: i % 2 == 0, md_new=0.1, md_rep=0.0)
    assert any(line.startswith("Picture check: too few") for line in lines), lines
    assert "couldn't confirm" in verdict(lines), lines


def test_test_mode_frames_are_left_out():
    sc = StreamCheck()
    det = SizeRepeatDetector()
    for i in range(4000):
        size = 50_000 if i % 2 == 0 else 2_000
        dec = det.update(size, False)
        tick = SimpleNamespace(truth_new=(i % 2 == 0) if i >= 3600 else None, t_arrival=i * TICK,
                               extra={"seq": i, "size": size, "size_ref": det.ref, "size_new": dec,
                                      "keyframe": False, "big": det.big, "frames_lost": 0, "recovered": False})
        sc.add_tick(tick, SimpleNamespace(mean_diff=5.0 if i % 2 == 0 else 0.0, max_diff=90.0 if i % 2 == 0 else 0.0))
    assert sc.simulated == 400
    assert "left out" in sc.report()[0]


def test_empty_and_capped():
    assert StreamCheck().report() == []
    sc = StreamCheck(max_ticks=10)
    for i in range(20):
        sc.add(i * TICK, i, 1000, None, None, False, False, 0.0, 0.0)
    assert sc.extra == 10 and len(sc.t) == 10


def test_size_detector_reports_its_reference():
    d = SizeRepeatDetector()
    for size in (50_000, 2_000, 50_000, 2_000):
        d.update(size, False)
    assert d.ref is not None and not d.big
    for _ in range(8):
        d.update(50_000, False)
    d.update(1_000_000, False)  # far bigger than anything recent: treated as a keyframe
    assert d.big and d.ref is None


def test_received_video_mbps_counts_payload_before_decoding_and_reports_incomplete_taps():
    sc = StreamCheck()
    sc.payload.add(1, 7, 1.0, 12345, False)  # left edge of the first measured interval
    sc.payload.add(1, 8, 1.5, 500_000, False)
    sc.payload.add(1, 8, 1.5, 500_000, False)  # a held slot read twice
    sc.payload.add(2, 0, 100.0, 54321, False)  # a new session: leave out the disconnected time
    sc.payload.add(2, 1, 100.5, 500_000, False)
    assert sc.report() == ["Received video payload: 8.00 Mbps over 1.00 s at the compressed tap"]
    sc.payload.add(2, 3, 101.5, 125_000, True)
    assert sc.report() == ["Received video payload: 4.50 Mbps over 2.00 s at the compressed tap"
                           " (lower bound: 1 skipped slots, 1 truncated)"]
