"""The video window keeps the keys: when a program takes them with no keyboard or mouse input from the player
(chiaki-ng's stream window, the launcher minimising itself, the desktop), GLWindowSink.settle gives them back; when
the player switches on purpose (input), it's left alone. Windows' calls are stand-ins: no window opens."""
import time
from types import SimpleNamespace

from framegen import io, winutil

OURS = 100


def _window(monkeypatch, fg, idle_ms):
    w = io.GLWindowSink.__new__(io.GLWindowSink)  # no GL, no window
    now = time.perf_counter()
    w.overlay_of, w.settle_hook = None, None
    w._settle_until, w._settle_next, w._any_until = now + 10, 0.0, now + 15
    w._front_at, w._took_from = now - 1.0, set()
    w.glfw, w.win = SimpleNamespace(get_win32_window=lambda win: OURS), object()
    state = SimpleNamespace(fg=fg, idle_ms=idle_ms, brought=0)

    def bring(hwnd, nudge=False):
        state.brought += 1
        state.fg = (OURS, "python.exe")
        return True
    monkeypatch.setattr(io.sys, "platform", "win32")
    monkeypatch.setattr(winutil, "foreground", lambda: state.fg)
    monkeypatch.setattr(winutil, "ms_since_input", lambda: state.idle_ms)
    monkeypatch.setattr(winutil, "bring_to_front", bring)
    return w, state


def _poll(w):
    w._settle_next = 0.0
    w.settle()


def test_keys_taken_without_input_come_back(monkeypatch, capsys):
    w, s = _window(monkeypatch, (200, "chiaki.exe"), idle_ms=60_000)  # nothing pressed for a minute
    _poll(w)
    assert s.brought == 1 and "chiaki.exe took the keys: gave them back" in capsys.readouterr().out
    _poll(w)  # in front again: nothing to do
    assert s.brought == 1
    s.fg = (300, "")  # no window in front (the window in front went away)
    _poll(w)
    assert s.brought == 2 and "no window took the keys" in capsys.readouterr().out


def test_switching_on_purpose_is_left_alone(monkeypatch):
    w, s = _window(monkeypatch, (200, "discord.exe"), idle_ms=50)  # Alt+Tab or a click, just now
    _poll(w)
    assert s.brought == 0
    s.fg = (200, "chiaki.exe")  # even to chiaki-ng
    _poll(w)
    assert s.brought == 0


def test_after_the_first_seconds_only_the_usual_takers(monkeypatch, capsys):
    w, s = _window(monkeypatch, (200, "steam.exe"), idle_ms=60_000)
    _poll(w)  # in the first seconds, any program
    assert s.brought == 1 and "another program took the keys" in capsys.readouterr().out
    w._any_until = time.perf_counter() - 1
    s.fg = (200, "steam.exe")
    _poll(w)
    assert s.brought == 1  # later, a program that isn't one of ours is left alone
    for exe in ("smoovplay.exe", "explorer.exe", "chiaki.exe"):
        s.fg = (200, exe)
        _poll(w)
    assert s.brought == 4


def test_chiaki_minimised_again_when_the_video_window_is_back_in_front(monkeypatch):
    """After the first seconds, settle_hook (run.py: minimise chiaki-ng) runs each time the video window gets the keys
    back (Alt+Tab to chiaki-ng's window restores it), not on every poll, and not while chiaki-ng is in front."""
    w, s = _window(monkeypatch, (OURS, "python.exe"), idle_ms=50)
    w._settle_until = time.perf_counter() - 1  # past the first seconds
    calls = []
    w.settle_hook = lambda: calls.append(1)
    _poll(w)
    assert calls == []  # in front all along: nothing to do
    s.fg = (200, "chiaki.exe")  # Alt+Tab to chiaki-ng: left alone while the player looks at it
    _poll(w)
    assert calls == []
    s.fg = (OURS, "python.exe")  # and back: chiaki-ng's window goes down again
    _poll(w)
    assert calls == [1]
    _poll(w)
    assert calls == [1]  # once per return, not four times a second


def test_a_window_that_opened_without_the_keys_takes_them_once_even_after_mouse_moves(monkeypatch, capsys):
    w, s = _window(monkeypatch, (200, "chiaki.exe"), idle_ms=50)  # the mouse moved just now
    w._had_front = False  # Windows let chiaki-ng keep the keys when the video window opened
    _poll(w)
    assert s.brought == 1 and "opened behind chiaki.exe: took the keys" in capsys.readouterr().out
    w._front_at = time.perf_counter() - 1.0
    s.fg = (200, "chiaki.exe")  # a second later the player went back to chiaki-ng on purpose: theirs to choose
    _poll(w)
    assert s.brought == 1


def test_fullscreen_is_a_borderless_window_so_alt_tab_works():
    calls = []

    class G:
        DECORATED, FALSE, TRUE = 1, 0, 1

        def __getattr__(self, name):
            out = {"get_window_pos": (80, 80), "get_window_size": (1600, 900), "get_monitor_pos": (0, 0),
                   "get_monitors": ["only"]}.get(
                name, SimpleNamespace(size=SimpleNamespace(width=2560, height=1440)))
            return lambda *a: calls.append(name) or out

    w = io.GLWindowSink.__new__(io.GLWindowSink)
    w.glfw, w.win, w.overlay_of, w.dx, w.fs_style, w._borderless = G(), object(), None, None, "none", False
    w.windowed_pos, w.settings = None, SimpleNamespace(vsync=True)
    w._set_fullscreen(True)
    assert w._borderless and "set_window_monitor" not in calls  # GLFW's fullscreen stays on top of everything
    w._set_fullscreen(False)
    assert not w._borderless
