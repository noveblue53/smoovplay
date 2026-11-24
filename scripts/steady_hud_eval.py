"""Steady HUD variants, scored side by side on 60 fps clips (no window; the GPU runs RIFE once per clip).

    .venv\\Scripts\\python scripts\\steady_hud_eval.py testdata\\hud\\*.mp4 [--seconds 30] [--engine trt|torch]

Each clip plays as a simulated 30 fps game (every other frame left out, then used to score). RIFE runs with Steady
HUD off; every variant is then applied to that same RIFE frame, so all are scored on identical frames:

  all     dB over the whole frame
  still   dB over pixels unchanged in both real frames (where Steady HUD acts)
  HUD     dB over HUD-like pixels: unchanged in both real frames and the real in-between one, on an edge
  off %   share of HUD-like pixels the frame misses by more than a few levels: the wobble a viewer sees

A clip that's really a 30 fps game in a 60 fps file (every other frame a copy) has nothing to score and is skipped.
Writes out\\steady_eval\\table.txt.
"""
from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from framegen.interpolator import STEADY_HUD_THRESHOLD, RifeInterpolator  # noqa: E402
from framegen.io import Ps5SimSource  # noqa: E402
from framegen.pipeline import HUD_OFF_LEVELS, STILL_LEVELS, Pipeline, _db, hud_mask  # noqa: E402


def _weight(d: torch.Tensor, thr: float) -> torch.Tensor:
    """interpolator._steady's soft edge, on 0..255 differences: 1 below half the threshold, 0 above it."""
    t = thr * 255.0
    return ((t - d) / (0.5 * t)).clamp_(0.0, 1.0)


def _pool(w: torch.Tensor, grow: bool) -> torch.Tensor:
    x = w.permute(2, 0, 1).unsqueeze(0)  # HxWx1 -> 1x1xHxW
    x = F.max_pool2d(x, 3, 1, 1) if grow else -F.max_pool2d(-x, 3, 1, 1)
    return x[0].permute(1, 2, 0)


_K: dict = {}


def edges_near(a: torch.Tensor, b: torch.Tensor, r: int, level: float) -> torch.Tensor:
    """HxWx1 0/1: within r pixels of an edge (Sobel L1 of brightness above `level`) in either real frame. Text, icons
    and map lines have them; flat sky or road doesn't, and flat areas don't show wobble anyway."""
    k = _K.get(a.device)
    if k is None:
        gx = torch.tensor([[-1.0, 0.0, 1.0], [-2.0, 0.0, 2.0], [-1.0, 0.0, 1.0]], device=a.device)
        k = _K[a.device] = (torch.stack([gx, gx.t()]).unsqueeze(1),
                            torch.tensor([0.114, 0.587, 0.299], device=a.device))
    sob, wts = k
    lum = torch.stack([(a * wts).sum(-1), (b * wts).sum(-1)]).unsqueeze(1)  # 2x1xHxW
    g = F.conv2d(F.pad(lum, (1, 1, 1, 1), mode="replicate"), sob).abs().sum(1, keepdim=True).amax(0, keepdim=True)
    e = (g > level).float()
    if r:
        e = F.max_pool2d(e, 2 * r + 1, 1, r)
    return e[0].permute(1, 2, 0)


def steady(thr: float = STEADY_HUD_THRESHOLD, three: bool = False, grow: bool | None = None,
           edge_r: int | None = None, edge_level: float = 128.0):
    """A Steady HUD variant: (mid, a, b, a0) -> frame. three: also unchanged since the real frame before a (a0).
    grow True/False: widen/narrow the still area by a pixel. edge_r: only within edge_r pixels of an edge."""
    def fn(mid, a, b, a0):
        d = (b - a).abs().amax(-1, keepdim=True)
        if three and a0 is not None:
            d = torch.maximum(d, (a - a0).abs().amax(-1, keepdim=True))
        w = _weight(d, thr)
        if grow is not None:
            w = _pool(w, grow)
        if edge_r is not None:
            w = w * edges_near(a, b, edge_r, edge_level)
        return mid + w * (a + 0.5 * (b - a) - mid)
    return fn


VARIANTS = {
    "RIFE alone": lambda mid, a, b, a0: mid,
    "now (0.03)": steady(),
    "3 frames": steady(three=True),
    "edges, 1px": steady(edge_r=1),
    "edges, 2px": steady(edge_r=2),
    "edges, 3px": steady(edge_r=3),
    "edges 2px, softer": steady(edge_r=2, edge_level=64.0),
    "edges 2px, 3 frames": steady(three=True, edge_r=2),
}
# "Passing" pixels: unchanged between the two real frames, yet something is there in the real in-between frame (more
# than PASS_LEVELS off): a pole, debris or a fast car crossing a background that matches before and after. Steady
# HUD paints the background back over them (a hole in the moving thing); "missed %" counts how often a frame does.
PASS_LEVELS = 24


