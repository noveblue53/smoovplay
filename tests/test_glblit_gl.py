"""framegen/glblit.py's shader copy against a real glBlitFramebuffer, on this PC's graphics card (needs
OpenGL 4.5 and a hidden window: skipped without them, and left out of the CPU-only run like test_fill.py)."""
import numpy as np
import pytest

from framegen import glblit


@pytest.fixture(scope="module")
def GL():
    glfw = pytest.importorskip("glfw")
    from OpenGL import GL as gl

    if not glfw.init():
        pytest.skip("no OpenGL")
    glfw.window_hint(glfw.VISIBLE, glfw.FALSE)
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 4)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 5)
    glfw.window_hint(glfw.OPENGL_PROFILE, glfw.OPENGL_CORE_PROFILE)
    win = glfw.create_window(64, 64, "test", None, None)
    if not win:
        glfw.terminate()
        pytest.skip("no OpenGL 4.5 context")
    glfw.make_context_current(win)
    yield gl
    glfw.destroy_window(win)
    glfw.terminate()


def _texture(GL, img):
    h, w = img.shape[:2]
    tex = GL.glGenTextures(1)
    GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
    GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
    GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
    GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, img)
    fbo = GL.glGenFramebuffers(1)
    GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
    GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER, GL.GL_COLOR_ATTACHMENT0, GL.GL_TEXTURE_2D, tex, 0)
    return tex, fbo


def _copy(GL, shader, src_img, src, dst, out_w, out_h, linear):
    tex, fbo = _texture(GL, src_img)
    _, out_fbo = _texture(GL, np.zeros((out_h, out_w, 4), np.uint8))
    b = glblit.Blitter(GL)
    b.blit_ok = not shader
    GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, out_fbo)
    GL.glViewport(0, 0, out_w, out_h)
    b.copy(tex, src_img.shape[1], src_img.shape[0], fbo, src, dst, linear)
    GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, out_fbo)
    px = GL.glReadPixels(0, 0, out_w, out_h, GL.GL_RGBA, GL.GL_UNSIGNED_BYTE)
    vp = tuple(int(v) for v in GL.glGetIntegerv(GL.GL_VIEWPORT))
    b.close()
    return np.frombuffer(px, np.uint8).reshape(out_h, out_w, 4), vp


@pytest.mark.parametrize("dst", [(8, 40, 72, 8), (8, 8, 72, 40), (72, 8, 8, 40)])  # flipped, upright, mirrored
def test_shader_matches_a_nearest_blit_exactly(GL, dst):
    img = np.random.default_rng(1).integers(0, 256, (32, 64, 4), np.uint8)
    a, _ = _copy(GL, False, img, (0, 0, 64, 32), dst, 80, 48, False)
    b, vp = _copy(GL, True, img, (0, 0, 64, 32), dst, 80, 48, False)
    assert np.array_equal(a, b)
    assert vp == (0, 0, 80, 48)  # the caller's viewport is left as it was


def test_shader_matches_a_linear_downscale_closely(GL):
    # the window's plain copy of a 1080p stream into a 1600 x 900 window, scaled down to test size
    y, x = np.mgrid[0:108, 0:192]
    img = np.dstack([(x * 255 // 191), (y * 255 // 107), ((x + y) % 32) * 8, np.full_like(x, 255)]).astype(np.uint8)
    a, _ = _copy(GL, False, img, (0, 0, 192, 108), (0, 90, 160, 0), 160, 90, True)
    b, _ = _copy(GL, True, img, (0, 0, 192, 108), (0, 90, 160, 0), 160, 90, True)
    assert np.abs(a.astype(int) - b.astype(int)).max() <= 2
