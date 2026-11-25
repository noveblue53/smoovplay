"""framegen/glblit.py: the video window's plain copies to the screen fall back to a shader where the graphics
driver refuses glBlitFramebuffer (an AMD driver did, on the first frame). No graphics card needed here; the
check against a real blit is in test_glblit_gl.py."""
import re
from pathlib import Path

import pytest

from framegen import glblit
from framegen.glblit import Blitter, corners

GLError = pytest.importorskip("OpenGL.error").GLError


def test_corners_flipped_destination_maps_the_image_top_to_the_screen_top():
    # the window's copy: image row 0 (the top) to GL's top row, which is the larger y
    view, uv = corners(1920, 1080, (0, 0, 1920, 1080), (0, 900, 1600, 0))
    assert view == (0, 0, 1600, 900)
    assert uv == (0.0, 1.0, 1.0, 0.0)  # bottom-left of the viewport samples v = 1 (image row 1079, the bottom)


def test_corners_upright_and_offset():
    view, uv = corners(200, 100, (0, 0, 200, 100), (40, 10, 240, 110))
    assert view == (40, 10, 200, 100) and uv == (0.0, 0.0, 1.0, 1.0)
    view, uv = corners(200, 100, (50, 0, 150, 100), (0, 0, 100, 100))  # part of the source
    assert uv == (0.25, 0.0, 0.75, 1.0)


class _GL:
    GL_READ_FRAMEBUFFER, GL_COLOR_BUFFER_BIT, GL_LINEAR, GL_NEAREST = 1, 2, 3, 4

    def __init__(self, refuse: bool) -> None:
        self.refuse, self.blits, self.bound = refuse, 0, []

    def glBindFramebuffer(self, target, fbo):
        self.bound.append(fbo)

    def glBlitFramebuffer(self, *args):
        self.blits += 1
        if self.refuse:
            raise GLError(err=1282, baseOperation="glBlitFramebuffer")


def _blitter(gl, monkeypatch, force=False):
    monkeypatch.setattr(glblit, "FORCE_SHADER", force)
    b = Blitter(gl)
    drawn = []
    monkeypatch.setattr(b, "_draw", lambda *a: drawn.append(a))
    return b, drawn


def test_a_blit_that_works_is_used(monkeypatch):
    gl = _GL(refuse=False)
    b, drawn = _blitter(gl, monkeypatch)
    b.copy(7, 1920, 1080, 9, (0, 0, 1920, 1080), (0, 900, 1600, 0), True)
    assert gl.blits == 1 and not drawn and gl.bound == [9]


def test_a_refused_blit_switches_to_the_shader_for_good(monkeypatch, capsys):
    gl = _GL(refuse=True)
    b, drawn = _blitter(gl, monkeypatch)
    for _ in range(3):
        b.copy(7, 1920, 1080, 9, (0, 0, 1920, 1080), (0, 900, 1600, 0), True)
    assert gl.blits == 1  # tried once, not on every frame
    assert drawn == [(7, 1920, 1080, (0, 0, 1920, 1080), (0, 900, 1600, 0), True)] * 3
    out = capsys.readouterr().out
    assert out.count("refused glBlitFramebuffer") == 1 and "1282" in out


def test_framegen_blit_shader_starts_with_the_shader(monkeypatch):
    gl = _GL(refuse=False)
    b, drawn = _blitter(gl, monkeypatch, force=True)
    b.copy(7, 64, 64, 9, (0, 0, 64, 64), (0, 0, 64, 64), False)
    assert gl.blits == 0 and len(drawn) == 1


def test_the_video_window_copies_only_through_the_blitter():
    src = (Path(__file__).resolve().parent.parent / "framegen" / "io.py").read_text(encoding="utf-8")
    assert not re.search(r"GL\.glBlitFramebuffer\(", src)