class EvalPipeline(Pipeline):
    def __init__(self, interp: RifeInterpolator) -> None:
        super().__init__(interp)
        # summed squared error: all, still, HUD; then HUD pixels visibly off, passing pixels missed
        self.sums = {k: np.zeros(5) for k in VARIANTS}
        self.counts = np.zeros(4)  # pixels: all, still, HUD, passing
        self.frames = self.copies = 0
        self._a0 = self._a0_content = None

    def _score(self, st, tick, a, prev, prev_content, gt_fn, keep_worst=True, out=None) -> None:
        super()._score(st, tick, a, prev, prev_content, gt_fn, keep_worst=False, out=out)
        if prev_content is None or tick.content is None or tick.content - prev_content != 2:
            return
        gt_np = gt_fn(prev_content + 1)
        if gt_np is None:
            return
        dev = self.interp.device
        gt = torch.from_numpy(gt_np).to(dev).float()
        A, B = a.float(), self.interp.in_u8.float()
        mid = (self.interp.out_u8 if out is None else out).float()  # (out: the queued frame shown)
        a0 = self._a0 if self._a0_content == prev_content - 2 else None
        self._a0, self._a0_content = A, prev_content
        if (gt - A).abs().mean().item() < 0.5 and (B - gt).abs().mean().item() >= 0.5:
            self.copies += 1  # the "in-between" frame is a copy of A: a 30 fps game in a 60 fps file
        self.frames += 1
        still = ((B - A).abs().amax(-1, keepdim=True) <= STILL_LEVELS).float()
        hud = hud_mask(A, B, gt, still)
        passing = still * ((gt - A).abs().amax(-1, keepdim=True) > PASS_LEVELS).float()
        n = torch.stack([still.new_tensor(still.numel()), still.sum(), hud.sum(), passing.sum()]).tolist()
        self.counts += n
        for k, fn in VARIANTS.items():
            y = fn(mid, A, B, a0).round_().clamp_(0.0, 255.0)
            e = (y - gt).pow(2).mean(-1, keepdim=True)  # per pixel, over the three colours
            off = ((y - gt).abs().amax(-1, keepdim=True) > HUD_OFF_LEVELS).float()
            self.sums[k] += torch.stack([e.sum(), (e * still).sum(), (e * hud).sum(), (off * hud).sum(),
                                         (off * passing).sum()]).tolist()

    def rows(self) -> dict:
        return rows_of(self.sums, self.counts)


def rows_of(sums: dict, counts: np.ndarray) -> dict:
    c = np.maximum(counts, 1)
    return {k: (_db(s[0] / c[0]), _db(s[1] / c[1]), _db(s[2] / c[2]), 100 * s[3] / c[2], 100 * s[4] / c[3])
            for k, s in sums.items()}


def table(title: str, rows: dict, extra: str = "") -> str:
    base = rows["RIFE alone"]
    out = [f"{title}{extra}",
           f"  {'':22}{'all dB':>8}{'still dB':>10}{'HUD dB':>8}{'HUD off %':>11}{'passing missed %':>18}"
           "   vs RIFE: all / HUD dB"]
    for k, (al, s, h, off, miss) in rows.items():
        delta = "" if k == "RIFE alone" else f"   {al - base[0]:+.2f} / {h - base[2]:+.2f}"
        out.append(f"  {k:22}{al:8.2f}{s:10.2f}{h:8.2f}{off:11.2f}{miss:18.1f}{delta}")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clips", nargs="+")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--engine", default="trt", choices=["trt", "torch"])
    a = ap.parse_args()
    paths = [p for c in a.clips for p in (sorted(glob.glob(c)) or [c])]
    out = ROOT / "out" / "steady_eval"
    out.mkdir(parents=True, exist_ok=True)
    interp = None
    per_clip, totals = [], {k: np.zeros(5) for k in VARIANTS}
    counts = np.zeros(4)
    for path in paths:
        src = Ps5SimSource(path, max_ticks=int(a.seconds * 60))
        if src.step != 2:
            print(f"[eval] {Path(path).name}: not a 60 fps file, skipped")
            continue
        if interp is None or (interp.w, interp.h) != (src.w, src.h):
            interp = RifeInterpolator(src.w, src.h, "4.22.lite", 1.0, backend=a.engine)
        pipe = EvalPipeline(interp)
        pipe.run(src, None, score=True)
        name = Path(path).stem
        if pipe.frames == 0 or pipe.copies > 0.5 * pipe.frames:
            print(f"[eval] {name}: {pipe.copies} of {pipe.frames} in-between frames are copies (a 30 fps game?), "
                  "skipped")
            continue
        rows = pipe.rows()
        text = table(name, rows, f"  ({pipe.frames} frames; still {100 * pipe.counts[1] / pipe.counts[0]:.0f}%, "
                                 f"HUD-like {100 * pipe.counts[2] / pipe.counts[0]:.2f}% of pixels)")
        print(text, flush=True)
        per_clip.append(text)
        for k in VARIANTS:
            totals[k] += pipe.sums[k]
        counts += pipe.counts
    if not per_clip:
        print("[eval] nothing scored")
        return 1
    overall = rows_of(totals, counts)
    text = "\n\n".join(per_clip + [table("ALL CLIPS", overall)])
    print("\n" + table("ALL CLIPS", overall))
    (out / "table.txt").write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
