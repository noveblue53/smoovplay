"""Where frames come from: what each kind of source is like, and the pieces every source shares.

A source (framegen.io, framegen.chiaki_tap) yields Ticks: one frame for each refresh of the console's output,
new content or a repeat. It describes itself with `info` (a SourceInfo), so the pipeline and run.py read what it
is like instead of checking which class it is.

Shared pieces:
  TestMode       "Simulate a 30 fps game": a 60 fps game played as if it were 30 fps, with the frames it leaves
                 out kept to score the generated ones against. Works with any 60 Hz source.
  Clock60        puts a source whose frames come at uneven times (window capture, a Moonlight stream) onto a
                 steady 60 Hz beat, the way a console's HDMI output delivers them.
  make_detector  the repeat detector that suits a source.

`python -m framegen.sources --json` (or `run.py --list-sources --json`) lists the sources for the launcher. It
imports nothing heavy, so it answers in well under a second.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, replace

# Sources that work but aren't offered in the launcher: video files are for testing. SMOOVPLAY_SOURCES=all shows
# them anyway.
HIDDEN = {"file"}
# Offered as Experimental: built from the best information available, without the hardware to test every setup. The
# launcher marks them and offers Send feedback (a GitHub issue form).
EXPERIMENTAL = {"capture", "window", "tap"}


@dataclass(frozen=True)
class SourceInfo:
    kind: str  # "chiaki" (PS5 Remote Play), "capture" (capture card), "window" (another app's window, framegen/window.py),
    # "tap" (another app's decoded frames through the frame tap, framegen/tap.py), "file" (a recording as-is),
    # "sim" (simulated PS5)
    name: str  # shown to people
    live: bool = True  # frames come in real time; False: a file, read as fast as the pipeline takes them
    rate: str = "fixed"  # "fixed": a frame every refresh, repeats included (Remote Play, HDMI). "variable": frames
    # come when the sender chooses (window capture, Moonlight), so Clock60 puts them on a 60 Hz beat
    hz: float = 60.0  # refreshes per second, for a fixed rate
    repeats: str = "pixels"  # how a repeat is told from new content: "hint" (the source says, e.g. the frame tap's
    # flags; the picture check is only a fallback) or "pixels" (pictures compared: see make_detector)
    test_mode: bool = True  # "Simulate a 30 fps game" works with it (False: built in, as for "sim")
    full_range: bool = True  # frames come as full-range RGB (0-255); False: limited range (16-235), to be expanded
    audio: str = ""  # who plays the sound, in words ("" = nobody yet)
    controls: str = ""  # where the controller goes, in words


KINDS = {
    "chiaki": SourceInfo("chiaki", "PS5 Remote Play", audio="chiaki-ng plays the sound",
                         controls="Your controller connects to this PC; chiaki-ng sends it to the PS5"),
    "capture": SourceInfo("capture", "Capture card", audio="SmoovPlay plays the card's sound, in step with the picture",
                          controls="Your controller connects to the console as usual"),
    "window": SourceInfo("window", "App window", rate="variable", test_mode=False,
                         audio="The app plays its own sound, a little ahead of the picture",
                         controls="Your controller connects to the app as usual"),
    "tap": SourceInfo("tap", "Frame tap (for developers)", audio="The app plays its own sound",
                      controls="Your controller connects to the app as usual"),
    "file": SourceInfo("file", "Video file", live=False),
    "sim": SourceInfo("sim", "Video file as a PS5 30 fps game", live=False, test_mode=False),
}


def source_info(kind: str, **changes) -> SourceInfo:
    """A kind's description, with what only the running source knows (its frame rate, ...) filled in."""
    return replace(KINDS[kind], **changes)


