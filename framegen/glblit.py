"""Copying a texture's picture into a rectangle of the bound draw framebuffer, for the video window: with
glBlitFramebuffer, or with a shader where the graphics driver refuses that.

glBlitFramebuffer is the simplest copy, and on NVIDIA cards it's what the window has always used. An AMD driver
(RX 6750 XT, Windows 10) refused it with GL_INVALID_OPERATION on the first frame, which closed the window: a blit
into a multisampled screen, which anti-aliasing forced in a driver's settings can give, is always refused, for one.
So the first refusal switches the window to drawing the same rectangle as a textured triangle, for good, and says so
in the log. FRAMEGEN_BLIT=shader starts with the shader (to check that path on any card).
"""
from __future__ import annotations

import os

FORCE_SHADER = os.environ.get("FRAMEGEN_BLIT", "") == "shader"

_VERT = """#version 450
uniform vec4 corners;  // texture coordinates at the viewport's bottom-left (xy) and top-right (zw) corners
out vec2 uv;
void main() {  // one triangle covering the viewport
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = corners.xy + (corners.zw - corners.xy) * p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

_FRAG = """#version 450
layout(binding = 0) uniform sampler2D src;
in vec2 uv;
out vec4 color;
void main() { color = texture(src, uv); }
"""


def corners(tw: int, th: int, src: tuple, dst: tuple) -> tuple[tuple, tuple]:
    """The shader's draw for glBlitFramebuffer(*src, *dst) from a tw x th texture: (viewport x, y, w, h) and the
    texture coordinates at the viewport's bottom-left and top-right corners (u0, v0, u1, v1). As with a blit, the
    source corner (sx0, sy0) lands on the destination corner (dx0, dy0), so a destination with dy0 > dy1 is flipped."""
    sx0, sy0, sx1, sy1 = src
    dx0, dy0, dx1, dy1 = dst
    ux0, ux1 = (sx0, sx1) if dx0 <= dx1 else (sx1, sx0)
    vy0, vy1 = (sy0, sy1) if dy0 <= dy1 else (sy1, sy0)
    view = (min(dx0, dx1), min(dy0, dy1), abs(dx1 - dx0), abs(dy1 - dy0))
    return view, (ux0 / tw, vy0 / th, ux1 / tw, vy1 / th)


class Blitter:
    """One per GL context (the window's)."""

    def __init__(self, GL) -> None:
        self.GL = GL
        self.blit_ok = not FORCE_SHADER
        self.prog = self.vao = self.u_corners = None
        self.samplers: dict[bool, int] = {}

    def copy(self, tex: int, tw: int, th: int, fbo: int, src: tuple, dst: tuple, linear: bool) -> None:
        """Copy src (x0, y0, x1, y1) of texture `tex` (tw x th, attached to framebuffer `fbo`) to dst in the bound
        draw framebuffer, as glBlitFramebuffer(*src, *dst, GL_COLOR_BUFFER_BIT, LINEAR or NEAREST) would. Leaves
        `fbo` bound for reading."""
        GL = self.GL
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, fbo)
        if self.blit_ok:
            from OpenGL.error import GLError

            try:
                GL.glBlitFramebuffer(*src, *dst, GL.GL_COLOR_BUFFER_BIT, GL.GL_LINEAR if linear else GL.GL_NEAREST)
                return
            except GLError as e:
                self.blit_ok = False
                print(f"[display] the graphics driver refused glBlitFramebuffer (GL error {getattr(e, 'err', '?')}): "
                      "drawing the picture with a shader instead", flush=True)
        self._draw(tex, tw, th, src, dst, linear)

    def _setup(self) -> None:
        from OpenGL.GL import shaders

        GL = self.GL
        self.prog = shaders.compileProgram(shaders.compileShader(_VERT, GL.GL_VERTEX_SHADER),
                                           shaders.compileShader(_FRAG, GL.GL_FRAGMENT_SHADER), validate=False)
        self.u_corners = GL.glGetUniformLocation(self.prog, "corners")
        self.vao = GL.glGenVertexArrays(1)
        for linear in (False, True):  # sampler objects, so the textures' own filter settings stay as they are
            s = GL.glGenSamplers(1)
            f = GL.GL_LINEAR if linear else GL.GL_NEAREST
            for p, v in ((GL.GL_TEXTURE_MIN_FILTER, f), (GL.GL_TEXTURE_MAG_FILTER, f),
                         (GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE), (GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)):
                GL.glSamplerParameteri(s, p, v)
            self.samplers[linear] = s

    def _draw(self, tex: int, tw: int, th: int, src: tuple, dst: tuple, linear: bool) -> None:
        GL = self.GL
        if self.prog is None:
            self._setup()
        view, uv = corners(tw, th, src, dst)
        if view[2] <= 0 or view[3] <= 0:
            return
        old_view = GL.glGetIntegerv(GL.GL_VIEWPORT)
        blend = GL.glIsEnabled(GL.GL_BLEND)
        if blend:
            GL.glDisable(GL.GL_BLEND)  # a blit ignores blending
        GL.glViewport(*view)
        GL.glUseProgram(self.prog)
        GL.glUniform4f(self.u_corners, *uv)
        GL.glBindVertexArray(self.vao)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glBindSampler(0, self.samplers[linear])
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindSampler(0, 0)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        GL.glViewport(*(int(v) for v in old_view))
        if blend:
            GL.glEnable(GL.GL_BLEND)

    def close(self) -> None:
        GL = self.GL
        if self.prog is not None:
            GL.glDeleteProgram(self.prog)
            GL.glDeleteVertexArrays(1, [self.vao])
            GL.glDeleteSamplers(len(self.samplers), list(self.samplers.values()))
            self.prog = None
