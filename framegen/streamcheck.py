"""Remote Play stream check: how well the size-based repeat check read a real game, in plain words.

The size check (chiaki_tap.SizeRepeatDetector) decides new frame or repeat from compressed size alone. The
pipeline goes by the picture (cadence.DuplicateDetector(learn=True)) since 0.3.4; the size check stays as a second
opinion, and for test mode. This records what it decided next to what the picture check measured, and reports,
for one session:
  - the game's frame rate, second by second, and the stream's;
  - the rhythm: a 30 fps game on a 60 Hz stream alternates new / repeat (N r N r ...), so a break is either the
    game itself (a hitch, a frame that came early) or a misread. Each break is tied to what was around it:
    frames missing, packet loss, a keyframe (big whatever it shows), a size close to the threshold;
  - the picture's view: in stretches where new frames and repeats clearly differ on screen, a "repeat" that
    changed like a new frame, or a "new" frame that changed no more than a repeat, looks misread;
  - how far apart the two sizes are, and when frames arrived.
One ~5 minute session of a native 30 fps game is enough to see whether the size check can be trusted.

Nothing here touches the GPU or the AI engine: it only keeps what the stream and the picture check already
measured, so it's safe in Copy diagnostics (no NVIDIA VFG numbers).
"""

from __future__ import annotations

from array import array

import numpy as np

TICK_MS = 1000 / 60
MAX_TICKS = 30 * 60 * 60  # the first 30 minutes; a check needs ~5
AMBIGUOUS = (0.25, 0.6)  # size / typical new frame, around SizeRepeatDetector's 0.4: where size can't really tell
STRETCH = 60  # frames calibrated together for the picture's view (1 s)