# ----------------------------------------------------------------------------- test mode
class TestMode:
    """"Simulate a 30 fps game", for any 60 Hz source: frames 0, 2, 4... of a 60 fps game arrive as new content,
    and each odd frame is replaced by the one before it, so the stream looks like a 30 fps game (A A B B). The
    frames left out are kept, so the pipeline can score each generated frame against the real one it replaced:
    gt(content) is the frame left out at `content`.

    It's a filter, tick -> tick or None (drop it). A source that queues frames on a thread of its own (Remote Play,
    capture) applies it before the queue, so a frame dropped from a full queue never shifts which frames are kept;
    a file applies it as it reads. Switching it on or off (set_on) takes effect from the next frame.

    It needs a game running at a steady 60 fps. Each tick carries extra["game_new"], the source's own word on whether
    the game drew a new frame there (Remote Play's size check; None for a source that doesn't say), so the pipeline
    can tell when the game isn't at 60, and gt() won't offer a frame the game itself repeated.
    """

    __test__ = False  # not a pytest test class, despite the name

    KEEP = 8  # left-out frames kept for scoring; the pipeline asks within a frame or two

    def __init__(self, on: bool = False) -> None:
        self.on = on
        self._n = 0  # frames seen while on
        self._last = None  # the last kept frame
        self._truth: dict = {}
        self._game: dict = {}  # the source's repeat hint for each recent frame: True new, False repeat, None unknown

    def set_on(self, on: bool) -> None:
        if on and not self.on:
            self._last = None  # don't repeat a frame from before it was switched on
            self._truth.clear()
            self._game.clear()
        self.on = on

    def gt(self, content: int):
        """The real frame left out at `content`, or None. None too when the game repeated a frame there or at the
        kept frames either side (by the source's hint): below 60 fps the left-out frame isn't the real one halfway
        between them, so there's nothing fair to compare with."""
        if any(self._game.get(i) is False for i in (content - 1, content, content + 1)):
            return None
        return self._truth.get(content)

    def __call__(self, tick):
        if not self.on:
            return tick
        i = self._n
        self._n += 1
        # (Remote Play: its size check, which only says roughly; the pipeline's own call is the picture's)
        self._game[i] = tick.extra["game_new"] = tick.extra.get("size_new", tick.new_hint)
        self._game.pop(i - self.KEEP, None)
        if i % 2 == 0:  # kept: arrives as new content
            self._last = tick.frame
            tick.content, tick.truth_new, tick.new_hint = i, True, True
        elif self._last is not None:  # left out: the kept frame again, the real one kept as truth
            self._truth[i] = tick.frame
            self._truth.pop(i - self.KEEP, None)
            tick.frame = self._last
            tick.content, tick.truth_new, tick.new_hint = i - 1, False, False
        else:  # switched on at an odd frame: nothing to repeat yet
            return None
        return tick

    def wrap(self, ticks):
        from .io import Waiting
        for tick in ticks:
            if isinstance(tick, Waiting):  # (no frame yet: passed on, for the loop to keep the window answering)
                yield tick
            elif (tick := self(tick)) is not None:
                yield tick


# ----------------------------------------------------------------------------- 60 Hz clock
class WindowBeatPhase:
    """Fit a 60 Hz beat to the Xbox app's recent capture-ready times on a faster desktop.

    Windows may put adjacent app frames on either side of a scheduler beat (issue #40).
    Fit their phase from up to one second of past changes; the pictures themselves need
    no warm-up queue. Limit the movement of each output beat to 0.25 ms.
    """

    def __init__(self, period: float) -> None:
        self.period = period
        self._samples: deque = deque(maxlen=64)
        self._target: float | None = None

    def observe(self, ready: float) -> None:
        times = self._samples
        if times and ready - times[-1] > 0.5:
            times.clear()  # minimize, capture restart or a still picture
            self._target = None
        times.append(ready)
        if len(times) < 8:
            return
        # Estimate how many 60 Hz beats each distinct app picture spans. Usually one, but a
        # stream with bit-identical 30 fps repeats can produce only one capture every two.
        stride = max(1, round((ready - times[0]) / ((len(times) - 1) * self.period)))
        phases = [t - i * stride * self.period for i, t in enumerate(times)]
        latest = max(phases)
        if latest - min(phases) < 1.5 * self.period:
            # Aim just after the latest ordinary arrival on that beat. The small margin covers
            # a near-boundary arrival / timer uncertainty. A lost frame or a different source
            # cadence broadens the fit: keep the existing beat until the stream settles.
            self._target = latest + self.period / 12

    def next(self, beat: float) -> float:
        p = self.period
        if self._target is None or beat - self._samples[-1] > 0.5:
            return beat + p
        error = (self._target - beat + p / 2) % p - p / 2
        return beat + p + max(-0.00025, min(0.00025, error))


