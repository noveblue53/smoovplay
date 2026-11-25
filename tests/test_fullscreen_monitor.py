"""F (fullscreen) fills the monitor the video window is on, not always the primary one. No window or GL needed."""
from types import SimpleNamespace

from framegen.io import GLWindowSink, window_monitor

LEFT = ("left", 0, 0, 2560, 1440)  # the primary monitor, listed first as GLFW does
RIGHT = ("right", 2560, 200, 1920, 1080)


def test_the_monitor_with_most_of_the_window_on_it():
    assert window_monitor((100, 100, 1600, 900), [LEFT, RIGHT]) == "left"
    assert window_monitor((2700, 300, 1600, 900), [LEFT, RIGHT]) == "right"
    assert window_monitor((2000, 300, 1600, 900), [LEFT, RIGHT]) == "right"  # straddling: more of it on the right
    assert window_monitor((-5000, -5000, 800, 600), [LEFT, RIGHT]) == "left"  # on neither: the primary one


class _Glfw:
    """Stand-ins for the GLFW calls fullscreen makes, with two monitors and a window on the right-hand one."""
    TRUE, FALSE, DECORATED = 1, 0, 0x20005

    def __init__(self, at):
        self.at, self.placed = at, None

    def get_monitors(self):
        return [m[0] for m in (LEFT, RIGHT)]

    def get_primary_monitor(self):
        return LEFT[0]

    def get_monitor_pos(self, mon):
        m = LEFT if mon == LEFT[0] else RIGHT
        return m[1], m[2]

    def get_video_mode(self, mon):
        m = LEFT if mon == LEFT[0] else RIGHT
        return SimpleNamespace(size=SimpleNamespace(width=m[3], height=m[4]), refresh_rate=60)

    def get_window_pos(self, win):
        return self.at[:2]

    def get_window_size(self, win):
        return self.at[2:]

    def set_window_attrib(self, win, attrib, value):
        pass

    def set_window_pos(self, win, x, y):
        self.placed = (x, y, *(self.placed or (0, 0, 0, 0))[2:])

    def set_window_size(self, win, w, h):
        self.placed = (*(self.placed or (0, 0))[:2], w, h)


def test_borderless_fullscreen_covers_the_windows_own_monitor():
    sink = object.__new__(GLWindowSink)  # only what fullscreen reads, without opening a window
    sink.glfw, sink.win, sink._borderless, sink.windowed_pos = _Glfw((2700, 300, 1600, 900)), object(), False, None
    sink._set_borderless(True)
    assert sink.glfw.placed == (2560, 200, 1920, 1080)
    assert sink.windowed_pos == (2700, 300, 1600, 900)  # where F puts it back
