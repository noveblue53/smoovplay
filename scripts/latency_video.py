"""Delay from a slow-motion phone video: button press to screen, or one screen against another.

    .venv\\Scripts\\python scripts\\latency_video.py press clip.mov
    .venv\\Scripts\\python scripts\\latency_video.py side  clip.mov

Film in 240 fps slow motion (each camera frame is about 4.2 ms). Use the phone's original file: an edited or
exported slow-motion clip can be retimed. If the file says 30 fps but was filmed at 240, add --fps 240.

press: film the controller and the screen together. Rest your thumb on the button and press it from there, so the
  only movement in the button's box is the press. Use something that reacts at once and the same way each time, on
  a still part of the picture, such as a flashlight toggled against a wall. For a 30 fps game use a response
  drawn during 30 fps gameplay. Console overlays and some game menus run at 60 fps. Space presses at least a
  second apart. For a tail estimate, aim for 100 matched
  presses per setup. The report gives each matched delay, the median and p95. For Xbox Remote Play, film the
  same action with the Xbox app alone, then SmoovPlay with frame generation off and on.
side: film two screens showing the same game: the PS5's own HDMI output and SmoovPlay (picture-by-picture on one
  monitor is best, as both halves then go through the same screen). Any motion works. Reported: how far behind
  the second box is, which is what Remote Play and SmoovPlay add to the picture. With frame generation on, the
  in-between frames show each change in steps, so this is the delay to about the middle of those steps.

The boxes are drawn with the mouse on the first frame (Enter or Space to confirm each), or given as x,y,w,h in
pixels: --button and --screen for press, --a (the PS5's own output) and --b (SmoovPlay) for side. A picture of
the first 20 matched presses (button at onset, screen before and at reaction) is saved next to the video.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

QUIET_FRAMES = 6  # an event starts after at least this many quiet camera frames
MAX_DELAY_S = 0.6  # a screen change more than this long after a press isn't that press's
MAX_LAG_S = 0.3  # side: the longest lag looked for
SHEET_ROWS = 20


def read_boxes(path: str, boxes: list[tuple[int, int, int, int]], every: int = 1, *,
               changes_only: bool = False) -> tuple[list[np.ndarray], float]:
    """Grey pixels per box for side mode, or just the mean absolute changes per box for press mode, plus fps."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        sys.exit(f"can't open {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    out = [[] for _ in boxes]
    previous = [None] * len(boxes)
    i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i % every == 0:
                for k, (x, y, w, h) in enumerate(boxes):
                    crop = frame[y:y + h, x:x + w]
                    if not crop.size:
                        sys.exit(f"box {(x, y, w, h)} is outside the {frame.shape[1]}x{frame.shape[0]} video")
                    grey = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
                    if changes_only:
                        delta = 0.0 if previous[k] is None else cv2.norm(grey, previous[k], cv2.NORM_L1) / grey.size
                        out[k].append(delta)
                        previous[k] = grey
                    else:
                        out[k].append(grey.astype(np.float32))
            i += 1
    finally:
        cap.release()
    if not i:
        sys.exit(f"no frames in {path}")
    return [np.asarray(o) for o in out], fps


def onsets(signal: np.ndarray, quiet: int = QUIET_FRAMES, k: float = 8.0) -> list[int]:
    """Frames where a change starts: above the noise (median + k x MAD of the whole clip, at least 1.5 levels)
    after `quiet` frames below it."""
    med = float(np.median(signal))
    mad = float(np.median(np.abs(signal - med))) or 0.1
    thr = max(med + k * 1.4826 * mad, med + 1.5)
    out, calm = [], quiet
    for i, v in enumerate(signal):
        if v > thr:
            if calm >= quiet:
                out.append(i)
            calm = 0
        else:
            calm += 1
    return out


def presses_of(starts: list[int], fps: float) -> list[int]:
    """Button movements that start a press: letting go (and any wobble) within MAX_DELAY_S of a press is part of it."""
    out: list[int] = []
    for s in starts:
        if not out or (s - out[-1]) / fps > MAX_DELAY_S:
            out.append(s)
    return out


def pair(presses: list[int], changes: list[int], fps: float) -> list[tuple[int, int]]:
    """Each press with the first screen change after it (within MAX_DELAY_S, and before the next press)."""
    out = []
    for n, p in enumerate(presses):
        nxt = presses[n + 1] if n + 1 < len(presses) else None
        c = next((c for c in changes if c > p and (nxt is None or c <= nxt) and (c - p) / fps <= MAX_DELAY_S), None)
        if c is not None:
            out.append((p, c))
    return out


def lag(a: np.ndarray, b: np.ndarray, fps: float) -> tuple[float, float]:
    """How many frames b runs behind a (both showing the same picture), to a fraction of a frame, and how well they
    match there (correlation, 0..1). Each box is shrunk to 16x9 blocks; each block's brightness over time is
    compared, frame-to-frame changes rather than levels, so the screens' own brightness and contrast don't matter."""
    def feat(x):
        small = np.stack([cv2.resize(f, (16, 9), interpolation=cv2.INTER_AREA) for f in x]).reshape(len(x), -1)
        d = np.diff(small, axis=0)
        return (d - d.mean(0)) / (d.std(0) + 1e-6)
    fa, fb = feat(a), feat(b)
    n = min(len(fa), len(fb))
    max_lag = int(MAX_LAG_S * fps)
    scores = []
    for L in range(-max_lag, max_lag + 1):
        x, y = (fa[: n - L], fb[L:n]) if L >= 0 else (fa[-L:n], fb[: n + L])
        scores.append(float((x * y).mean()) if len(x) > fps else -1.0)
    i = int(np.argmax(scores))
    best = float(i - max_lag)
    if 0 < i < len(scores) - 1:  # a parabola through the peak and its neighbours
        y0, y1, y2 = scores[i - 1], scores[i], scores[i + 1]
        den = y0 - 2 * y1 + y2
        if den < 0:
            best += 0.5 * (y0 - y2) / den
    return best, scores[i]


