"""The delay test (L in the video window, PS5 Remote Play): chiaki-ng presses a button on the PS5 a few times, and
each time the pipeline times how long the answer takes to reach this PC, from the moment chiaki-ng sent the press
to the first frame that shows it. That's the controller's trip to the PS5, the game's own response, encoding and
the stream back. Add SmoovPlay's time (measured as it plays) and it's button to screen, all but the controller's
own link (a few ms) and the monitor.

It presses Options: in most games that opens the pause menu, a big change on screen that the game draws at its
own frame rate, so frame generation's delay is included. Best done standing still. (The PS button's control centre
is drawn at 60 fps over the game, so with frame generation on it would pass straight through.)

The picture is compared in 32x18 blocks of brightness: a press is answered when at least a quarter of the blocks
change by much more than the picture moved on its own in the second before the test.
"""
from __future__ import annotations

import time

import numpy as np
import torch
import torch.nn.functional as F

BUTTON_OPTIONS = 1 << 12  # ChiakiControllerButton
PRESSES = 6  # an even number, so a menu that opens and closes ends closed
HOLD_MS = 100
SETTLE_S = 1.0  # watch the picture this long first, to learn how much it moves on its own
SPAN = 10  # ...comparing frames this many ticks apart (about as long as an answer takes)
WAIT_S = 1.0  # the longest wait for an answer
SEND_S = 0.5  # the longest wait for chiaki-ng to send the press
GAP_S = 1.2  # from an answer (or giving up) to the next press
MIN_CHANGE = 6.0  # brightness levels: the smallest change that counts as an answer, however still the picture


BLOCKS = (18, 32)
SAMPLES = 15  # pixels looked at across each block (and down it): every 4th at 1080p, whose blocks are 60 px


def step(h: int, w: int) -> int:
    """How far apart small() reads pixels: 4 at 1080p, 8 at 4K, every pixel in a frame too small to spare any."""
    return max(1, min(h // (BLOCKS[0] * SAMPLES), w // (BLOCKS[1] * SAMPLES)))


def small(frame_u8: torch.Tensor) -> np.ndarray:
    """A frame (HxWx3 uint8 BGR, any device) as 18x32 blocks of brightness. Only every step()th pixel is read: with
    DirectML the frame is in the CPU's memory, where turning a whole 1080p frame into floats took 5 ms each tick,
    and a block's average comes out the same to within a brightness level."""
    s = step(*frame_u8.shape[:2])
    grey = frame_u8[::s, ::s].float() @ torch.tensor([0.114, 0.587, 0.299], device=frame_u8.device)
    return F.adaptive_avg_pool2d(grey[None, None], BLOCKS)[0, 0].cpu().numpy()


def change(a: np.ndarray, b: np.ndarray) -> float:
    """How much of the picture changed: the 75th percentile of the blocks' change, so at least a quarter of it."""
    return float(np.percentile(np.abs(a - b), 75))


class DelayTest:
    """press(buttons, hold_ms) -> token and press_sent(token) -> time or None are chiaki-ng's (ChiakiSource).
    Feed it every frame with feed(); it presses, watches and times by itself until `done`."""

    def __init__(self, press, press_sent, presses: int = PRESSES, button: int = BUTTON_OPTIONS,
                 now: float | None = None) -> None:
        self._press, self._press_sent = press, press_sent
        self.presses, self.button = presses, button
        self.state, self.t_state = "settle", time.perf_counter() if now is None else now
        self.recent: list[np.ndarray] = []  # the last few frames' blocks
        self.motion: list[float] = []
        self.threshold = MIN_CHANGE
        self.ms: list[float] = []  # each answered press: sent -> the first frame showing it arrived
        self.pressed = self.missed = 0
        self.error = ""
        self._ref: np.ndarray | None = None
        self._token = 0
        self._t_sent = 0.0

    @property
    def done(self) -> bool:
        return self.state == "done"

    def _to(self, state: str, now: float) -> None:
        self.state, self.t_state = state, now

    def feed(self, blocks: np.ndarray, t_arrival: float, now: float) -> None:
        """One frame (small()), when it reached this PC, and the time now."""
        self.recent = (self.recent + [blocks])[-SPAN - 1:]
        if self.state == "settle":
            if len(self.recent) > SPAN:
                self.motion.append(change(self.recent[0], blocks))
            if now - self.t_state >= SETTLE_S and self.motion:
                self.threshold = max(MIN_CHANGE, 2.5 * float(np.percentile(self.motion, 95)))
                self._to("gap", now - GAP_S)
        if self.state == "gap" and now - self.t_state >= GAP_S:
            if self.pressed >= self.presses:
                self._to("done", now)
                return
            self._ref, self.pressed = blocks, self.pressed + 1
            self._token = self._press(self.button, HOLD_MS)
            self._to("sending", now)
        if self.state == "sending":
            sent = self._press_sent(self._token)
            if sent is not None:
                self._t_sent = sent
                self._to("waiting", now)
            elif now - self.t_state > SEND_S:
                self.error = "chiaki-ng didn't send the press"
                self._to("done", now)
                return
        if self.state == "waiting":
            if t_arrival > self._t_sent and change(self._ref, blocks) > self.threshold:
                self.ms.append((t_arrival - self._t_sent) * 1000)
                self._to("gap", now)
            elif now - self._t_sent > WAIT_S:
                self.missed += 1
                if self.missed >= 2 and not self.ms:
                    self.error = "nothing changed on screen after pressing Options"
                    self._to("done", now)
                    return
                self._to("gap", now)

    def status(self) -> str:
        """The overlay's line while it runs."""
        if self.state == "settle":
            return "DELAY TEST: stand still; it presses Options a few times (a menu opening is what it times)"
        return f"DELAY TEST: press {self.pressed} of {self.presses}" + (
            f"  PS5 answered in {np.median(self.ms):.0f} ms" if self.ms else "")

    def result(self, smoovplay_ms: float | None) -> str:
        """The answer, in a line: the PS5 and Remote Play's part, SmoovPlay's, and the two together."""
        if self.error and not self.ms:
            return (f"Delay test: {self.error}. Try where Options opens a menu, standing still"
                    if "nothing changed" in self.error else
                    f"Delay test: {self.error}. Close chiaki-ng and open it from SmoovPlay")
        remote = float(np.median(self.ms))
        text = (f"Delay test: PS5 and Remote Play {remote:.0f} ms (button sent to the answer arriving, median of "
                f"{len(self.ms)})")
        if smoovplay_ms is not None:
            text += (f", SmoovPlay {smoovplay_ms:.0f} ms: {remote + smoovplay_ms:.0f} ms from button to "
                     "SmoovPlay's screen, plus your controller's link and your monitor")
        return text
