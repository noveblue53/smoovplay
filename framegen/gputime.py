"""When the graphics card is done with each tick's frames, timed with CUDA events instead of waiting for it.

Generated frames go to the GL window as CUDA tensors, and everything that reads them (the CUDA-GL copy, RTX VSR,
Looks in clips, instant replay) runs on PyTorch's current stream, so the order on the stream keeps the picture
right without the CPU waiting. Without that wait, the CPU's clock only says when the work was queued. TickClock
puts an event at the start of each tick and one after each generated frame, and reads them once they're done (a
tick or two later), so the stats still say when the frames were ready.

The start event goes in just after the picture check, whose `.tolist()` waits for the stream anyway: the GPU is
idle there, so the CPU's time then and the event's time are the same moment. (Without such a wait the times are
a lower bound.) At most `depth` ticks wait to be read: one more waits for the oldest, which also keeps the GPU
from running more than `depth` ticks ahead of the CPU, whatever the display's own limits.
"""
from __future__ import annotations

import time
from collections import deque


def _cuda_event():
    import torch
    return torch.cuda.Event(enable_timing=True)


class _Tick:
    __slots__ = ("t0", "start", "marks")

    def __init__(self, t0: float, start) -> None:
        self.t0, self.start, self.marks = t0, start, []


class TickClock:
    """on: the generated frames stay on the GPU (no wait for them). Off, each tick's stats are given at once, with
    the CPU's times as they are (the CPU path's download waits for the GPU already).

    Per tick: begin() where the stream is idle, mark() after each generated frame is queued, done(fn) at the end.
    fn(at) is called once the tick's GPU work is done, in tick order: at(t, k=None) is CPU time t, or the time mark k
    (default: the tick's last; -1: none, t as it is) was done if that's later. Event and clock are swappable for
    tests."""

    def __init__(self, on: bool, depth: int = 2, event=_cuda_event, now=time.perf_counter) -> None:
        self.on, self.depth = on, depth
        self._event, self._now = event, now
        self._open: _Tick | None = None  # the tick being made
        self._pending: deque = deque()  # (tick, fn), oldest first
        self.waits = 0  # times the CPU waited for a tick `depth` back (the GPU was that far behind)

    def begin(self) -> None:
        if not self.on:
            return
        ev = self._event()
        ev.record()
        self._open = _Tick(self._now(), ev)

    def mark(self) -> int | None:
        """After a generated frame is queued: its index in this tick (for at()), or None when off."""
        if self._open is None:
            return None
        ev = self._event()
        ev.record()
        self._open.marks.append(ev)
        return len(self._open.marks) - 1

    def last(self) -> int:
        """The newest mark of this tick so far, or -1 (no generated frame yet, or off)."""
        return len(self._open.marks) - 1 if self._open is not None else -1

    def done(self, fn) -> None:
        tick, self._open = self._open, None
        self._pending.append((tick, fn))
        self.poll()

    def poll(self, depth: int | None = None) -> None:
        """Gives the ticks whose GPU work is done; waits for the oldest while more than `depth` are left."""
        depth = self.depth if depth is None else depth
        while self._pending:
            tick, fn = self._pending[0]
            if tick is not None and tick.marks and not tick.marks[-1].query():
                if len(self._pending) <= depth:
                    return
                tick.marks[-1].synchronize()
                self.waits += 1
            self._pending.popleft()
            fn(self._at(tick))

    def flush(self) -> None:
        """Every tick's stats, waiting for the GPU (at the end of a session)."""
        self.poll(0)

    @staticmethod
    def _at(tick: _Tick | None):
        if tick is None or not tick.marks:
            return lambda t, k=None: t
        ready: dict = {}

        def at(t: float, k: int | None = None) -> float:
            k = len(tick.marks) - 1 if k is None else k
            if k < 0:
                return t
            if k not in ready:
                ready[k] = tick.t0 + tick.start.elapsed_time(tick.marks[k]) / 1000
            return max(t, ready[k])
        return at
