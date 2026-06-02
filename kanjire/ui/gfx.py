"""Immediate-mode drawing helpers used by scene backgrounds."""
from __future__ import annotations

import pyglet
from pyglet.gl import GL_QUADS

from kanjire.ui.theme import Color


def gradient_quad(
    x: float, y: float, w: float, h: float, bottom: Color, top: Color
) -> None:
    """Draw a vertical-gradient rectangle (immediate mode)."""
    pyglet.graphics.draw(
        4,
        GL_QUADS,
        ("v2f", (x, y, x + w, y, x + w, y + h, x, y + h)),
        ("c3B", (*bottom, *bottom, *top, *top)),
    )


def fill_quad(x: float, y: float, w: float, h: float, color: Color) -> None:
    pyglet.graphics.draw(
        4,
        GL_QUADS,
        ("v2f", (x, y, x + w, y, x + w, y + h, x, y + h)),
        ("c3B", (*color, *color, *color, *color)),
    )
