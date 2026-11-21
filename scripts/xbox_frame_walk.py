"""Trace a decoded pan pair through Clock60, CPU RIFE 4.22 Lite and the real OpenGL sink.

Requires a desktop with OpenGL 4.5 and the 1440p fixture from xbox_picture_ab.py.
The harness supplies the next capture after the previous beat completes so slow CPU
inference cannot skip the inputs. Use this for provenance and geometry only.
"""
import argparse
import queue
import sys
import threading
import time
from pathlib import Path

import av
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from framegen.interpolator import RifeInterpolator
from framegen.io import GLWindowSink, Tick
from framegen.pipeline import Pipeline
from framegen.settings import Settings
from framegen.sources import Clock60, make_detector, source_info
from framegen.streamcheck import VideoPayloadRate
from framegen.window import Rect
from framegen.window_trace import WindowTrace
from xbox_picture_ab import LOW, SIZE, process_window


class Pair:
    """Stand in for the Windows capture reader with two decoded fixture images."""
    info = source_info("window", hz=60)
    xbox_window = True
    path = "CPU"

    def __init__(self, folder, size):
        self.frames = [np.asarray(Image.open(folder / f"decoded-{i}.png").convert("RGB")) for i in (3, 4)]
        self.size = size
        self.requests = queue.Queue()

    def send(self, i):
        published = threading.Event()
        self.requests.put((i, published))
        return published

    def __iter__(self):
        for i, published in iter(self.requests.get, None):
            acquired = time.perf_counter()
            bgr = np.ascontiguousarray(process_window(self.frames[i], self.size)[:, :, ::-1])
            ready = time.perf_counter()
            yield Tick(bgr, acquired, i, t_ready=ready, t_source=(3+i)/30,
                       extra=dict(window_seq=3+i, window_ready=ready, window_backend="decoded fixture (CPU)"))
            published.set()

    def beats(self):
        clock = iter(Clock60(self))
        self.send(0)
        try:
            yield next(clock)  # A, then a beat with no new picture
            yield next(clock)
            if not self.send(1).wait(timeout=15):
                raise RuntimeError("timed out preparing frame B")
            yield next(clock)  # midpoint A/B, then B
            yield next(clock)
        finally:
            self.requests.put(None)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("fixture", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--processing", choices=("1080p", "native"), default="1080p")
    a = ap.parse_args()
    torch.set_num_threads(2)
    size = LOW if a.processing == "1080p" else SIZE
    payload = VideoPayloadRate()
    t = 0.0
    with av.open(a.fixture / "source.h264") as video:
        for i, p in enumerate(packet for packet in video.demux(video=0) if packet.size):
            payload.add(1, i, t, p.size, False)
            t += float(p.duration * p.time_base)
    print("Fixture counter replay (synthetic arrivals):", payload.report())

    pair = Pair(a.fixture, size)
    model = RifeInterpolator(*size, version="4.22.lite", device="cpu", scale=1, fp16=True, cuda_graph=False)
    settings = Settings({"hz": 60, "present": "gl", "scaler": "fsr", "vsync": False, "overlay": False})
    sink = GLWindowSink(*size, settings)
    try:
        # Exercise the real fullscreen overlay layout without attaching to a Windows HWND.
        rect = Rect(0, 0, *SIZE)
        sink.overlay_of = lambda: (rect, rect)
        settings.override("fullscreen", True)
        sink._set_fullscreen(True)
        with a.output.open("w", newline="", encoding="utf-8") as file:
            sink.frame_trace = WindowTrace(file)
            pipeline = Pipeline(model, make_detector(pair.info))
            pipeline.run(pair.beats(), sink, settings=settings, trace=sink.frame_trace, live_native_60=True)
    finally:
        sink.close()


if __name__ == "__main__":
    main()