def _same_window_picture(frame, previous) -> bool:
    """Exact comparison on the CPU. Check a few pixels first to cheaply reject moving pictures.
    Confirm against every pixel before dropping a capture: HUD strokes may be a single pixel."""
    import numpy as np
    return (previous is not None and np.array_equal(frame[::16, ::16], previous[::16, ::16])
            and np.array_equal(frame, previous))


class Clock60:
    """Puts a variable-rate source on a steady beat (60 Hz by default), the way a console's HDMI output delivers
    frames. Usually each beat takes the newest frame, repeating the last picture when nothing arrived.
    For a high-refresh Xbox window, follow the app's phase and keep at most two arrivals. When two
    compete for a beat, show the older only if it has waited at most one period plus 2 ms; otherwise
    go straight to the live picture. The reader never blocks the beat waiting for a missing frame.

    Each beat's tick: a fresh frame keeps the source's own repeat hint (usually None: the pipeline compares
    pictures); a beat with no fresh frame is a repeat for certain (new_hint False). t_arrival stays when the frame
    really arrived (so latency includes the wait for the beat); t_ready is when the beat handed it on, the first
    moment the pipeline could have it. Frames overtaken by a newer one before their beat are counted in
    `overtaken`.
    """

    def __init__(self, source, hz: float = 60.0, clock=time.perf_counter, sleep=time.sleep) -> None:
        self.source, self.period = source, 1.0 / hz
        self._clock, self._sleep = clock, sleep
        self._cond = threading.Condition()
        self._latest = None
        self._serial = 0  # frames received so far
        self._done = False
        self.overtaken = 0
        self.xbox_window = getattr(source, "xbox_window", False)
        self._monitor_hz = None
        self._phase = None
        self._spare = None
        self.desktop_copies = 0

    def __getattr__(self, name: str):  # w, h, info, close... come from the source
        return getattr(self.source, name)

    def _offer(self, tick) -> None:
        """Publish a capture under the condition lock. Keep memory bounded during slow processing."""
        if self.xbox_window:
            monitor_hz = self.source.info.hz
            if monitor_hz != self._monitor_hz:
                self._monitor_hz = monitor_hz
                self._phase = WindowBeatPhase(self.period) if monitor_hz > 1 / self.period else None
                if self._serial > self._taken:  # discard pictures queued on the old display mode
                    self.overtaken += 1 + int(self._spare is not None)
                    self._taken = self._serial
                    self._spare = None
            if self._phase is not None:
                self._phase.observe(tick.t_ready if tick.t_ready is not None else tick.t_arrival)
        if self._latest is not None and self._serial > self._taken:
            if self._phase is None or self._spare is not None:
                self.overtaken += 1
            self._spare = self._latest if self._phase is not None else None
        self._latest, self._serial = tick, self._serial + 1

    def _reader(self) -> None:
        from .io import Waiting
        previous = None
        try:
            for tick in self.source:
                if isinstance(tick, Waiting):  # the beat goes on by itself: it repeats the last frame
                    continue
                if self.xbox_window and self.source.path == "CPU":
                    # ddagrab wakes for changes anywhere on the monitor. A recapture can otherwise
                    # replace the very same app frame with a later timestamp, or contaminate the
                    # detector's learned band for decoded repeats. CUDA checks DXGI dirty rectangles.
                    if _same_window_picture(tick.frame, previous):
                        self.desktop_copies += 1
                        continue
                    previous = tick.frame
                with self._cond:
                    self._offer(tick)
                    self._cond.notify_all()
        finally:
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def __iter__(self):
        from .io import Tick
        self._taken = 0  # the serial of the frame handed on last
        threading.Thread(target=self._reader, name="clock60", daemon=True).start()
        with self._cond:  # the first beat is the first frame
            self._cond.wait_for(lambda: self._latest is not None or self._done)
        beat, index = self._clock(), 0
        while True:
            with self._cond:
                latest, serial, done = self._latest, self._serial, self._done
                fresh = serial != self._taken
                if self._spare is not None:
                    now = self._clock()
                    ready = self._spare.t_ready if self._spare.t_ready is not None else self._spare.t_arrival
                    if now - ready <= self.period + 0.002:
                        latest, serial = self._spare, serial - 1
                    else:
                        self.overtaken += 1
                    self._spare = None
                self._taken = serial
                beat = self._phase.next(beat) if self._phase is not None else beat + self.period
            if latest is None or (done and not fresh):
                return
            handed = self._clock()  # the beat, or a moment after it if the sleep overshot
            yield Tick(latest.frame, latest.t_arrival if fresh else handed, index, latest.content,
                       latest.truth_new if fresh else None,
                       new_hint=latest.new_hint if fresh else False, damaged=latest.damaged and fresh,
                       t_ready=handed, t_source=latest.t_source, extra=dict(latest.extra) if fresh else {})
            index += 1
            now = self._clock()
            if beat < now - self.period:  # fell a whole beat behind (the pipeline stalled): restart the beat from now
                beat = now
            elif beat > now:
                self._sleep(beat - now)


