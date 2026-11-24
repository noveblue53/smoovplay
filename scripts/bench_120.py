"""Can we do 30 -> 120 fps? Speed and quality of 3 in-between frames per real frame.

Speed: per real frame, encode it once and generate t = 1/4, 1/2, 3/4 (each copied to the CPU, as the
pipeline would), on real video frames. The budget at 120 Hz is 33.3 ms per real frame, minus four
screen updates. "queued" asks for all three at once (RifeInterpolator.queue, as the pipeline does) and
copies each out once its own event has passed, so the GPU makes the next while one is copied; "first"
is when the 1/4 frame is on the CPU, which queueing must not make later.

Quality, without a 120 fps recording: from a 60 fps clip keep every 4th frame (a 15 fps "game"),
generate the 3 frames in between and score them against the real ones. That is the same job as
30 -> 120 with twice the motion per step, so it's pessimistic. The 30 -> 60 score on the same frames
(every 2nd frame, t = 1/2) is printed alongside for reference.

    python scripts/bench_120.py testdata/clip_1080p60.mp4 testdata/another_1080p60.mp4 --scales 1.0 0.5
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import av
import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from framegen.interpolator import RifeInterpolator  # noqa: E402
from framegen.pipeline import psnr  # noqa: E402

TS = (0.25, 0.5, 0.75)


def stream(path: str, start: float = 0.0, seconds: float = 1e9):
    with av.open(path) as c:
        vs = c.streams.video[0]
        if start:
            c.seek(int(start / vs.time_base), stream=vs)
        for fr in c.decode(vs):
            if fr.time is not None and fr.time < start:
                continue
            if fr.time is not None and fr.time >= start + seconds:
                return
            yield fr.to_ndarray(format="bgr24")


def groups(path: str, start: float, seconds: float):
    """Overlapping runs of 5 frames: real frame, 3 in-between frames, next real frame."""
    buf = []
    for img in stream(path, start, seconds):
        buf.append(img)
        if len(buf) == 5:
            yield buf
            buf = buf[4:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("videos", nargs="+")
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--speed-frames", type=int, default=60, help="real frames timed per setting")
    ap.add_argument("--scales", nargs="+", type=float, default=[1.0, 0.5])
    ap.add_argument("--model", default="4.22.lite")
    a = ap.parse_args()

    w, h = 1920, 1080
    with av.open(a.videos[0]) as c:
        w, h = c.streams.video[0].codec_context.width, c.streams.video[0].codec_context.height
    pinned = torch.empty((h, w, 3), dtype=torch.uint8).pin_memory()
    out_pinned = torch.empty((h, w, 3), dtype=torch.uint8).pin_memory()
    print(f"{torch.cuda.get_device_name(0)}  {w}x{h}  RIFE {a.model}")
    m = lambda xs: float(np.mean([x for x in xs if np.isfinite(x)]))  # noqa: E731

    for scale in a.scales:
        it = RifeInterpolator(w, h, a.model, scale)

        def load(img, slot):
            np.copyto(pinned.numpy(), img)
            it.load(pinned)  # asynchronous: wait for it before the pinned buffer is reused
            torch.cuda.current_stream().synchronize()
            it.prepare(slot)

        # ---- speed: per real frame, 1 midpoint (60 Hz output) vs 3 in-betweens (120 Hz), downloads included
        for name, ts, queued in (("1 frame (60 Hz)", (0.5,), False), ("3 frames (120 Hz)", TS, False),
                                 ("3 queued (120 Hz)", TS, True)):
            times, firsts = [], []
            for k, g in enumerate(groups(a.videos[0], a.start, 1e9)):
                if k == 0:
                    load(g[0], 0)
                slot = (k + 1) % 2
                torch.cuda.synchronize()
                t0 = time.perf_counter()
                load(g[4], slot)
                if queued:
                    for q in [it.queue(slot, t) for t in ts]:
                        q.event.synchronize()  # this frame only
                        out_pinned.copy_(q.frame)
                        if q.t == ts[0]:
                            firsts.append((time.perf_counter() - t0) * 1000)
                else:
                    for t in ts:
                        out_pinned.copy_(it.interpolate(slot, t))  # blocking copy to the CPU, as the pipeline does
                        if t == ts[0]:
                            firsts.append((time.perf_counter() - t0) * 1000)
                times.append((time.perf_counter() - t0) * 1000)
                if len(times) == a.speed_frames + 5:
                    break
            x, f = np.array(times[5:]), np.array(firsts[5:])
            print(f"scale {scale}: {name:<18} per real frame  p50 {np.median(x):5.1f} ms  p95 {np.percentile(x, 95):5.1f} ms"
                  f"   first p50 {np.median(f):5.1f} ms")

        # ---- quality
        for video in a.videos:
            sc = {t: {"rife": [], "blend": [], "repeat": []} for t in TS}
            mid60 = {"rife": [], "blend": [], "repeat": []}
            for g in groups(video, a.start, a.seconds):
                gpu = [torch.from_numpy(x).cuda() for x in g]
                load(g[0], 0)
                load(g[4], 1)
                for j, t in enumerate(TS, 1):
                    sc[t]["rife"].append(psnr(it.interpolate(1, t), gpu[j]))
                    sc[t]["blend"].append(psnr((gpu[0].float() * (1 - t) + gpu[4].float() * t).round(), gpu[j]))
                    sc[t]["repeat"].append(psnr(gpu[0], gpu[j]))  # a 15 fps game shows the old frame
                for p, q in ((0, 2), (2, 4)):  # 30 -> 60 on the same frames
                    load(g[p], 0)
                    load(g[q], 1)
                    mid60["rife"].append(psnr(it.interpolate(1, 0.5), gpu[p + 1]))
                    mid60["blend"].append(psnr(((gpu[p].float() + gpu[q].float()) * 0.5).round(), gpu[p + 1]))
                    mid60["repeat"].append(psnr(gpu[p], gpu[p + 1]))
            allr = [x for t in TS for x in sc[t]["rife"]]
            print(f"scale {scale}: {Path(video).stem}, {len(sc[0.5]['rife'])} groups.  dB: RIFE | blend | repeat")
            for t in TS:
                print(f"    15->60  t={t:<4}  {m(sc[t]['rife']):5.2f} | {m(sc[t]['blend']):5.2f} | {m(sc[t]['repeat']):5.2f}")
            print(f"    15->60  all 3     {m(allr):5.2f} | {m([x for t in TS for x in sc[t]['blend']]):5.2f} | "
                  f"{m([x for t in TS for x in sc[t]['repeat']]):5.2f}   (worst RIFE {min(allr):.2f})")
            print(f"    30->60  t=0.5     {m(mid60['rife']):5.2f} | {m(mid60['blend']):5.2f} | {m(mid60['repeat']):5.2f}")
        del it
        torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    sys.exit(main())