def pick_box(path: str, what: str) -> tuple[int, int, int, int]:
    cap = cv2.VideoCapture(path)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit(f"can't read {path}")
    h, w = frame.shape[:2]
    s = min(1.0, 1600 / w, 900 / h)
    title = f"Draw a box around {what}, then Enter"
    r = cv2.selectROI(title, cv2.resize(frame, (int(w * s), int(h * s))), showCrosshair=False)
    cv2.destroyWindow(title)
    if not r[2] or not r[3]:
        sys.exit("no box drawn")
    return tuple(int(round(v / s)) for v in r)


def parse_box(text: str | None):
    return tuple(int(v) for v in text.split(",")) if text else None


def sheet(path: str, boxes, pairs, out: Path) -> int:
    """Draw just the requested thumbnails into a bounded sheet as the decoder reaches them."""
    pairs = pairs[:SHEET_ROWS]
    if not pairs:
        return 0
    tile_w, tile_h, pad = 320, 160, 4
    row_h, col_w = tile_h + 2 * pad, tile_w + 2 * pad
    canvas = np.zeros((len(pairs), row_h, 3 * col_w, 3), np.uint8)
    filled = np.zeros(len(pairs), np.uint8)
    wanted = {}
    for row, (press, change) in enumerate(pairs):
        for col, n in enumerate((press, change - 1, change)):
            wanted.setdefault(n, []).append((row, col))
    cap = cv2.VideoCapture(path)
    i = 0
    try:
        while wanted:
            ok, frame = cap.read()
            if not ok:
                break
            for row, col in wanted.pop(i, ()):
                x, y, w, h = boxes[0 if col == 0 else 1]
                crop = frame[y:y + h, x:x + w]
                h, w = crop.shape[:2]
                scale = min(tile_w / w, tile_h / h)
                dw, dh = max(1, round(w * scale)), max(1, round(h * scale))
                dx, dy = col * col_w + (col_w - dw) // 2, (row_h - dh) // 2
                canvas[row, dy:dy + dh, dx:dx + dw] = cv2.resize(crop, (dw, dh))
                del crop  # release its full camera frame before scanning for the next press
                filled[row] += 1
            i += 1
    finally:
        cap.release()
    rows = canvas[filled == 3]
    if len(rows) and cv2.imwrite(str(out), rows.reshape(-1, 3 * col_w, 3)):
        return len(rows)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("press", "side"))
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, help="the rate it was filmed at, if the file says otherwise")
    ap.add_argument("--button", help="press: the button's box, x,y,w,h in pixels")
    ap.add_argument("--screen", help="press: the box on screen where the reaction shows")
    ap.add_argument("--a", help="side: the PS5's own output (the earlier screen)")
    ap.add_argument("--b", help="side: SmoovPlay (the later screen)")
    ap.add_argument("--no-sheet", action="store_true", help="press: skip the contact sheet of the first 20 matches")
    a = ap.parse_args(argv)

    if a.mode == "press":
        boxes = [parse_box(a.button) or pick_box(a.video, "the button"),
                 parse_box(a.screen) or pick_box(a.video, "where the screen reacts")]
    else:
        boxes = [parse_box(a.a) or pick_box(a.video, "the PS5's own picture"),
                 parse_box(a.b) or pick_box(a.video, "SmoovPlay's picture")]
    crops, file_fps = read_boxes(a.video, boxes, changes_only=a.mode == "press")
    fps = a.fps or file_fps
    if not fps:
        sys.exit("the video doesn't say its frame rate: add --fps")
    if fps < 120:
        print(f"Warning: {fps:.0f} fps is too coarse for this ({1000 / fps:.0f} ms a frame). Film in 240 fps slow "
              "motion, and if it was, add --fps 240.")
    ms = 1000.0 / fps
    print(f"{len(crops[0])} frames at {fps:.0f} fps ({ms:.1f} ms each)")

    if a.mode == "press":
        presses, changes = presses_of(onsets(crops[0]), fps), onsets(crops[1])
        pairs = pair(presses, changes, fps)
        print(f"presses found: {len(presses)}, screen changes: {len(changes)}, matched: {len(pairs)}")
        if not pairs:
            print("No press was followed by a screen change. Check the boxes (a tight one around the button, one "
                  "on a still part of the screen where the reaction shows).")
            return 1
        delays = [(c - p) * ms for p, c in pairs]
        for n, ((p, c), d) in enumerate(zip(pairs, delays), 1):
            print(f"  press {n:2d}: frame {p} -> {c}: {d:5.1f} ms")
        median, p95 = np.percentile(delays, [50, 95])
        print(f"Button to screen: median {median:.0f} ms, p95 {p95:.0f} ms "
              f"(range {min(delays):.0f}-{max(delays):.0f} ms, {len(delays)} matched presses, "
              f"each reading is +/- {ms:.0f} ms)")
        if not a.no_sheet:
            out = Path(a.video).with_suffix(".presses.png")
            shown = sheet(a.video, boxes, pairs, out)
            if shown:
                print(f"First {shown} matched presses (button, screen before, screen as it changes): {out}")
    else:
        frames, match = lag(crops[0], crops[1], fps)
        print(f"SmoovPlay's picture is {frames * ms:.0f} ms behind the PS5's own ({frames:+.1f} frames; how well "
              f"the two match there: {match:.2f}, where 1 is perfect)")
        if match < 0.3:
            print("That match is weak: check that both boxes show the whole game picture, and film more motion.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