# ----------------------------------------------------------------------------- repeat detector
def make_detector(info: SourceInfo, threshold: float = 2.0):
    """The repeat detector for a source. Files compare each frame with the previous one; a capture card compares with
    the last *new* frame, so a slow change that stays under the threshold from frame to frame still adds up and is
    caught; PS5 Remote Play, an app's window, or an app's decoded frames through the frame tap learn where the line
    between their repeats and new frames is (a decoded stream's repeats are never quite identical, but change far
    less than new frames)."""
    from .cadence import DuplicateDetector
    return DuplicateDetector(threshold, reference="new" if info.kind == "capture" else "previous",
                             learn=info.kind in ("chiaki", "window", "tap"))


# ----------------------------------------------------------------------------- the list for the launcher
GUIDES = {
    "chiaki": "Connect your PS5 with chiaki-ng (SmoovPlay sets it up), then press Play.",
    "capture": "Connect the console's HDMI to the capture card and the card to this PC. On a PS5, turn off HDCP "
               "while you use it (Settings > System > HDMI). Set the console to 1080p, 60 Hz, HDR off (or the Capture card "
               "resolution picked in Settings).",
    "window": "Open the app you stream with (PS Remote Play, the Xbox app, Moonlight, or a browser) and start the "
              "game, pick its window, then press Play. Keep the app's window on screen: SmoovPlay shows the smooth "
              "picture on top of it, and the app keeps the controller and the keyboard. SmoovPlay's own keys use Ctrl+Alt "
              "(Ctrl+Alt+M menu, Ctrl+Alt+F fullscreen, Ctrl+Alt+Esc quit), and can be changed on the Play page.",
    "tap": "For app developers: an app built to send its frames straight to SmoovPlay (docs/frame-tap.md in "
           "SmoovPlay's repo). For the Xbox app, PS Remote Play, Moonlight or a browser, pick App window instead. "
           "Press Play, then start the app: its smooth video opens in its own window, and the app keeps the "
           "controller and its sound.",
    "file": "For testing: a video file played as a source.",
}


def describe(kind: str) -> dict:
    """One source for the launcher: its info, whether it's offered, and a line on setting it up."""
    return {**asdict(KINDS[kind]), "hidden": kind in HIDDEN, "experimental": kind in EXPERIMENTAL,
            "guide": GUIDES[kind]}


def list_sources(show_all: bool = False) -> list[dict]:
    out = []
    for kind in ("chiaki", "capture", "window", "tap", "file"):
        d = describe(kind)
        if show_all:
            d["hidden"] = False
        out.append(d)
    return out


def main(argv: list[str] | None = None) -> int:
    import os
    argv = sys.argv[1:] if argv is None else argv
    show_all = os.environ.get("SMOOVPLAY_SOURCES", "").lower() == "all" or "--all" in argv
    sources = list_sources(show_all)
    if "--json" in argv:
        print(json.dumps({"sources": sources}))
    else:
        for s in sources:
            print(f"{s['kind']:8s} {s['name']}{'  (hidden)' if s['hidden'] else ''}\n         {s['guide']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
