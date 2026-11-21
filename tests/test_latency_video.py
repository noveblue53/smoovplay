"""scripts/latency_video.py on made-up slow-motion videos with known delays. CPU only."""
import importlib.util
import weakref
from pathlib import Path

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("latency_video", ROOT / "scripts" / "latency_video.py")
lv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lv)

FPS, W, H = 240, 320, 180
BUTTON, SCREEN = (20, 60, 40, 40), (160, 40, 120, 80)


def _write(path, frames):
    out = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (W, H))
    for f in frames:
        out.write(f)
    out.release()


def _press_video(path, presses, delay, seed=0):
    """A button (left) pressed at `presses` (thumb down over 3 frames, up 12 frames later) and a screen (right)
    whose menu opens or closes `delay` frames after each press; camera noise everywhere, and a busy bit of game
    outside the screen box."""
    rng = np.random.default_rng(seed)
    n = presses[-1] + delay + 60
    menu, frames = False, []
    for i in range(n):
        f = np.full((H, W, 3), 90, np.uint8)
        x, y, w, h = BUTTON
        depth = 0
        for p in presses:
            if p <= i < p + 3:
                depth = i - p + 1
            elif p + 3 <= i < p + 15:
                depth = 3
            elif p + 15 <= i < p + 18:
                depth = 3 - (i - p - 14)
        f[y + 8 * depth: y + h, x: x + w] = 200  # the thumb, lower as it presses
        if any(i == p + delay for p in presses):
            menu = not menu
        x, y, w, h = SCREEN
        f[y: y + h, x: x + w] = (40, 40, 40)
        if menu:
            f[y + 10: y + h - 10, x + 10: x + w - 10] = (230, 230, 230)
        f[150:175, (3 * i) % 280: (3 * i) % 280 + 30] = 255  # something moving elsewhere
        noise = rng.normal(0, 2, f.shape)
        frames.append(np.clip(f + noise, 0, 255).astype(np.uint8))
    _write(path, frames)


def test_press_mode_finds_each_press_and_its_delay(tmp_path, capsys):
    path = tmp_path / "press.avi"
    presses = [30, 300, 560, 830, 1100]
    _press_video(path, presses, delay=31)  # 31 frames at 240 fps: 129 ms
    box = lambda b: ",".join(map(str, b))  # noqa: E731
    assert lv.main(["press", str(path), "--button", box(BUTTON), "--screen", box(SCREEN)]) == 0
    out = capsys.readouterr().out
    assert "presses found: 5" in out and "matched: 5" in out
    assert "median 129 ms" in out
    assert (tmp_path / "press.presses.png").exists()


def test_side_mode_finds_the_lag_between_two_screens(tmp_path):
    rng = np.random.default_rng(1)
    texture = cv2.GaussianBlur(rng.integers(0, 255, (400, 1400), dtype=np.uint8), (0, 0), 4)
    lag_frames, n = 13, 900
    speed = np.cumsum(2 + 2 * np.sin(np.arange(n + lag_frames) / 37.0))  # an uneven pan, like a game camera
    def view(t, gain, offset):
        x = int(speed[t]) % 800
        return np.clip(texture[100:180, x: x + 140] * gain + offset, 0, 255).astype(np.uint8)
    frames = []
    for i in range(n):
        f = np.zeros((H, W, 3), np.uint8)
        f[20:100, 10:150] = view(i + lag_frames, 1.0, 0)[..., None]  # the PS5's own output, ahead
        f[20:100, 170:310] = view(i, 0.8, 30)[..., None]  # SmoovPlay: same picture, later, a different screen
        frames.append(f)
    path = tmp_path / "side.avi"
    _write(path, frames)
    crops, fps = lv.read_boxes(str(path), [(10, 20, 140, 80), (170, 20, 140, 80)])
    assert fps == pytest.approx(FPS)
    frames_behind, match = lv.lag(crops[0], crops[1], fps)
    assert frames_behind == pytest.approx(lag_frames, abs=0.5) and match > 0.5


def test_letting_go_of_the_button_isnt_a_new_press():
    starts = [10, 25, 26, 400, 412]
    assert lv.presses_of(starts, 240) == [10, 400]
    assert lv.pair([10, 400], [5, 40, 41, 300, 450], 240) == [(10, 40), (400, 450)]


def test_hundred_press_clip_reports_tail_and_limits_contact_sheet(tmp_path, monkeypatch, capsys):
    # A streamed 100-second camera fixture. Ninety presses take 24 frames, ten take 48.
    # A hundredth matched press is outside the contact sheet but must count toward p95.
    videos = []

    class Video:
        def __init__(self, path):
            self.n, self.live = 0, []
            videos.append(self)

        def isOpened(self):
            return True

        def get(self, prop):
            return FPS

        def read(self):
            if self.n == 100 * FPS:
                return False, None
            press, phase = divmod(self.n, FPS)
            frame = np.zeros((8, 16, 3), np.uint8)
            if 12 <= phase < 22:
                frame[:, :8] = 220
            delay = 24 if press < 90 else 48
            if 12 + delay <= phase < 12 + delay + 50:
                frame[:, 8:] = 180
            self.n += 1
            self.live = [ref for ref in self.live if ref() is not None]
            assert len(self.live) <= 1  # neither pass retains old decoded camera frames
            self.live.append(weakref.ref(frame))
            return True, frame

        def release(self):
            pass

    monkeypatch.setattr(lv.cv2, "VideoCapture", Video)
    path = tmp_path / "long.mov"
    assert lv.main(["press", str(path), "--button", "0,0,8,8", "--screen", "8,0,8,8"]) == 0
    out = capsys.readouterr().out
    assert "matched: 100" in out and "median 100 ms, p95 200 ms" in out
    assert "First 20 matched presses" in out
    measured, sheet = videos
    assert measured.n == 100 * FPS
    assert sheet.n == 19 * FPS + 12 + 24 + 1