def _mmss(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def _n(mask) -> int:
    return int(np.count_nonzero(mask))


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "n/a"


class VideoPayloadRate:
    """Bytes read from compressed tap slots, before decoder or display drops. Start each session
    at its first arrival: its first payload precedes the measured interval. Gaps leave a lower bound.
    """

    def __init__(self) -> None:
        self.session = self.seq = self.last = None
        self.bytes = self.gaps = self.truncated = 0
        self.seconds = 0.0

    def add(self, session: int, seq: int, t: float, size: int, truncated: bool) -> None:
        if session == self.session and seq <= self.seq:
            return  # a slot held while the decoder starts can be read twice
        if session == self.session:
            self.bytes += size
            self.gaps += seq - self.seq - 1
            self.truncated += int(truncated)
            self.seconds += max(0.0, t - self.last)
            t = max(self.last, t)  # near-simultaneous arrivals can round the other way
        self.session, self.seq, self.last = session, seq, t

    def report(self) -> str:
        if self.seconds <= 0:
            return ""
        lower = f" (lower bound: {self.gaps} skipped slots, {self.truncated} truncated)" if (
            self.gaps or self.truncated) else ""
        rate = self.bytes * 8 / (self.seconds * 1e6)
        return f"Received video payload: {rate:.2f} Mbps over {self.seconds:.2f} s at the compressed tap{lower}"


class StreamCheck:
    """Collects one row per Remote Play frame the pipeline saw; report() turns them into plain-language lines."""

    def __init__(self, max_ticks: int = MAX_TICKS) -> None:
        self.payload = VideoPayloadRate()
        self.max_ticks = max_ticks
        self.t = array("d")         # arrival, s
        self.seq = array("q")       # the tap's frame number: a jump means frames never reached the pipeline
        self.size = array("q")      # compressed bytes
        self.ref = array("d")       # the typical new frame size the check compared with (nan: keyframe, warm-up)
        self.decision = array("b")  # size check: 1 new, 0 repeat, -1 undecided (the picture check decided)
        self.key = array("b")       # keyframe, or a frame so big it was treated as one
        self.lost = array("b")      # frames were lost before it, or it's the PS5's recovery frame
        self.mean_diff = array("d")  # the picture check: average change from the frame before (0-255)
        self.max_diff = array("d")   # ... and the biggest change anywhere in the picture
        self.simulated = 0          # frames played with "Simulate a 30 fps game" on: not part of the check
        self.extra = 0              # frames past max_ticks

    def add(self, t: float, seq: int, size: int, ref: float | None, decision: bool | None, key: bool,
            lost: bool, mean_diff: float, max_diff: float) -> None:
        if len(self.t) >= self.max_ticks:
            self.extra += 1
            return
        self.t.append(t)
        self.seq.append(seq)
        self.size.append(size)
        self.ref.append(float("nan") if ref is None else ref)
        self.decision.append(-1 if decision is None else int(decision))
        self.key.append(int(key))
        self.lost.append(int(lost))
        self.mean_diff.append(mean_diff if np.isfinite(mean_diff) else float("nan"))
        self.max_diff.append(max_diff if np.isfinite(max_diff) else float("nan"))

    def add_tick(self, tick, diff) -> None:
        """From the pipeline's on_tick: a Tick from ChiakiSource and the picture check's FrameDiff."""
        if tick.truth_new is not None:  # test mode replaced this frame
            self.simulated += 1
            return
        e = tick.extra
        self.add(tick.t_arrival, e.get("seq", 0), e.get("size", 0), e.get("size_ref"), e.get("size_new"),
                 bool(e.get("keyframe") or e.get("big")), bool(e.get("frames_lost") or e.get("recovered")),
                 float(diff.mean_diff), float(diff.max_diff))

    # ------------------------------------------------------------------------------------------------- report
    def report(self) -> list[str]:
        n = len(self.t)
        bitrate = self.payload.report()
        if n < 2:
            return [bitrate] if bitrate else []
        t = np.frombuffer(self.t, dtype=np.float64)
        seq = np.frombuffer(self.seq, dtype=np.int64)
        size = np.frombuffer(self.size, dtype=np.int64)
        ref = np.frombuffer(self.ref, dtype=np.float64)
        dec = np.frombuffer(self.decision, dtype=np.int8)
        key = np.frombuffer(self.key, dtype=np.int8).astype(bool)
        lost = np.frombuffer(self.lost, dtype=np.int8).astype(bool)
        md = np.frombuffer(self.mean_diff, dtype=np.float64)
        mx = np.frombuffer(self.max_diff, dtype=np.float64)
        span = float(t[-1] - t[0])
        new, rep = dec == 1, dec == 0
        lines = [f"Stream check (Remote Play), {_mmss(span)} checked"
                 + (" (the first 30 minutes; later frames weren't recorded)" if self.extra else "")]
        if self.simulated:
            lines[0] += f"; {_mmss(self.simulated * TICK_MS / 1000)} with \"Simulate a 30 fps game\" on left out"

        if bitrate:
            lines.append(bitrate)

        # Frame rates, per whole second of play.
        edges = t[0] + np.arange(int(span) + 1)
        per_s = np.histogram(t[new], edges)[0] if len(edges) > 1 else np.zeros(0, dtype=np.int64)
        pkt_s = np.histogram(t, edges)[0] if len(edges) > 1 else np.zeros(0, dtype=np.int64)
        step = np.diff(seq)
        missing = int((step[step > 1] - 1).sum())
        stream_fps = float(np.median(pkt_s)) if len(per_s) else float("nan")
        game_fps = float(np.median(per_s)) if len(per_s) else float("nan")
        steady = int(np.count_nonzero((per_s >= 28) & (per_s <= 32)))
        if len(per_s):
            lines.append(f"Stream: {stream_fps:.0f} frames a second reached SmoovPlay"
                         + (f"; {missing} never did (packet loss, or SmoovPlay fell behind)" if missing else ""))
            lines.append(f"Game frame rate, by the size check: {game_fps:.0f} fps (median); "
                         f"{_pct(steady, len(per_s))} of seconds at 28-32 fps")
            bands = [("under 25", per_s < 25), ("25-27", (per_s >= 25) & (per_s < 28)),
                     ("28-32", (per_s >= 28) & (per_s <= 32)), ("33-45", (per_s > 32) & (per_s <= 45)),
                     ("46-55", (per_s > 45) & (per_s <= 55)), ("56+", per_s > 55)]
            lines.append("  seconds at: " + ", ".join(f"{name} fps {int(np.count_nonzero(m))}"
                                                       for name, m in bands if np.any(m)))
        thirty = 25 <= game_fps <= 35

        # The picture's view, calibrated per stretch: only where new frames and repeats clearly differ on screen.
        miss, extra_new, checked = _picture_flags(dec, md, mx, key)
        looks = miss | extra_new

        # The rhythm: consecutive frames, both decided, nothing missing between them.
        ok = (step == 1) & (dec[1:] >= 0) & (dec[:-1] >= 0)
        a, b = dec[:-1], dec[1:]
        pairs = int(np.count_nonzero(ok))
        alt = int(np.count_nonzero(ok & (a != b)))
        breaks = np.flatnonzero(ok & (a == b)) + 1  # the second frame of each break
        nn, rr = int(np.count_nonzero(dec[breaks] == 1)), int(np.count_nonzero(dec[breaks] == 0))
        lines.append(f"Rhythm (a 30 fps game alternates new / repeat): {_pct(alt, pairs)} of {pairs} frame pairs; "
                     f"{nn} new-new, {rr} repeat-repeat" + ("" if thirty else " (this game isn't at 30 fps, so "
                                                            "breaks are expected)"))
        holds = _runs(rep, seq)
        if holds:
            longest = max(holds) + 1
            n_long = sum(r >= 2 for r in holds)
            lines.append(f"  held frames: {n_long} times a frame stayed up 3 or more ticks (50 ms+), longest "
                         f"{longest} ticks ({longest * TICK_MS:.0f} ms)" if n_long else
                         "  held frames: none longer than 2 ticks")
        ratio = size / ref  # nan where there was no reference
        near = (ratio >= AMBIGUOUS[0]) & (ratio <= AMBIGUOUS[1])
        if thirty and len(breaks):
            causes = {"frames missing": 0, "packet loss": 0, "keyframe": 0, "size close to the threshold": 0,
                      "picture says misread": 0, "the game itself (the picture agrees)": 0, "can't tell": 0}
            for i in breaks:
                lo, hi = max(0, i - 2), min(n, i + 3)
                if np.any(step[max(0, i - 3):min(n - 1, i + 2)] != 1):
                    causes["frames missing"] += 1
                elif lost[lo:hi].any():
                    causes["packet loss"] += 1
                elif key[i] or key[i - 1]:
                    causes["keyframe"] += 1
                elif looks[i] or looks[i - 1]:
                    causes["picture says misread"] += 1
                elif near[i] or near[i - 1]:
                    causes["size close to the threshold"] += 1
                elif checked[i] and checked[i - 1]:
                    causes["the game itself (the picture agrees)"] += 1
                else:
                    causes["can't tell"] += 1  # a near-still stretch: the picture can't separate them either
            lines.append("  breaks: " + ", ".join(f"{k} {v}" for k, v in causes.items() if v))

        n_checked = int(np.count_nonzero(checked))
        n_looks = int(np.count_nonzero(looks))
        if n_checked >= 600:
            lines.append(f"Picture check, in stretches where the picture moves ({_pct(n_checked, n)} of frames): "
                         f"{n_looks} look misread ({_pct(n_looks, n_checked)}): {int(np.count_nonzero(miss))} "
                         f"\"repeats\" changed like a new frame, {int(np.count_nonzero(extra_new))} \"new\" frames "
                         "changed no more than a repeat")
        else:
            lines.append(f"Picture check: too few stretches where new frames and repeats clearly differ on screen "
                         f"({n_checked} frames; a still scene, or few repeats), so it can't confirm the size check")
        # Does the PS5 send true copies? Then the picture alone could tell repeats apart, big or small, keyframes
        # and still stretches included: for tuning the size check against this.
        same = np.isfinite(mx) & (mx <= 0.01)
        n_rep = int(np.count_nonzero(rep & ~key))
        if n_rep:
            lines.append(f"  pixel-identical to the frame before: {_pct(_n(same & rep & ~key), n_rep)} of \"repeats\", "
                         f"{_pct(_n(same & new & ~key), _n(new & ~key))} of \"new\" frames")
            if np.count_nonzero(same & rep & ~key) >= 0.9 * n_rep:
                agree = int(np.count_nonzero((new & ~same) | (rep & same)))
                lines.append(f"  the picture alone (pixel-identical = repeat) agrees with the size check on "
                             f"{_pct(agree, int(np.count_nonzero(dec >= 0)))} of frames, and would also decide the "
                             f"{int(np.count_nonzero(dec < 0))} the size check left undecided")

        # How far apart new frames and repeats are by size.
        sized = ~key & np.isfinite(ratio)
        rn, rp = ratio[sized & new], ratio[sized & rep]
        if len(rn) and len(rp):
            lines.append(f"Sizes: new frames {np.median(size[sized & new]) / 1000:.1f} KB, repeats "
                         f"{np.median(size[sized & rep]) / 1000:.1f} KB (medians). Against a typical new frame, the "
                         f"smallest 5% of new frames were {np.percentile(rn, 5):.2f}x and the biggest 5% of repeats "
                         f"{np.percentile(rp, 95):.2f}x (threshold 0.40x); {_pct(_n(near & sized), _n(sized))} "
                         f"of frames were close to it ({AMBIGUOUS[0]:.2f}-{AMBIGUOUS[1]:.2f}x)")
        key_runs = _runs(key, seq)
        lines.append(f"Undecided by size (the picture check decided): {int(np.count_nonzero(dec < 0))} frames; "
                     f"keyframes (or frames far bigger than recent ones) {int(np.count_nonzero(key))} "
                     f"({_pct(_n(key), n)})" + (f", longest run {max(key_runs)} in a row" if key_runs else ""))

        # When frames arrived.
        dt = np.diff(t)[step == 1] * 1000
        if len(dt):
            lines.append(f"Arrival: a frame every {np.median(dt):.1f} ms (median), 95% within "
                         f"{np.percentile(dt, 95):.1f} ms, slowest {dt.max():.0f} ms; "
                         f"{_pct(int(np.count_nonzero(dt > 25)), len(dt))} over 25 ms")
        if np.count_nonzero(new) > 2:
            dn = np.diff(t[new]) * 1000
            lines.append(f"  new frames: every {np.median(dn):.1f} ms (median), 95% within "
                         f"{np.percentile(dn, 95):.1f} ms")

        lines.append(_verdict(span, stream_fps, game_fps, steady, len(per_s), alt, pairs,
                              int(np.count_nonzero(looks)), n_checked, float(np.count_nonzero(dec < 0)) / n))
        return lines


def _picture_flags(dec, md, mx, key, stretch: int = STRETCH):
    """(miss, extra_new, checked) per frame, judged by the picture within each stretch of frames.

    Calibrated on the picture alone, never on the size check's own answers (which may be what's wrong): in a
    30 fps game half the frames are new, so the 75th percentile of the picture's change is a typical new frame
    and the 25th a typical repeat (the encoder sharpening a still picture, or nothing). A stretch counts only if
    those clearly differ: a typical new frame changes the picture by at least 0.5 on average, and 5 times more
    than a typical repeat. Then a "repeat" that changed at least half as much as a typical new frame is a likely
    miss, and a "new" frame that changed, on average and at its biggest, no more than twice a typical repeat is
    a likely extra.
    """
    n = len(dec)
    miss, extra, checked = np.zeros(n, bool), np.zeros(n, bool), np.zeros(n, bool)
    fin = np.isfinite(md) & np.isfinite(mx)  # keyframes' pictures count for calibrating, not for judging
    for s in range(0, n, stretch):
        sl = slice(s, s + stretch)
        d, m, x, f = dec[sl], md[sl], mx[sl], fin[sl]
        if np.count_nonzero(f) < stretch // 3:
            continue
        lo, typ_new = (float(v) for v in np.percentile(m[f], [25, 75]))
        typ_rep = max(lo, 0.02)
        if typ_new < 0.5 or typ_new < 5 * typ_rep:
            continue
        quiet = f & (m <= 2 * typ_rep)
        rep_max = max(float(np.median(x[quiet])) if np.any(quiet) else 0.0, 1.0)
        o = f & ~key[sl] & (d >= 0)
        checked[sl] = o
        miss[sl] = o & (d == 0) & (m >= 0.5 * typ_new)
        extra[sl] = o & (d == 1) & (m <= 2 * typ_rep) & (x <= 2 * rep_max)
    return miss, extra, checked


def _runs(mask: np.ndarray, seq: np.ndarray) -> list[int]:
    """Lengths of runs of True in mask, split where frames are missing."""
    out, run = [], 0
    for i, m in enumerate(mask):
        if i and seq[i] != seq[i - 1] + 1 and run:
            out.append(run)
            run = 0
        if m:
            run += 1
        elif run:
            out.append(run)
            run = 0
    if run:
        out.append(run)
    return out


def _verdict(span, stream_fps, game_fps, steady, seconds, alt, pairs, looks, checked, undecided) -> str:
    """undecided: the share of frames the size check left to the picture check."""
    if span < 60 or not seconds:
        return "Verdict: too short to judge; play for at least a minute (about 5 is best)"
    if stream_fps < 45:
        return ("Verdict: the stream itself is 30 fps, so there are no repeats to check. Set chiaki-ng's frame "
                "rate to 60 and play again")
    if checked < 600:
        reads = "the picture couldn't confirm it (too little movement)"
    else:
        reads = f"{_pct(looks, checked)} of moving frames look misread" if looks else "no frame looks misread"
    if game_fps >= 55:
        return (f"Verdict: this game ran at about 60 fps here, so there were few repeats to fill; {reads}. For this "
                "check, play a game (or a mode) that runs at 30 fps")
    if steady / seconds < 0.8:
        return (f"Verdict: this game's frame rate varied (about {game_fps:.0f} fps), so its rhythm can't be "
                f"checked; {reads}")
    rate = looks / checked if checked >= 600 else 0.0
    if undecided > 0.05:
        return (f"Verdict: the size check left {100 * undecided:.0f}% of this 30 fps game's frames undecided (the "
                f"picture check stood in; {reads}). Please send this report")
    if rate <= 0.005 and alt / max(pairs, 1) >= 0.97:
        return f"Verdict: the size check read this 30 fps game correctly ({_pct(alt, pairs)} on rhythm; {reads})"
    if rate <= 0.02:
        return (f"Verdict: the size check mostly read this 30 fps game correctly ({_pct(alt, pairs)} on rhythm; "
                f"{reads}). Please send this report")
    return f"Verdict: the size check misread this 30 fps game too often ({reads}). Please send this report"
